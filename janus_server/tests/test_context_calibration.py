"""토큰 실측 기반 컨텍스트 압축 — chars/token 보정 회귀 테스트."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import agent
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient


def fill(session: agent.Session, blocks: int, chunk: str) -> None:
    for index in range(blocks):
        session.append("user", content=f"request {index} " + chunk)
        session.append("assistant", content=f"result {index} " + chunk)


class ContextCalibrationTests(unittest.TestCase):
    def test_uncalibrated_threshold_matches_configured_chars(self):
        session = agent.Session("system", context_max_chars=4_000)
        self.assertEqual(1_000, session.context_token_target)
        self.assertEqual(4_000, session.effective_max_chars())
        self.assertEqual(0, session.token_calibration_samples)
        self.assertIsNone(
            agent.Session("system", context_max_chars=None).effective_max_chars()
        )

    def test_measured_ratio_tightens_compaction_before_overflow(self):
        """한국어처럼 chars/token이 낮으면 같은 chars에서 더 일찍 압축해야 한다."""
        session = agent.Session(
            "system", context_max_chars=8_000, context_recent_blocks=2,
        )
        fill(session, blocks=8, chunk="본문 " * 100)
        session.derive_messages()
        uncalibrated = dict(session.context_stats)
        self.assertFalse(uncalibrated["compacted"])

        # 실측: 같은 chars가 4자/토큰 가정의 2배 토큰이었다 → 비율 2.0
        sent = uncalibrated["sent_chars"]
        session.observe_usage(sent, sent // 2)
        self.assertAlmostEqual(2.0, session.chars_per_token)
        self.assertEqual(4_000, session.effective_max_chars())

        session.derive_messages()
        calibrated = session.context_stats
        self.assertTrue(calibrated["compacted"])
        self.assertLess(calibrated["sent_chars"], uncalibrated["sent_chars"])

    def test_bad_usage_reports_are_clamped_or_ignored(self):
        session = agent.Session("system")
        session.observe_usage(0, 100)
        session.observe_usage(100, 0)
        session.observe_usage(-5, -5)
        self.assertEqual(0, session.token_calibration_samples)
        self.assertEqual(agent.HEURISTIC_CHARS_PER_TOKEN, session.chars_per_token)

        session.observe_usage(10_000, 1)  # 극단값은 상한으로 클램프
        self.assertEqual(agent.CHARS_PER_TOKEN_BOUNDS[1], session.chars_per_token)
        session.observe_usage(1, 10_000)  # 하한 클램프가 EMA로 섞인다
        self.assertLess(session.chars_per_token, agent.CHARS_PER_TOKEN_BOUNDS[1])
        self.assertGreaterEqual(
            session.chars_per_token, agent.CHARS_PER_TOKEN_BOUNDS[0]
        )
        self.assertEqual(2, session.token_calibration_samples)

    def test_context_stats_report_calibrated_estimates(self):
        session = agent.Session("system", context_max_chars=8_000)
        session.append("user", content="측정 대상 " * 50)
        session.observe_usage(1_000, 500)  # 비율 2.0
        session.derive_messages()
        stats = session.context_stats
        self.assertEqual(2.0, stats["chars_per_token"])
        self.assertEqual(1, stats["token_calibration_samples"])
        self.assertEqual(2_000, stats["context_token_target"])
        self.assertEqual(
            stats["sent_chars"] // 2, stats["sent_token_estimate"]
        )

    def test_run_feeds_measured_usage_into_calibration(self):
        session = agent.Session("system")
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceContext(
                Path(tmp), "task-cal", "workspace-cal", "dispatch-cal",
            )
            agent.run(
                client=FakeClient([{"text": "done"}]), session=session,
                model="fake", system_prompt="", task="calibrate",
                tool_names=[], workspace_context=workspace,
                approve=lambda _name, _args: True,
                emit=lambda _kind, **_data: None,
            )
        # fakes.usage_chunk는 prompt_tokens=1을 보고한다 → 상한으로 클램프
        self.assertEqual(1, session.token_calibration_samples)
        self.assertEqual(
            agent.CHARS_PER_TOKEN_BOUNDS[1], session.chars_per_token
        )


if __name__ == "__main__":
    unittest.main()
