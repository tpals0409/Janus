"""Background worker lifecycle and role capability contracts."""

from __future__ import annotations

import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from janus_server import runtime
from janus_server.scheduler import ResourceScheduler
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient


def make_orchestration(fake: FakeClient, root: Path, *, tools: list[str]):
    context = WorkspaceContext(
        root=root, task_id="task_async", workspace_id="workspace_async",
    ).for_dispatch("dispatch_async")
    spec = {
        "name": "async", "model": "qwen3.8-27b", "tools": tools,
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
    orch.current_dispatch_id = "dispatch_async"
    orch.current_user_text = "워커를 배치해서 진행해"
    orch.active_workspace_context = context
    return orch


def control(orch, name: str):
    return next(tool["handler"] for tool in orch.worker_control_tools
                if tool["name"] == name)


def test_bundled_personas_and_skills_are_composed_by_role():
    janus = runtime.persona_prompt("janus")
    assert "# Janus" in janus
    assert "# Task Contract" in janus
    assert "name: task-contract" not in janus

    implementer = runtime.persona_prompt(
        "implementer", custom_prompt="Keep the change inside src/widget.ts.",
    )
    assert "# Implementer" in implementer
    assert "# Minimal Patch" in implementer
    assert "# Verification Before Completion" in implementer
    assert "## Delegated emphasis" in implementer
    assert "Keep the change inside src/widget.ts." in implementer

    assert "# Scout" in runtime.persona_prompt("researcher")
    with pytest.raises(ValueError, match="unknown persona role"):
        runtime.persona_prompt("architect")


def test_finish_turn_records_a_structured_outcome():
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(FakeClient([]), Path(tmp), tools=["echo"])
        recorded = orch.finish_turn["handler"](
            outcome="completed", summary="Implemented the requested change.",
            evidence=["pytest: passed"],
        )
        assert recorded["recorded"] is True
        assert orch.snapshot_turn_outcome() == {
            "outcome": "completed",
            "summary": "Implemented the requested change.",
            "evidence": ["pytest: passed"],
        }


def test_spawn_returns_before_worker_finishes_and_wait_collects_result():
    entered = threading.Event()
    release = threading.Event()

    def blocked():
        entered.set()
        release.wait(2)
        return {"text": "background result"}

    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(FakeClient([blocked]), Path(tmp), tools=["echo"])
        started = time.monotonic()
        created = orch.create_worker["handler"](
            name="background", system_prompt="work", task="do it",
            role="implementer", tools=[], max_steps=2,
        )
        elapsed = time.monotonic() - started

        assert created["created"] is True
        assert elapsed < 0.5
        assert entered.wait(1)
        assert control(orch, "worker_status")(created["worker"])["status"] == "running"
        release.set()
        result = control(orch, "wait_worker")(created["worker"], 2)
        assert result["finished"] is True
        assert result["status"] == "completed"
        assert result["result"] == "background result"


def test_role_defaults_inherit_parent_tools_and_keep_read_only_roles_safe():
    parent_tools = ["read_file", "glob", "grep", "write_file", "edit_file", "run_bash"]
    fake = FakeClient([{"text": "implemented"}, {"text": "researched"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp), tools=parent_tools)
        implementer = orch.create_worker["handler"](
            name="coder", system_prompt="work", task="implement",
            role="implementer", tools=[], max_steps=2,
        )
        assert control(orch, "wait_worker")(implementer["worker"], 2)["finished"]
        implementer_tools = {
            item["function"]["name"] for item in fake.captured[0]["tools"]
        }
        assert implementer_tools == set(parent_tools)

        orch.turn_worker_count = 0
        researcher = orch.create_worker["handler"](
            name="reader", system_prompt="inspect", task="research",
            role="researcher", tools=[], max_steps=2,
        )
        assert control(orch, "wait_worker")(researcher["worker"], 2)["finished"]
        researcher_tools = {
            item["function"]["name"] for item in fake.captured[1]["tools"]
        }
        assert {"write_file", "edit_file", "run_bash"}.isdisjoint(researcher_tools)
        assert {"read_file", "glob", "grep"}.issubset(researcher_tools)


def test_new_read_only_and_execution_roles_get_expected_tool_sets():
    parent_tools = ["read_file", "glob", "grep", "write_file", "edit_file", "run_bash"]
    fake = FakeClient([
        {"text": "scouted"}, {"text": "planned"},
        {"text": "prototyped"}, {"text": "operated"},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp), tools=parent_tools)
        for index, role in enumerate(("scout", "planner", "prototyper", "operator")):
            orch.turn_worker_count = 0
            created = orch.create_worker["handler"](
                name=role, task=f"do {role}", role=role, tools=[], max_steps=2,
            )
            assert control(orch, "wait_worker")(created["worker"], 2)["finished"]
            tool_names = {
                item["function"]["name"] for item in fake.captured[index]["tools"]
            }
            if role in {"scout", "planner"}:
                assert {"write_file", "edit_file", "run_bash"}.isdisjoint(tool_names)
                assert {"read_file", "glob", "grep"}.issubset(tool_names)
            else:
                assert tool_names == set(parent_tools)


def test_send_reuses_worker_session_and_stop_is_exposed():
    fake = FakeClient([{"text": "first result"}, {"text": "follow-up result"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp), tools=["echo"])
        created = orch.create_worker["handler"](
            name="persistent", system_prompt="work", task="first",
            role="implementer", tools=[], max_steps=2,
        )
        assert control(orch, "wait_worker")(created["worker"], 2)["finished"]
        queued = control(orch, "send_worker")(created["worker"], "second")
        assert queued["queued"] is True
        result = control(orch, "wait_worker")(created["worker"], 2)
        assert result["result"] == "follow-up result"
        messages = fake.captured[1]["messages"]
        assert any(message.get("content") == "first result" for message in messages)
        assert any(message.get("content") == "second" for message in messages)
        stopped = control(orch, "stop_worker")(created["worker"])
        assert stopped == {"worker": created["worker"], "status": "completed", "stopped": False}
