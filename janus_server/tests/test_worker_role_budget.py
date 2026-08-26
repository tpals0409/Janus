"""같은 역할 재스폰 상한(workers.role_limit) — 페르소나 재시도 계약의 엔진 강제."""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import patch

from janus_server import runtime
from janus_server.scheduler import ResourceScheduler
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient


def make_orchestration(fake: FakeClient, root: Path, *, budget: dict | None = None):
    context = WorkspaceContext(
        root=root, task_id="task_roles", workspace_id="workspace_roles",
    ).for_dispatch("dispatch_roles")
    spec = {
        "name": "roles", "model": "qwen3.8-27b", "tools": ["echo"],
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
            budget=budget,
        )
    orch.current_dispatch_id = "dispatch_roles"
    orch.current_user_text = "워커를 배치해서 진행해"
    orch.active_workspace_context = context
    return orch


def spawn(orch, **kwargs):
    return orch.create_worker["handler"](**kwargs)


def wait_worker(orch, worker_id: str, timeout: float = 5.0) -> dict:
    handler = next(tool["handler"] for tool in orch.worker_control_tools
                   if tool["name"] == "wait_worker")
    return handler(worker_id, timeout)


def test_same_role_spawns_stop_at_engine_role_limit():
    fake = FakeClient([
        {"text": "try one"}, {"text": "try two"}, {"text": "try three"},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        for index in range(3):  # 초기 시도 + 교정 재시도 2회 — 페르소나 계약 범위
            created = spawn(orch, name=f"impl-{index}",
                            task=f"attempt {index} of the edit",
                            role="implementer", tools=[], max_steps=2)
            assert created.get("created") is True, created
            assert wait_worker(orch, created["worker"])["finished"] is True

        fourth = spawn(orch, name="impl-3", task="yet another retry wording",
                       role="implementer", tools=[], max_steps=2)
        assert "created" not in fourth
        assert fourth["reason"] == "worker_role_budget"
        assert fourth["counts"] == {
            "role": "implementer", "spawned": 3, "role_limit": 3,
            "total_spawns": 3, "total_limit": 4,
        }
        assert "different allowed role" in fourth["result"]
        # 거부가 스폰 회계를 소비하지 않는다.
        assert orch.worker_seq == 3


def test_role_limit_is_per_role_not_global():
    fake = FakeClient([
        {"text": "i1"}, {"text": "i2"}, {"text": "i3"},
        {"text": "scouted after implementers retired"},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        for index in range(3):
            created = spawn(orch, name=f"impl-{index}",
                            task=f"edit attempt {index}",
                            role="implementer", tools=[], max_steps=2)
            assert wait_worker(orch, created["worker"])["finished"] is True

        scout = spawn(orch, name="eye", task="fresh investigation angle",
                      role="scout", tools=[], max_steps=2)
        assert scout.get("created") is True  # 역할이 다르면 영향을 받지 않는다
        assert wait_worker(orch, scout["worker"])["finished"] is True
        assert orch.role_spawn_counts == {"implementer": 3, "scout": 1}


def test_budget_override_tightens_the_role_limit():
    fake = FakeClient([{"text": "only attempt"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(
            fake, Path(tmp),
            budget={"workers": {"role_limit": 1}},
        )
        first = spawn(orch, name="solo", task="the one allowed try",
                      role="implementer", tools=[], max_steps=2)
        assert first.get("created") is True
        assert wait_worker(orch, first["worker"])["finished"] is True

        retry = spawn(orch, name="retry", task="same role different words",
                      role="implementer", tools=[], max_steps=2)
        assert "created" not in retry
        assert retry["reason"] == "worker_role_budget"
        assert retry["counts"]["spawned"] == 1
        assert retry["counts"]["role_limit"] == 1
