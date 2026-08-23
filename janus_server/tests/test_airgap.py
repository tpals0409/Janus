import socket

import pytest

from janus_server.airgap import AirgapViolation, assert_local_artifacts, local_network_only
from janus_server.workflow import (
    CheckpointStore,
    ExecutionLimits,
    Stage,
    WorkerExecutionError,
    WorkflowEngine,
)


def external_network_worker(_stage, _context):
    socket.getaddrinfo("example.com", 443)
    return {"unreachable": True}


def test_local_network_gate_blocks_dns_and_external_connects():
    with local_network_only() as events:
        with pytest.raises(AirgapViolation, match="DNS"):
            socket.getaddrinfo("example.com", 443)
        sock = socket.socket()
        try:
            with pytest.raises(AirgapViolation, match="destination"):
                sock.connect(("203.0.113.1", 443))
        finally:
            sock.close()
        udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            with pytest.raises(AirgapViolation, match="destination"):
                udp.sendto(b"blocked", ("203.0.113.1", 53))
        finally:
            udp.close()
    assert [event["kind"] for event in events] == ["blocked_dns", "blocked", "blocked"]


def test_every_isolated_worker_is_automatically_airgap_guarded(tmp_path):
    engine = WorkflowEngine(
        [Stage("external")], CheckpointStore(tmp_path / "checkpoint.json")
    )
    with pytest.raises(WorkerExecutionError, match="AirgapViolation"):
        engine.run_isolated(
            external_network_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        )


def test_durable_artifact_audit_rejects_remote_uris():
    assert_local_artifacts({"checkpoint": "/local/run/checkpoint.json", "output": "outputs/a.json"})
    with pytest.raises(AirgapViolation, match="remote artifact"):
        assert_local_artifacts({"output": "https://example.com/result.json"})
