"""완료 신고를 Task의 acceptance command로 검증한다.

`finish_turn(outcome="completed")`가 아무 검증 없이 턴을 완료로 굳히면
verification은 UI 버튼에만 존재하고 에이전트 계약에는 없는 것과 같다. 게이트가
실패하면 outcome은 partial로 내려가고, 사용자에게 보이는 최종 답변(summary)도
함께 바로잡힌다 — 화면은 성공인데 기록은 partial인 거짓 상태를 막는다.
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from janus_server import runtime
from janus_server.scheduler import ResourceScheduler
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient


def make_orchestration(root: Path, acceptance_command: str, events: list | None = None):
    context = WorkspaceContext(
        root=root, task_id="task_gate", workspace_id="workspace_gate",
    ).for_dispatch("dispatch_gate")
    spec = {
        "name": "gate", "model": "qwen3.8-27b", "tools": ["read_file"],
        "approval": "auto", "worker_policy": "none",
        "acceptance_command": acceptance_command,
    }
    sink = events if events is not None else []
    with (
        patch.object(runtime, "resolve_local_model", lambda name: name),
        patch.object(runtime, "make_client", lambda: FakeClient([])),
    ):
        orch = runtime.Orchestration(
            spec, send=sink.append, approver=lambda *_args: True,
            workspace_context=context, scheduler=ResourceScheduler(),
        )
    orch.current_dispatch_id = "dispatch_gate"
    orch.active_workspace_context = context
    return orch


def finish(orch, **kwargs):
    return orch.finish_turn["handler"](**kwargs)


def test_passing_acceptance_keeps_the_completed_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(Path(tmp), "exit 0")
        result = finish(orch, outcome="completed", summary="구현을 마쳤습니다",
                        evidence=["runtime.py"])

    assert result["outcome"] == "completed"
    assert result["acceptance"] == {
        "command": "exit 0", "exit_code": 0, "passed": True,
    }
    assert orch.turn_outcome["outcome"] == "completed"
    assert orch.turn_outcome["summary"] == "구현을 마쳤습니다"


def test_failing_acceptance_downgrades_completed_to_partial():
    events: list = []
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(Path(tmp), "echo boom >&2; exit 3", events)
        result = finish(orch, outcome="completed", summary="구현을 마쳤습니다",
                        evidence=["runtime.py"])

    assert result["outcome"] == "partial"
    assert result["acceptance"]["passed"] is False
    assert result["acceptance"]["exit_code"] == 3
    assert orch.turn_outcome["outcome"] == "partial"

    # 사용자에게 보이는 최종 답변이 성공 주장 그대로 남지 않는다.
    summary = orch.turn_outcome["summary"]
    assert summary.startswith("[검증 실패]")
    assert "exit 3" in summary
    assert "구현을 마쳤습니다" in summary  # 모델 보고는 보존한다

    # 실제 실패 근거가 evidence 맨 앞에 붙는다.
    assert orch.turn_outcome["evidence"][0].startswith("acceptance 실패:")
    assert "runtime.py" in orch.turn_outcome["evidence"]

    gates = [e for e in events if e.get("kind") == "acceptance_gate"]
    assert [g["passed"] for g in gates] == [False]
    assert gates[0]["claimed"] == "completed"


def test_gate_only_runs_for_completed():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(Path(tmp), "exit 3")
        for outcome in ("partial", "input_required", "mockup_review"):
            result = finish(orch, outcome=outcome, summary="중간 보고")
            assert result["outcome"] == outcome
            assert "acceptance" not in result


def test_no_acceptance_command_leaves_self_declared_completion_intact():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(Path(tmp), "")
        result = finish(orch, outcome="completed", summary="끝냈습니다")

    assert result["outcome"] == "completed"
    # 없는 근거를 있는 척하지 않는다 — acceptance 키 자체가 없다.
    assert "acceptance" not in result


def test_gate_failure_to_run_is_not_treated_as_a_pass():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(Path(tmp), "true")
        with patch.object(
            runtime.verification_mod, "run",
            side_effect=RuntimeError("scheduler down"),
        ):
            result = finish(orch, outcome="completed", summary="끝냈습니다")

    assert result["outcome"] == "partial"
    assert result["acceptance"]["exit_code"] is None
    assert "scheduler down" in orch.turn_outcome["evidence"][1]
