"""서킷 브레이커 판정 — error 키 존재가 아니라 값으로 실패를 계수한다 (P4 QA 결함)."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import agent
from janus_server import tools as T
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient


def run_with_tool(handler, turns) -> tuple[list[dict], FakeClient]:
    events: list[dict] = []
    fake = FakeClient(turns)
    tool = T._t(
        "probe", handler, lambda value: str(value),
        T._obj([]), "probe", resource_class="cpu_tool",
    )
    with tempfile.TemporaryDirectory() as tmp:
        workspace = WorkspaceContext(
            Path(tmp), "task-cb", "workspace-cb", "dispatch-cb",
        )
        agent.run(
            client=fake, model="fake", system_prompt="",
            task="probe", tool_names=["probe"], extra_tools=[tool],
            workspace_context=workspace, approve=lambda _n, _a: True,
            emit=lambda kind, **data: events.append({"kind": kind, **data}),
            max_steps=8,
        )
    return events, fake


class CircuitBreakerTests(unittest.TestCase):
    def test_null_error_field_in_successful_results_does_not_trip_the_breaker(self):
        # worker_status/wait_worker 뷰처럼 정상 결과에 error=None이 실린다.
        calls = [("probe", "{}")]
        events, _ = run_with_tool(
            lambda: {"status": "completed", "error": None},
            [{"calls": calls}, {"calls": calls}, {"calls": calls}, {"text": "done"}],
        )
        self.assertEqual([], [e for e in events if e["kind"] == "circuit_break"])

    def test_null_error_field_renders_the_result_body_not_an_error_banner(self):
        # wait_worker view가 "ERROR: None"으로 렌더링되면 모델이 결과를 못 본다.
        view = {"worker": "w1", "status": "completed",
                "result": "full report", "error": None}
        rendered = T.render("wait_worker", view, registry={
            "wait_worker": T._t("wait_worker", lambda: view,
                                lambda v: v["result"], T._obj([]), "wait"),
        })
        self.assertEqual("full report", rendered)
        self.assertEqual("ERROR: boom", T.render("wait_worker", {"error": "boom"}))

    def test_tool_run_end_reports_success_for_null_error_results(self):
        calls = [("probe", "{}")]
        events, _ = run_with_tool(
            lambda: {"status": "completed", "error": None},
            [{"calls": calls}, {"text": "done"}],
        )
        ends = [e for e in events if e["kind"] == "tool_run_end"]
        self.assertEqual(["success"], [e.get("status") for e in ends])

    def test_repeated_errors_withdraw_the_tool_and_the_turn_ends_with_a_report(self):
        """서킷 브레이크가 턴을 죽이지 않는다 — 도구만 회수하고 보고를 받는다 (P6)."""
        calls = [("probe", "{}")]
        events, fake = run_with_tool(
            lambda: {"error": "boom"},
            [{"calls": calls}, {"calls": calls}, {"calls": calls},
             {"text": "3회 시도가 모두 실패해 중단했습니다."}],
        )
        breaks = [e for e in events if e["kind"] == "circuit_break"]
        self.assertEqual(["probe"], [e.get("tool") for e in breaks])
        reasons = [e.get("reason") for e in events if e["kind"] == "done"]
        self.assertNotIn("circuit_break:probe", reasons)
        # 회수 후 마지막 생성: probe 스키마가 제공되지 않고 보고 지시가 주입된다.
        final_call = fake.captured[-1]
        self.assertNotIn("tools", final_call)
        self.assertIn("was withdrawn", final_call["messages"][-1]["content"])
        # 최종 보고가 실제로 나왔다.
        texts = [e.get("text") for e in events if e["kind"] == "text_delta"]
        self.assertIn("3회 시도가 모두 실패해 중단했습니다.", "".join(t or "" for t in texts))

    def test_withdrawn_tool_cannot_still_be_executed(self):
        """스키마에서 빼는 것만으로는 못 막는다.

        로컬 모델은 광고되지 않은 도구명도 그냥 뱉는다. 실행 게이트까지 닫지
        않으면 3연속 실패한 도구가 4번째로 다시 실행된다 — 승인 3회 거부 뒤에도
        네 번째 승인 대화상자가 뜨는 경로다.
        """
        invocations = []

        def handler():
            invocations.append(1)
            return {"error": "boom"}

        calls = [("probe", "{}")]
        events, _ = run_with_tool(
            handler,
            [{"calls": calls}, {"calls": calls}, {"calls": calls},
             {"calls": calls},  # 회수 뒤에도 모델이 같은 도구를 부른다
             {"text": "중단했습니다."}],
        )
        self.assertEqual(3, len(invocations), "회수 후에도 핸들러가 실행됐다")
        rejected = [e for e in events if e["kind"] == "tool_rejected"]
        self.assertEqual(["probe"], [e.get("name") for e in rejected])
        self.assertEqual(
            ["tool_not_in_node_subset"], [e.get("reason") for e in rejected]
        )


if __name__ == "__main__":
    unittest.main()
