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


class PrefixCacheTests(unittest.TestCase):
    def build(self) -> agent.Session:
        session = agent.Session(
            "SYSTEM PROMPT", context_max_chars=3_000, context_recent_blocks=2,
        )
        for index in range(12):
            session.append("user", content=f"request {index} " + "본문 " * 60)
            session.append("assistant", content=f"result {index} " + "본문 " * 60)
        return session

    def test_compaction_keeps_the_system_prompt_byte_identical(self):
        """요약을 system에 붙이면 압축이 돌 때마다 KV prefix 캐시가 무효가 된다.

        로컬에선 prefill이 지연의 지배 항이라, 컨텍스트를 줄이려는 최적화가
        캐시를 통째로 버리는 자기모순이었다.
        """
        session = self.build()
        messages = session.derive_messages()
        self.assertTrue(session.context_stats["compacted"])
        self.assertEqual("SYSTEM PROMPT", messages[0]["content"])

        # 요약은 system 뒤 별도 메시지로 실린다.
        self.assertIn(agent.SUMMARY_ENVELOPE.strip(), messages[1]["content"])

    def test_prefix_hash_survives_further_compaction(self):
        session = self.build()
        session.derive_messages()
        first = session.context_stats["prefix_hash"]

        # 대화가 더 자라 요약 내용이 바뀌어도 안정 prefix는 그대로다.
        for index in range(6):
            session.append("user", content=f"more {index} " + "본문 " * 60)
            session.append("assistant", content=f"done {index} " + "본문 " * 60)
        session.derive_messages()

        self.assertEqual(first, session.context_stats["prefix_hash"])
        self.assertTrue(session.context_stats["prefix_reused"])
        self.assertEqual(
            len("SYSTEM PROMPT"), session.context_stats["cache_candidate_chars"]
        )

    def test_measured_cache_hits_sit_next_to_the_probe(self):
        """접두사를 지켰는지(prefix_reused)와 서버가 실제로 재사용했는지는 다르다."""
        session = agent.Session("system", context_max_chars=None)
        session.append("user", content="hello")
        session.derive_messages()
        self.assertEqual(0.0, session.context_stats["last_cache_hit_ratio"])

        session.observe_usage(1_000, 500, 400)
        session.derive_messages()
        self.assertEqual(0.8, session.context_stats["last_cache_hit_ratio"])

        # APC 미지원 서버는 0으로 보고된다 — "지켰는데 헛수고"가 그대로 보인다.
        session.observe_usage(1_000, 500, 0)
        session.derive_messages()
        self.assertEqual(0.0, session.context_stats["last_cache_hit_ratio"])


class SummaryGranularityTests(unittest.TestCase):
    def summarize_with(self, tool_name: str) -> str:
        session = agent.Session("system")
        block = [
            {"role": "assistant", "content": "", "tool_calls": [{
                "id": "c1", "type": "function",
                "function": {"name": tool_name, "arguments": "{}"},
            }]},
            {"role": "tool", "tool_call_id": "c1", "content": "Z" * 3_000},
        ]
        return session._project_summary([block])

    def test_discovery_results_keep_more_than_write_results(self):
        """탐색 결과를 쓰기 결과와 같은 길이로 접으면 모델이 같은 파일을 다시 읽는다."""
        discovery = self.summarize_with("read_file")
        action = self.summarize_with("write_file")

        self.assertGreater(len(discovery), len(action))
        self.assertIn("Z" * agent.DEFAULT_SUMMARY_CHARS, action)
        self.assertIn("Z" * agent.DISCOVERY_SUMMARY_CHARS, discovery)
        # 둘 다 상한은 지킨다 — 차등이지 무제한이 아니다.
        self.assertNotIn("Z" * (agent.DISCOVERY_SUMMARY_CHARS + 1), discovery)


class SkillCatalogTests(unittest.TestCase):
    """카탈로그는 매 요청 prefill에 고정으로 얹힌다 — 상한이 없으면 대화가 밀린다."""

    def catalog(self, snapshots: list[dict]) -> str:
        orchestration = object.__new__(runtime.Orchestration)
        orchestration.skill_snapshots = snapshots
        return runtime.Orchestration._skill_catalog_prompt(orchestration)

    def skill(self, index: int, **overrides) -> dict:
        item = {
            "namespace": "ns", "name": f"skill{index}",
            "activation_mode": "auto",
            "description": "D" * 900,
            "compiled": {"activation": {"model_invocable": True}},
        }
        item.update(overrides)
        return item

    def test_descriptions_are_truncated_and_entries_are_capped(self):
        many = [self.skill(i) for i in range(runtime.SKILL_CATALOG_MAX_ENTRIES + 5)]
        text = self.catalog(many)

        self.assertNotIn("D" * (runtime.SKILL_CATALOG_DESCRIPTION_CHARS + 1), text)
        listed = [line for line in text.splitlines() if line.startswith("- ns:")]
        self.assertEqual(runtime.SKILL_CATALOG_MAX_ENTRIES, len(listed))
        self.assertIn("and 5 more", text)

    def test_auto_skills_survive_the_cap_before_manual_ones(self):
        snapshots = [
            self.skill(i, activation_mode="manual")
            for i in range(runtime.SKILL_CATALOG_MAX_ENTRIES)
        ] + [self.skill(99, activation_mode="auto")]
        text = self.catalog(snapshots)
        self.assertIn("ns:skill99", text)

    def test_skills_the_model_cannot_invoke_are_not_advertised(self):
        text = self.catalog([
            self.skill(1, compiled={"activation": {"model_invocable": False}}),
            self.skill(2),
        ])
        self.assertNotIn("ns:skill1", text)
        self.assertIn("ns:skill2", text)

    def test_activation_paths_reach_the_prompt(self):
        """컴파일만 되고 죽어 있던 필드 — 언제 쓰는 스킬인지 알려주는 유일한 신호다."""
        text = self.catalog([self.skill(1, compiled={
            "activation": {"model_invocable": True, "paths": ["src/**/*.py"]},
        })])
        self.assertIn("applies to: src/**/*.py", text)


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
