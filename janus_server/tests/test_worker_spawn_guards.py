"""스폰·후속 경로가 우회할 수 없는 엔진 가드.

세 가지 우회로를 막는다:

- 스폰이 스레드 기동 전에 실패하면 회계(seq·active_workers·fingerprint)를 되돌린다.
  임대만 반납하면 active_workers가 영영 줄지 않고(감소는 워커 스레드의 finally에만
  있다) 남은 fingerprint가 이후 같은 스폰을 전부 duplicate로 막는다.
- send_worker 후속은 재실행이다 — 예산·동시성·write 임대를 스폰과 같게 받는다.
- read-only로 좁혀진 턴에서 쓰기 워커를 스폰해 턴 가드를 우회할 수 없다.
"""

from __future__ import annotations

import tempfile
import threading
from pathlib import Path
from unittest.mock import patch

from janus_server import runtime
from janus_server import tools as T
from janus_server.scheduler import ResourceScheduler
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient

PARENT_TOOLS = ["read_file", "glob", "grep", "write_file", "edit_file", "run_bash"]


def make_orchestration(fake: FakeClient, root: Path, send=None):
    context = WorkspaceContext(
        root=root, task_id="task_guard", workspace_id="workspace_guard",
    ).for_dispatch("dispatch_guard")
    spec = {
        "name": "guard", "model": "qwen3.8-27b", "tools": PARENT_TOOLS,
        "approval": "auto", "worker_policy": "autonomous",
        "allow_autonomous_workers": True,
    }
    with (
        patch.object(runtime, "resolve_local_model", lambda name: name),
        patch.object(runtime, "make_client", lambda: fake),
    ):
        orch = runtime.Orchestration(
            spec, send=send or (lambda _event: None), approver=lambda *_args: True,
            workspace_context=context, scheduler=ResourceScheduler(),
        )
    orch.current_dispatch_id = "dispatch_guard"
    orch.current_user_text = "워커를 배치해서 진행해"
    orch.active_workspace_context = context
    return orch


def spawn(orch, **kwargs):
    return orch.create_worker["handler"](**kwargs)


def control(orch, name: str):
    return next(tool["handler"] for tool in orch.worker_control_tools
                if tool["name"] == name)


def wait_worker(orch, worker_id: str, timeout: float = 5.0) -> dict:
    return control(orch, "wait_worker")(worker_id, timeout)


# ── 스폰 실패 롤백 ──

def test_failed_spawn_rolls_back_accounting_and_does_not_poison_the_fingerprint():
    """전송 실패 한 번이 세션의 위임 기능을 영구히 죽이면 안 된다."""
    fail_once = {"armed": True}

    def flaky_send(event: dict) -> None:
        # 스폰 회계 뒤·스레드 기동 전에 나오는 이벤트에서 한 번만 터진다
        # (실제로는 이 경로가 SQLite 기록이라 일시 실패가 가능하다).
        if event.get("kind") == "worker_step_budget_reserved" and fail_once["armed"]:
            fail_once["armed"] = False
            raise RuntimeError("transient event sink failure")

    fake = FakeClient([{"text": "done"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp), send=flaky_send)
        args = dict(name="writer", task="implement A", role="implementer",
                    tools=[], max_steps=2)

        try:
            spawn(orch, **args)
        except RuntimeError:
            pass
        else:  # pragma: no cover - 실패를 주입했으므로 여기 오면 테스트가 틀렸다
            raise AssertionError("주입한 실패가 전파되지 않았다")

        # 회계가 전부 원복됐다.
        assert orch.worker_seq == 0
        assert orch.active_workers == 0
        assert orch.role_spawn_counts.get("implementer", 0) == 0
        assert orch.worker_requests == {}
        assert orch.worker_records == {}
        assert orch.write_ownership.snapshot() == {}

        # 같은 스폰이 duplicate_worker_running으로 막히지 않는다.
        retried = spawn(orch, **args)
        assert retried.get("created") is True, retried
        assert wait_worker(orch, retried["worker"])["finished"] is True


# ── send_worker 게이트 ──

def test_followup_reacquires_the_write_lease():
    """첫 실행의 finally가 임대를 반납했다 — 후속은 다시 잡아야 한다."""
    release = threading.Event()
    running = threading.Event()

    def hold():
        running.set()
        release.wait(3)
        return {"text": "followup done"}

    fake = FakeClient([{"text": "first done"}, hold])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        first = spawn(orch, name="writer", task="implement A",
                      role="implementer", tools=[], max_steps=2,
                      owned_paths=["src/"])
        assert first.get("created") is True
        assert wait_worker(orch, first["worker"])["finished"] is True
        assert orch.write_ownership.snapshot() == {}, "첫 실행이 임대를 반납했어야 한다"

        queued = control(orch, "send_worker")(first["worker"], "src/를 마저 고쳐줘")
        assert queued.get("queued") is True, queued
        assert running.wait(3)
        # 후속이 도는 동안 임대를 다시 쥐고 있어야 한다.
        assert list(orch.write_ownership.snapshot().values()) == [["src/"]]

        # 그리고 그 임대는 실제로 겹치는 스폰을 막는다.
        rival = spawn(orch, name="rival", task="implement B",
                      role="implementer", tools=[], max_steps=2,
                      owned_paths=["src/deep/file.py"])
        assert rival.get("reason") == "write_partition_conflict", rival

        release.set()
        assert wait_worker(orch, first["worker"])["finished"] is True
        assert orch.write_ownership.snapshot() == {}


def test_followup_budget_carries_over_instead_of_resetting():
    """후속마다 풀 예산을 새로 주면 role_limit이 막으려던 무한 재디스패치가 열린다."""
    fake = FakeClient([{"text": "first done"}, {"text": "second done"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        first = spawn(orch, name="worker", task="do A",
                      role="researcher", tools=["read_file"], max_steps=4)
        assert first.get("created") is True
        assert wait_worker(orch, first["worker"])["finished"] is True

        record = orch.worker_records[first["worker"]]
        spent = int(record["worker_budget"].snapshot()["usage"]["steps"])
        assert spent >= 1, "첫 실행이 스텝을 썼어야 한다"

        assert control(orch, "send_worker")(first["worker"], "이어서 해줘")["queued"]
        assert wait_worker(orch, first["worker"])["finished"] is True
        carried = int(record["worker_budget"].snapshot()["usage"]["steps"])
        assert carried > spent, "후속 예산이 0에서 다시 시작했다"


def test_followup_is_refused_when_the_worker_budget_is_spent():
    fake = FakeClient([{"text": "first done"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        # 후속은 프로필의 worker 한도를 그대로 쓰고 사용량만 이월한다 — 한도를
        # 1로 낮추면 첫 실행 한 스텝으로 이미 소진된다.
        orch.budget["worker"]["step_limit"] = 1
        first = spawn(orch, name="worker", task="do A",
                      role="researcher", tools=["read_file"], max_steps=1)
        assert first.get("created") is True
        assert wait_worker(orch, first["worker"])["finished"] is True

        refused = control(orch, "send_worker")(first["worker"], "이어서 해줘")
        assert refused.get("reason") == "worker_budget_exhausted", refused
        assert orch.active_workers == 0
        assert orch.write_ownership.snapshot() == {}


def test_followup_respects_the_concurrent_worker_limit():
    release = threading.Event()
    running = threading.Event()

    def hold():
        running.set()
        release.wait(3)
        return {"text": "live done"}

    fake = FakeClient([{"text": "first done"}, hold])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        done = spawn(orch, name="done-worker", task="do A",
                     role="researcher", tools=["read_file"], max_steps=4)
        assert wait_worker(orch, done["worker"])["finished"] is True
        live = spawn(orch, name="live-worker", task="do B",
                     role="researcher", tools=["read_file"], max_steps=4)
        assert live.get("created") is True
        assert running.wait(3)

        orch.budget["workers"]["concurrent_limit"] = 1
        refused = control(orch, "send_worker")(done["worker"], "이어서 해줘")
        assert refused.get("reason") == "worker_concurrent_budget", refused

        release.set()
        assert wait_worker(orch, live["worker"])["finished"] is True


# ── read-only 턴 가드 ──

def test_read_only_turn_cannot_spawn_a_write_worker():
    """턴 가드가 부모 도구만 좁히고 워커는 안 좁히면 가드가 아니다."""
    fake = FakeClient([{"text": "scouted"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        # turn()이 read-only 요청에서 채우는 값과 같은 상태를 만든다.
        orch.turn_tools = ["read_file", "glob", "grep"]

        created = spawn(orch, name="writer", task="edit the file",
                        role="implementer",
                        tools=["read_file", "write_file", "run_bash"], max_steps=2)
        assert created.get("created") is True
        assert created["tools"] == ["read_file"], created["tools"]
        # 쓰기 도구가 없으므로 write 임대 자체를 잡지 않는다.
        assert orch.write_ownership.snapshot() == {}
        assert wait_worker(orch, created["worker"])["finished"] is True


# ── 부모도 소유권 테이블을 지난다 ──

def test_parent_cannot_write_a_path_a_worker_holds():
    """가장 활발한 writer인 오케스트레이터가 면제면 불변식이 아니다."""
    release = threading.Event()
    running = threading.Event()

    def hold():
        running.set()
        release.wait(3)
        return {"text": "worker done"}

    fake = FakeClient([hold])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        worker = spawn(orch, name="writer", task="edit src",
                       role="implementer", tools=[], max_steps=2,
                       owned_paths=["src/"])
        assert worker.get("created") is True
        assert running.wait(3)

        # 부모가 실제로 쓰는 경로 그대로 — dispatch가 _context를 주입한다.
        registry = {**T.REGISTRY}
        registry.update({t["name"]: t for t in orch._parent_write_guards()})
        context = orch.active_workspace_context

        def parent_write(path: str) -> dict:
            return T.dispatch(
                "write_file", {"path": path, "content": "parent edit"},
                approve=lambda *_a: True, registry=registry, context=context,
            )

        blocked = parent_write("src/deep/module.py")
        assert blocked["reason"] == "write_partition_conflict", blocked
        assert worker["worker"] in blocked["error"]

        # 임대 밖 경로는 그대로 통과한다.
        allowed = parent_write("docs/notes.md")
        assert "reason" not in allowed, allowed
        assert (Path(tmp) / "docs" / "notes.md").exists()

        release.set()
        assert wait_worker(orch, worker["worker"])["finished"] is True

        # 임대가 풀리면 부모가 다시 쓸 수 있다.
        after = parent_write("src/deep/module.py")
        assert "reason" not in after, after
        assert (Path(tmp) / "src" / "deep" / "module.py").exists()


def test_full_turn_tools_are_restored_for_a_normal_turn():
    fake = FakeClient([{"text": "done"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        assert orch.turn_tools is None
        created = spawn(orch, name="writer", task="edit the file",
                        role="implementer",
                        tools=["read_file", "write_file"], max_steps=2)
        assert created["tools"] == ["read_file", "write_file"]
        assert wait_worker(orch, created["worker"])["finished"] is True
