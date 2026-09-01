"""컨텍스트 회계와 프롬프트 위생 — 로컬 소형 모델의 prefill을 정직하게 센다."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import agent, runtime
from janus_server import tools as T


class ToolSchemaAccountingTests(unittest.TestCase):
    def test_schemas_count_against_the_compaction_threshold(self):
        """스키마는 매 요청에 함께 나간다 — 회계에서 빠지면 임계가 실제 prefill의
        상당 부분을 못 본다."""
        session = agent.Session("system", context_max_chars=2_000)
        session.append("user", content="x" * 400)
        session.derive_messages()
        without = session.context_stats["sent_chars"]
        self.assertEqual(0, session.context_stats["tool_schema_chars"])

        session.observe_tool_schemas(T.schemas_for(["read_file", "grep", "glob"]))
        session.derive_messages()
        with_schemas = session.context_stats["sent_chars"]

        self.assertGreater(session.context_stats["tool_schema_chars"], 0)
        self.assertEqual(
            without + session.context_stats["tool_schema_chars"], with_schemas
        )

    def test_a_single_oversized_block_is_shrunk_not_sent_whole(self):
        """병렬 도구 호출 한 덩어리가 임계의 두 배여도 그대로 나가면 안 된다."""
        session = agent.Session("system", context_max_chars=4_000)
        session.append("user", content="do the thing")
        session.append("assistant", content="", tool_calls=[
            {"id": f"c{i}", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}
            for i in range(5)
        ])
        for i in range(5):
            session.append("tool_result", tool_call_id=f"c{i}", name="read_file",
                           value={"content": "y" * 6_000, "offset": 0,
                                  "total_lines": 1, "has_more": False})

        messages = session.derive_messages()
        sent = session._chars(messages)
        self.assertLessEqual(sent, session.effective_max_chars())
        # tool call과 result의 짝은 그대로 유지된다 — 서버가 거부하지 않는다.
        tool_messages = [m for m in messages if m["role"] == "tool"]
        self.assertEqual(5, len(tool_messages))
        self.assertTrue(
            any("elided to fit the context window" in m["content"]
                for m in tool_messages)
        )


class PromptHygieneTests(unittest.TestCase):
    def test_personas_do_not_cite_bundled_paths_the_model_cannot_read(self):
        """번들 본문은 같은 프롬프트에 이미 인라인돼 있다.

        경로를 인용하면 소형 모델이 워크스페이스에 없는 파일을 read_file 한다 —
        스텝 하나와 컨텍스트를 버리는 실패다.
        """
        for role in ("janus", "scout", "planner", "prototyper",
                     "implementer", "verifier", "operator"):
            with self.subTest(role=role):
                prompt = runtime.persona_prompt(role)
                self.assertNotIn("personas/", prompt)
                self.assertNotIn("SKILL.md", prompt)

    def test_every_persona_including_janus_has_a_size_ceiling(self):
        """상한이 없으면 프롬프트가 조용히 커져도 아무도 모른다."""
        for role, ceiling in (
            ("janus", runtime.ORCHESTRATOR_SYSTEM_MAX_CHARS),
            ("scout", runtime.WORKER_SYSTEM_MAX_CHARS),
            ("implementer", runtime.WORKER_SYSTEM_MAX_CHARS),
        ):
            with self.subTest(role=role):
                self.assertLessEqual(len(runtime.persona_prompt(role)), ceiling)

        with self.assertRaises(RuntimeError):
            runtime.persona_prompt(
                "janus",
                custom_prompt="z" * (runtime.ORCHESTRATOR_SYSTEM_MAX_CHARS + 1),
            )


class ReadFileRenderTests(unittest.TestCase):
    def test_line_numbers_orient_the_model_and_paging_is_explicit(self):
        rendered = T.render("read_file", {
            "path": "a.py", "content": "alpha\nbeta\n", "offset": 10,
            "limit": 2, "total_lines": 40, "has_more": True,
        })
        self.assertIn("    11  alpha", rendered)
        self.assertIn("    12  beta", rendered)
        self.assertIn("offset=12", rendered)

    def test_a_complete_read_does_not_advertise_more(self):
        rendered = T.render("read_file", {
            "path": "a.py", "content": "only\n", "offset": 0,
            "limit": 400, "total_lines": 1, "has_more": False,
        })
        self.assertIn("     1  only", rendered)
        self.assertNotIn("offset=", rendered)


if __name__ == "__main__":
    unittest.main()
