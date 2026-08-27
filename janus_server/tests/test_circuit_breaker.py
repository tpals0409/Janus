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


def run_with_tool(handler, turns) -> list[dict]:
    events: list[dict] = []
    tool = T._t(
        "probe", handler, lambda value: str(value),
        T._obj([]), "probe", resource_class="cpu_tool",
    )
    with tempfile.TemporaryDirectory() as tmp:
        workspace = WorkspaceContext(
            Path(tmp), "task-cb", "workspace-cb", "dispatch-cb",
        )
        agent.run(
            client=FakeClient(turns), model="fake", system_prompt="",
            task="probe", tool_names=["probe"], extra_tools=[tool],
            workspace_context=workspace, approve=lambda _n, _a: True,
            emit=lambda kind, **data: events.append({"kind": kind, **data}),
            max_steps=8,
        )
    return events


class CircuitBreakerTests(unittest.TestCase):
    def test_null_error_field_in_successful_results_does_not_trip_the_breaker(self):
        # worker_status/wait_worker 뷰처럼 정상 결과에 error=None이 실린다.
        calls = [("probe", "{}")]
        events = run_with_tool(
            lambda: {"status": "completed", "error": None},
            [{"calls": calls}, {"calls": calls}, {"calls": calls}, {"text": "done"}],
        )
        reasons = [e.get("reason") for e in events if e["kind"] == "done"]
        self.assertNotIn("circuit_break:probe", reasons)

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
        events = run_with_tool(
            lambda: {"status": "completed", "error": None},
            [{"calls": calls}, {"text": "done"}],
        )
        ends = [e for e in events if e["kind"] == "tool_run_end"]
        self.assertEqual(["success"], [e.get("status") for e in ends])

    def test_repeated_real_errors_still_trip_the_breaker(self):
        calls = [("probe", "{}")]
        events = run_with_tool(
            lambda: {"error": "boom"},
            [{"calls": calls}, {"calls": calls}, {"calls": calls}, {"text": "unreached"}],
        )
        reasons = [e.get("reason") for e in events if e["kind"] == "done"]
        self.assertIn("circuit_break:probe", reasons)


if __name__ == "__main__":
    unittest.main()
