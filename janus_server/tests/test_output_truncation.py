"""max_tokens 절단(finish_reason="length")을 파싱 실패와 구분한다.

thinking 모드에서는 reasoning·답변·도구 인자가 max_tokens 하나를 나눠 쓴다.
절단으로 잘린 tool_call arguments를 그냥 "인자 JSON 파싱 실패"로 돌려주면 모델은
원인을 모른 채 같은 길이로 다시 시도한다.
"""

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
from tests.fakes import (
    FakeClient,
    FakeStream,
    call_chunk,
    finish_chunk,
    text_chunk,
    usage_chunk,
)


def run_turns(turns) -> list[dict]:
    events: list[dict] = []
    tool = T._t("probe", lambda **_kw: {"ok": True}, str, T._obj([]), "probe")
    with tempfile.TemporaryDirectory() as tmp:
        workspace = WorkspaceContext(
            Path(tmp), "task-trunc", "workspace-trunc", "dispatch-trunc",
        )
        agent.run(
            client=FakeClient(turns), model="fake", system_prompt="",
            task="probe", tool_names=["probe"], extra_tools=[tool],
            workspace_context=workspace, approve=lambda _n, _a: True,
            emit=lambda kind, **data: events.append({"kind": kind, **data}),
            max_steps=4,
        )
    return events


class OutputTruncationTests(unittest.TestCase):
    def test_assemble_reports_finish_reason(self):
        stream = FakeStream([text_chunk("partial"), finish_chunk("length")])
        _, _, _, finish_reason = agent._assemble(
            stream, lambda _kind, **_data: None
        )
        self.assertEqual("length", finish_reason)

    def test_assemble_finish_reason_is_none_without_the_field(self):
        stream = FakeStream([text_chunk("ok"), usage_chunk()])
        _, _, _, finish_reason = agent._assemble(
            stream, lambda _kind, **_data: None
        )
        self.assertIsNone(finish_reason)

    def test_truncated_tool_arguments_report_truncation_not_a_parse_error(self):
        # 인자 JSON이 중간에서 끊긴 채 finish_reason=length로 끝난 생성.
        truncated_turn = [
            call_chunk(0, "c1", "probe", '{"path": "very/long/pa'),
            finish_chunk("length"),
            usage_chunk(),
        ]
        events = run_turns([truncated_turn, {"text": "짧게 나눠 다시 호출하겠습니다."}])

        truncations = [e for e in events if e["kind"] == "generation_truncated"]
        self.assertEqual(1, len(truncations))
        self.assertTrue(truncations[0]["had_tool_calls"])

        results = [e for e in events if e["kind"] == "tool_result"]
        self.assertEqual(1, len(results))
        self.assertEqual("output_truncated", results[0]["value"]["reason"])
        self.assertIn("max_tokens", results[0]["value"]["error"])

    def test_unparseable_arguments_without_truncation_stay_a_parse_error(self):
        # 절단이 아닌 진짜 malformed JSON은 기존 메시지를 유지한다.
        malformed_turn = [
            call_chunk(0, "c1", "probe", "{not json}"),
            usage_chunk(),
        ]
        events = run_turns([malformed_turn, {"text": "고치겠습니다."}])

        self.assertEqual(
            [], [e for e in events if e["kind"] == "generation_truncated"]
        )
        results = [e for e in events if e["kind"] == "tool_result"]
        self.assertEqual(1, len(results))
        self.assertNotIn("reason", results[0]["value"])
        self.assertIn("파싱 실패", results[0]["value"]["error"])


if __name__ == "__main__":
    unittest.main()
