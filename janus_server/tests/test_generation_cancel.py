"""prefill에 매달린 생성도 취소된다.

`client.chat.completions.create()`는 서버가 prefill을 끝낼 때까지 블로킹한다.
취소 검사가 청크 루프 안에만 있으면 첫 바이트가 오기 전에는 stop 버튼이 요청
타임아웃(기본 1,200초)까지 아무 효과가 없고, 그동안 model generation 슬롯도
계속 잡혀 있다.
"""

from __future__ import annotations

import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import agent
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient, FakeStream, text_chunk, usage_chunk


class StalledClient:
    """create()가 풀릴 때까지 블로킹하는 클라이언트 — prefill 정체를 흉내낸다."""

    def __init__(self, release: threading.Event) -> None:
        self.release = release
        self.entered = threading.Event()
        self.closed = threading.Event()
        self.chat = type("C", (), {"completions": self})()

    def create(self, **_kwargs):
        self.entered.set()
        self.release.wait(30)
        return self._stream()

    def _stream(self):
        client = self

        class Stream(FakeStream):
            def close(self) -> None:
                client.closed.set()

        return Stream([text_chunk("late"), usage_chunk()])


class GenerationCancelTests(unittest.TestCase):
    def test_open_stream_raises_when_cancelled_before_the_first_chunk(self):
        release = threading.Event()
        client = StalledClient(release)
        cancel = threading.Event()

        result: dict = {}

        def call() -> None:
            try:
                agent._open_stream(lambda: client.create(), cancel)
            except agent.GenerationCancelled:
                result["cancelled"] = True

        worker = threading.Thread(target=call, daemon=True)
        worker.start()
        self.assertTrue(client.entered.wait(5), "생성 요청이 시작되지 않았다")

        started = time.monotonic()
        cancel.set()
        worker.join(5)
        self.assertFalse(worker.is_alive(), "취소가 첫 청크 전에 먹지 않았다")
        self.assertTrue(result.get("cancelled"))
        self.assertLess(time.monotonic() - started, 5)

        # 뒤늦게 도착한 스트림은 서버 생성을 붙잡지 않도록 닫힌다.
        release.set()
        self.assertTrue(client.closed.wait(5), "취소 후 도착한 스트림이 안 닫혔다")

    def test_run_returns_cancelled_instead_of_waiting_for_the_request_timeout(self):
        release = threading.Event()
        client = StalledClient(release)
        cancel = threading.Event()
        events: list[dict] = []

        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceContext(
                Path(tmp), "task-cancel", "workspace-cancel", "dispatch-cancel",
            )

            def call() -> None:
                agent.run(
                    client=client, model="fake", system_prompt="", task="hang",
                    tool_names=[], workspace_context=workspace,
                    approve=lambda _n, _a: True,
                    emit=lambda kind, **data: events.append({"kind": kind, **data}),
                    cancel=cancel, max_steps=2,
                )

            worker = threading.Thread(target=call, daemon=True)
            worker.start()
            self.assertTrue(client.entered.wait(5))
            cancel.set()
            worker.join(5)
            self.assertFalse(worker.is_alive(), "턴이 취소로 끝나지 않았다")
            release.set()

        self.assertEqual(
            ["cancelled"], [e["reason"] for e in events if e["kind"] == "done"]
        )
        ends = [e for e in events if e["kind"] == "model_generation_end"]
        self.assertEqual(["cancelled"], [e["status"] for e in ends])

    def test_uncancelled_generation_still_returns_the_stream(self):
        release = threading.Event()
        release.set()
        client = StalledClient(release)
        stream = agent._open_stream(lambda: client.create(), threading.Event())
        text, calls, usage, _ = agent._assemble(
            stream, lambda _kind, **_data: None
        )
        self.assertEqual("late", text)
        self.assertEqual([], calls)
        self.assertEqual(1, usage["prompt_tokens"])


class TransientStreamFailureTests(unittest.TestCase):
    """스트림이 한 번 끊겼다고 턴 전체가 죽으면 안 된다.

    recovery는 model_oom·timeout 같은 일시 실패를 이미 분류할 줄 아는데,
    agent 루프가 그 판정을 쓰지 않아 그때까지의 작업이 실패로 보였다.
    """

    def run_turn(self, turns) -> tuple[str, list[dict]]:
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceContext(
                Path(tmp), "task-retry", "workspace-retry", "dispatch-retry",
            )
            last, _ = agent.run(
                client=FakeClient(turns), model="fake", system_prompt="",
                task="go", tool_names=[], workspace_context=workspace,
                approve=lambda _n, _a: True,
                emit=lambda kind, **data: events.append({"kind": kind, **data}),
                max_steps=4,
            )
        return last, events

    def test_a_retryable_stream_failure_is_retried_once(self):
        def boom():
            raise RuntimeError("connection reset")

        last, events = self.run_turn([boom, {"text": "복구 후 답변"}])

        self.assertEqual("복구 후 답변", last)
        retries = [e for e in events if e["kind"] == "model_generation_retry"]
        self.assertEqual(1, len(retries))
        self.assertEqual("runtime_error", retries[0]["failure_kind"])

    def test_a_second_failure_still_surfaces(self):
        def boom():
            raise RuntimeError("connection reset")

        with self.assertRaisesRegex(RuntimeError, "connection reset"):
            self.run_turn([boom, boom, {"text": "안 쓰임"}])

    def test_a_non_retryable_failure_is_not_retried(self):
        def boom():
            # worktree 충돌은 recovery가 retryable=False로 분류한다.
            raise RuntimeError("worktree already checked out")

        with self.assertRaisesRegex(RuntimeError, "already checked out"):
            self.run_turn([boom, {"text": "안 쓰임"}])


if __name__ == "__main__":
    unittest.main()
