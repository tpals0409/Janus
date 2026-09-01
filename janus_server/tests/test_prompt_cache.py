"""서버 프롬프트 캐시(APC) 실측 배선 — cached_tokens 추출·이벤트 전파 테스트."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import agent
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient, FakeStream, text_chunk, usage_chunk


class PromptCacheTests(unittest.TestCase):
    def test_assemble_extracts_measured_cache_hits(self):
        stream = FakeStream([text_chunk("ok"), usage_chunk(cached_tokens=7)])
        _, _, usage, _ = agent._assemble(stream, lambda _kind, **_data: None)
        self.assertEqual(7, usage["cached_tokens"])
        self.assertEqual(1, usage["prompt_tokens"])  # 전체 프롬프트 수는 그대로

    def test_assemble_defaults_to_zero_without_apc_report(self):
        stream = FakeStream([text_chunk("ok"), usage_chunk()])
        _, _, usage, _ = agent._assemble(stream, lambda _kind, **_data: None)
        self.assertEqual(0, usage["cached_tokens"])

    def test_run_propagates_cached_tokens_on_usage_event(self):
        events: list[dict] = []
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceContext(
                Path(tmp), "task-apc", "workspace-apc", "dispatch-apc",
            )
            agent.run(
                client=FakeClient([[text_chunk("done"), usage_chunk(5)]]),
                model="fake", system_prompt="", task="probe cache",
                tool_names=[], workspace_context=workspace,
                approve=lambda _name, _args: True,
                emit=lambda kind, **data: events.append({"kind": kind, **data}),
            )
        usage_events = [e for e in events if e["kind"] == "usage"]
        self.assertEqual(1, len(usage_events))
        self.assertEqual(5, usage_events[0]["cached_tokens"])


if __name__ == "__main__":
    unittest.main()
