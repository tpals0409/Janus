"""병렬 write 워커의 파일 소유권 임대 계약.

같은 worktree를 공유하는 워커가 write_file/edit_file(그리고 사실상의 쓰기 경로인
run_bash)을 가지려면 엔진이 배타적 파일 소유권 임대를 요구한다. 선언 없는 writer는
워크스페이스 전체(*)를 임대하며, 겹치는 임대는 스폰 단계에서 거부된다 — 같은 파일을
병렬로 쓸 수 있는 경로는 남지 않는다.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from janus_server import runtime
from janus_server.ownership import (
    FileOwnershipTable,
    OwnershipConflict,
    owns_path,
)
from janus_server.scheduler import ResourceScheduler
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient

PARENT_TOOLS = ["read_file", "glob", "grep", "write_file", "edit_file", "run_bash"]


def make_orchestration(fake: FakeClient, root: Path):
    context = WorkspaceContext(
        root=root, task_id="task_lease", workspace_id="workspace_lease",
    ).for_dispatch("dispatch_lease")
    spec = {
        "name": "lease", "model": "qwen3.8-27b", "tools": PARENT_TOOLS,
        "approval": "ask", "worker_policy": "autonomous",
        "allow_autonomous_workers": True,
    }
    with (
        patch.object(runtime, "resolve_local_model", lambda name: name),
        patch.object(runtime, "make_client", lambda: fake),
    ):
        orch = runtime.Orchestration(
            spec, send=lambda _event: None, approver=lambda *_args: True,
            workspace_context=context, scheduler=ResourceScheduler(),
        )
    orch.current_dispatch_id = "dispatch_lease"
    orch.current_user_text = "워커를 배치해서 진행해"
    orch.active_workspace_context = context
    return orch


def spawn(orch, **kwargs):
    return orch.create_worker["handler"](**kwargs)


def wait_worker(orch, worker_id: str, timeout: float = 5.0) -> dict:
    handler = next(tool["handler"] for tool in orch.worker_control_tools
                   if tool["name"] == "wait_worker")
    return handler(worker_id, timeout)


def test_root_partition_semantics_in_ownership_module():
    """\"*\"는 모든 경로를 소유하고 무엇과도 겹친다."""
    table = FileOwnershipTable()
    assert owns_path(["*"], "deeply/nested/file.txt")
    lease = table.acquire("writer-a", ["*"])
    with pytest.raises(OwnershipConflict):
        table.acquire("writer-b", ["src/"])
    with pytest.raises(OwnershipConflict):
        table.acquire("writer-c", ["README.md"])
    lease.release()
    table.acquire("writer-b", ["src/"]).release()

    holder = FileOwnershipTable()
    holder.acquire("a", ["src/"])
    with pytest.raises(OwnershipConflict):
        holder.acquire("b", ["*"])


def test_invalid_owned_paths_are_rejected_before_any_accounting():
    fake = FakeClient([])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        rejected = spawn(orch, name="bad", task="x", role="implementer",
                         tools=[], max_steps=2, owned_paths=["../escape"])
        assert rejected["reason"] == "invalid_write_partition"
        # 거부가 스폰 회계(seq·fingerprint·active_workers)를 오염하지 않는다.
        assert orch.worker_seq == 0
        assert orch.active_workers == 0
        assert orch.write_ownership.snapshot() == {}


def test_unowned_second_writer_conflicts_then_succeeds_after_release():
    release_first = threading.Event()
    first_running = threading.Event()

    def hold_until_released():
        first_running.set()
        release_first.wait(3)
        return {"text": "first done"}

    fake = FakeClient([hold_until_released, {"text": "third done"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        first = spawn(orch, name="writer-a", task="implement A",
                      role="implementer", tools=[], max_steps=2)
        assert first.get("created") is True
        assert first_running.wait(3)
        # 기본 임대는 워크스페이스 전체(*) — 두 번째 무선언 writer는 스폰 자체가 거부된다.
        second = spawn(orch, name="writer-b", task="implement B",
                       role="implementer", tools=[], max_steps=2)
        assert "created" not in second
        assert second["reason"] == "write_partition_conflict"
        assert list(second["held"].values()) == [["*"]]
        assert "Do not implement the work yourself" in second["result"]

        release_first.set()
        finished = wait_worker(orch, first["worker"])
        assert finished["finished"] is True

        # 해제 후에는 다시 spawn할 수 있고, 회계가 오염되지 않아 순번이 이어진다.
        third = spawn(orch, name="writer-c", task="implement C",
                      role="implementer", tools=[], max_steps=2)
        assert third.get("created") is True
        assert third["worker"].startswith("w2-")
        assert wait_worker(orch, third["worker"])["finished"] is True
        assert orch.write_ownership.snapshot() == {}


def test_disjoint_owned_paths_allow_parallel_write_workers():
    pair = threading.Barrier(2)
    missed = []

    def blocker(tag: str):
        def run() -> dict:
            try:
                pair.wait(5)
            except Exception:  # 상대가 임계 구간에 못 들어온 경우
                missed.append(tag)
            return {"text": f"{tag} done"}
        return run

    fake = FakeClient([blocker("A"), blocker("B")])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        left = spawn(orch, name="left", task="edit src/a",
                     role="implementer", tools=[], max_steps=2,
                     owned_paths=["src/a/"])
        right = spawn(orch, name="right", task="edit src/b",
                      role="implementer", tools=[], max_steps=2,
                      owned_paths=["src/b/report.py"])
        assert left.get("created") is True
        assert right.get("created") is True

        # Barrier가 두 생성 모두를 통과시켰다는 것은 두 writer가 실제로
        # 동시에 실행됐다는 뜻이다 — 겹치지 않는 파티션은 병렬 fan-out이 허용된다.
        assert wait_worker(orch, left["worker"])["finished"] is True
        assert wait_worker(orch, right["worker"])["finished"] is True
        assert missed == []
        assert orch.write_ownership.snapshot() == {}


def test_overlapping_declared_partition_is_rejected_while_disjoint_passes():
    release_first = threading.Event()
    first_running = threading.Event()

    def hold_until_released():
        first_running.set()
        release_first.wait(3)
        return {"text": "holder done"}

    fake = FakeClient([hold_until_released, {"text": "disjoint done"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        holder = spawn(orch, name="holder", task="refactor src/",
                       role="implementer", tools=[], max_steps=2,
                       owned_paths=["src/"])
        assert holder.get("created") is True
        assert first_running.wait(3)

        overlapping = spawn(orch, name="clash", task="edit src/shared.py",
                            role="implementer", tools=[], max_steps=2,
                            owned_paths=["src/shared.py"])
        assert "created" not in overlapping
        assert overlapping["reason"] == "write_partition_conflict"

        disjoint = spawn(orch, name="docs", task="write docs/x.md",
                         role="implementer", tools=[], max_steps=2,
                         owned_paths=["docs/x.md"])
        assert disjoint.get("created") is True

        release_first.set()
        assert wait_worker(orch, holder["worker"])["finished"] is True
        assert wait_worker(orch, disjoint["worker"])["finished"] is True
        assert orch.write_ownership.snapshot() == {}


def test_read_only_role_spawns_without_touching_the_write_leases():
    release_first = threading.Event()

    def hold_until_released():
        release_first.wait(3)
        return {"text": "writer done"}

    fake = FakeClient([hold_until_released, {"text": "scouted"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        writer = spawn(orch, name="coder", task="implement",
                       role="implementer", tools=[], max_steps=2)
        assert writer.get("created") is True

        # read-only 역할은 write 도구가 없어 임대 없이 병렬 스폰된다.
        scout = spawn(orch, name="eye", task="investigate",
                      role="scout", tools=[], max_steps=2)
        assert scout.get("created") is True
        record = orch.worker_records[scout["worker"]]
        assert record["owned_partitions"] == []
        assert record["write_lease"] is None

        release_first.set()
        assert wait_worker(orch, writer["worker"])["finished"] is True
        assert wait_worker(orch, scout["worker"])["finished"] is True


def test_completed_writer_view_exposes_partitions_and_frees_the_lease():
    fake = FakeClient([{"text": "done"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        created = spawn(orch, name="solo", task="edit src/a/one.py",
                        role="implementer", tools=[], max_steps=2,
                        owned_paths=["src/a/one.py"])
        assert created.get("created") is True
        result = wait_worker(orch, created["worker"], 5)
        assert result["finished"] is True

        record = orch.worker_records[created["worker"]]
        assert record["owned_partitions"] == ["src/a/one.py"]
        view = orch._worker_view(record)
        assert view["owned_partitions"] == ["src/a/one.py"]
        assert orch.write_ownership.snapshot() == {}


