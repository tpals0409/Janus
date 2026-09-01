"""워커 성과의 턴 경계 회수 계약.

부모 턴이 끝나기 전에 결과를 받지 못한 워커는 quiesce로 강제 종료되지만, 그
성과(상태·변경 파일·부분 결과)는 사라지지 않는다 — 다음 턴 시작에서 [janus runtime]
봉투의 운영 노트로 세션에 재주입되고, 부모가 실제로 받아내면 회수 대상에서 빠진다.
"""

from __future__ import annotations

import json
import tempfile
import threading
import time
from pathlib import Path
from unittest.mock import patch

from janus_server import runtime
from janus_server.scheduler import ResourceScheduler
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient

PARENT_TOOLS = ["read_file", "glob", "grep", "write_file", "edit_file", "run_bash"]


def make_orchestration(fake: FakeClient, root: Path, *, on_worker_outcome=None,
                       persisted_worker_outcomes=None):
    context = WorkspaceContext(
        root=root, task_id="task_recover", workspace_id="workspace_recover",
    ).for_dispatch("dispatch_recover")
    spec = {
        "name": "recover", "model": "qwen3.8-27b", "tools": PARENT_TOOLS,
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
            on_worker_outcome=on_worker_outcome,
            persisted_worker_outcomes=persisted_worker_outcomes,
        )
    orch.current_dispatch_id = "dispatch_recover"
    orch.current_user_text = "워커를 배치해서 진행해"
    orch.active_workspace_context = context
    return orch


def spawn(orch, **kwargs):
    return orch.create_worker["handler"](**kwargs)


def control(orch, name: str):
    return next(tool["handler"] for tool in orch.worker_control_tools
                if tool["name"] == name)


def digest(orch) -> str:
    return orch._format_recovery_digest(orch._undelivered_terminal_workers())


def wait_status(orch, worker_id: str, status: str, timeout: float = 3.0) -> bool:
    record = orch.worker_records[worker_id]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if record["status"] == status:
            return True
        time.sleep(0.02)
    return False


def test_quiesced_writer_is_recovered_in_next_turn_context():
    writer_popped = threading.Event()
    release_writer = threading.Event()

    create_args = json.dumps({
        "name": "ed", "task": "edit src/a.py contents",
        "role": "implementer", "max_steps": 2, "tools": [],
    })

    def by_caller(request):
        """스크립트 위치가 아니라 요청 내용으로 누구의 생성인지 가른다.

        부모와 워커는 각자의 스레드에서 진짜로 동시에 생성을 시작한다 — 팝
        순서에 기대면 스케줄링에 따라 역할이 뒤바뀐다. 전체 본문으로 가르면
        부모의 create_worker 인자에도 같은 task 문자열이 있어 겹치므로,
        마지막 user 메시지(= 그 노드가 받은 지시)로 판별한다.
        """
        instruction = next(
            (str(message.get("content") or "")
             for message in reversed(request.get("messages") or [])
             if message.get("role") == "user"),
            "",
        )
        if instruction.startswith("edit src/a.py contents"):
            # 워커: 부모 턴이 끝날 때까지 실행 중으로 남는다
            writer_popped.set()
            release_writer.wait(5)
            return {"text": "writer output"}
        writer_popped.wait(5)  # 부모 마무리: 워커가 실행 중일 때만 끝낸다
        return {"text": "parent wrapping up"}

    fake = FakeClient([
        {"calls": [("create_worker", create_args)]},
        by_caller,
        by_caller,
        {"text": "resumed"},
        {"text": "again"},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))
        orch.turn("워커를 배치해서 src/a.py를 고쳐줘")

        record = next(iter(orch.worker_records.values()))
        assert record["quiesce"]["reason"] == "parent_turn_ended"
        release_writer.set()
        assert wait_status(orch, record["worker"], "cancelled")
        assert not record.get("delivered")

        # 다음 턴: 미전달 기록이 사용자 메시지 앞에 운영 노트로 주입된다.
        orch.turn("이어서 진행해줘")
        events = orch.session.events
        note_idx = next(i for i, e in enumerate(events) if e["kind"] == "user"
                        and "[janus runtime]" in e["content"])
        resumed_idx = next(i for i, e in enumerate(events) if e["kind"] == "user"
                           and e["content"] == "이어서 진행해줘")
        assert note_idx < resumed_idx
        note = events[note_idx]["content"]
        assert record["worker"] in note
        assert 'task="edit src/a.py contents"' in note
        assert "implementer · cancelled" in note
        assert record["recovery_notes"] == 1

        # 통합 없이 또 끝나면 같은 미전달 기록은 상한(3회)까지만 재노출된다.
        orch.turn("또 이어서 진행해줘")
        notes = [e for e in orch.session.events if e["kind"] == "user"
                 and "[janus runtime]" in e["content"]]
        assert len(notes) == 2


def test_delivered_and_discarded_records_leave_the_recovery_set():
    gate = threading.Event()

    def hold():
        gate.wait(5)
        return {"text": "held output"}

    fake = FakeClient([{"text": "done-one"}, {"text": "done-two"}, hold])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp))

        collected = spawn(orch, name="one", task="task one",
                          role="implementer", tools=[], max_steps=2)
        control(orch, "wait_worker")(collected["worker"], 5)
        assert digest(orch) == ""  # wait으로 결과를 받았다 — 회수 대상 아님

        pending = spawn(orch, name="two", task="task two",
                        role="implementer", tools=[], max_steps=2)
        assert wait_status(orch, pending["worker"], "completed")
        note = digest(orch)
        assert pending["worker"] in note
        assert 'task="task two"' in note
        assert 'result="done-two"' in note

        # 단일 worker_status로 결과를 봤다면 전달 완료다.
        control(orch, "worker_status")(pending["worker"])
        assert digest(orch) == ""

        # 부모가 명시적으로 중단(stop)한 워커도 회수 대상이 아니다.
        stopped = spawn(orch, name="three", task="task three",
                        role="implementer", tools=[], max_steps=2)
        assert wait_status(orch, stopped["worker"], "running")
        control(orch, "stop_worker")(stopped["worker"])
        assert digest(orch) == ""
        gate.set()
        assert wait_status(orch, stopped["worker"], "cancelled")


def test_on_worker_outcome_fires_once_per_terminal_and_resets_on_followup():
    outcomes = []
    gate = threading.Event()

    def blocked():
        gate.wait(5)
        return {"text": "slow output"}

    fake = FakeClient([
        {"text": "first result"},
        blocked,
        {"text": "follow-up result"},
    ])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp), on_worker_outcome=outcomes.append)
        created = spawn(orch, name="w", task="first",
                        role="implementer", tools=[], max_steps=2)
        wid = created["worker"]

        view = control(orch, "wait_worker")(wid, 5)
        assert view["finished"] is True and view["status"] == "completed"
        assert [(o["worker"], o["status"]) for o in outcomes] == [(wid, "completed")]
        assert outcomes[0]["result"] == "first result"
        assert "owned_partitions" in outcomes[0]
        # 영속 계약: 실행 식별자가 훅 페이로드에 함께 실린다.
        assert outcomes[0]["task_id"] == "task_recover"
        assert outcomes[0]["workspace_id"] == "workspace_recover"
        assert outcomes[0]["dispatch_id"] == "dispatch_recover"

        # 후속 작업 재기동 → 실행 중 강제 중단 → cancelled 훅이 정확히 1회.
        control(orch, "send_worker")(wid, "second instruction")
        record = orch.worker_records[wid]
        # 후속 재기동은 새 종료 경계다 — 훅·회수 상태가 여기서 초기화됐음을 검증.
        with orch.lock:
            assert record.get("outcome_recorded") is False
            assert not record.get("delivered")
        assert wait_status(orch, wid, "running")
        control(orch, "stop_worker")(wid)
        gate.set()
        assert wait_status(orch, wid, "cancelled")
        control(orch, "worker_status")(wid)  # 반복 조회가 훅을 다시 쏘지 않는다
        control(orch, "worker_status")(wid)
        assert [o["status"] for o in outcomes] == ["completed", "cancelled"]
        # 부모가 stop으로 명시적으로 버렸으므로 회수 노트 대상이 아니다.
        assert digest(orch) == ""


def test_persisted_outcomes_are_injected_once_on_the_first_turn():
    rows = [{
        "worker_id": "w9-coder", "name": "coder", "role": "implementer",
        "status": "cancelled", "result": "partial edit applied",
        "changed_paths": ["src/a.py"], "owned_partitions": ["src/"],
    }]
    fake = FakeClient([{"text": "resumed"}, {"text": "more"}])
    with tempfile.TemporaryDirectory() as tmp:
        orch = make_orchestration(fake, Path(tmp),
                                  persisted_worker_outcomes=rows)
        orch.turn("이어서 진행해줘")

        persisted = [e for e in orch.session.events if e["kind"] == "user"
                     and "Persisted worker outcomes" in e["content"]]
        assert len(persisted) == 1
        content = persisted[0]["content"]
        assert "[janus runtime]" in content
        assert "w9-coder [implementer · cancelled(persisted)]" in content
        assert "changed=[src/a.py]" in content
        assert 'result="partial edit applied"' in content
        assert orch.persisted_worker_outcomes == []  # 소비 완료

        orch.turn("또 이어서 해줘")
        all_notes = [e for e in orch.session.events if e["kind"] == "user"
                     and "Persisted worker outcomes" in e["content"]]
        assert len(all_notes) == 1  # 두 번째 턴에는 다시 주입되지 않는다
