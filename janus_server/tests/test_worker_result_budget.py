"""반환 방향 핸드오프 예산 — 워커 보고 절단이 컨텍스트만 보호하고 영속은 보존."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import runtime
from tests.fakes import FakeClient
from tests.test_worker_recovery import control, make_orchestration, spawn


class WorkerResultBudgetTests(unittest.TestCase):
    def test_short_result_passes_through_unchanged(self):
        fake = FakeClient([{"text": "concise handoff"}])
        with tempfile.TemporaryDirectory() as tmp:
            orch = make_orchestration(fake, Path(tmp))
            wid = spawn(orch, name="w", task="investigate",
                        role="scout", tools=[], max_steps=2)["worker"]
            view = control(orch, "wait_worker")(wid, 5)
        self.assertEqual("concise handoff", view["result"])
        self.assertFalse(view["result_truncated"])
        self.assertEqual(len("concise handoff"), view["result_chars"])

    def test_verbose_result_is_bounded_with_head_and_tail_kept(self):
        verbose = "HEAD-SUMMARY " + ("x" * 20_000) + " TAIL-CONCLUSION"
        outcomes: list[dict] = []
        fake = FakeClient([{"text": verbose}])
        with tempfile.TemporaryDirectory() as tmp:
            orch = make_orchestration(
                fake, Path(tmp), on_worker_outcome=outcomes.append)
            wid = spawn(orch, name="w", task="investigate",
                        role="scout", tools=[], max_steps=2)["worker"]
            view = control(orch, "wait_worker")(wid, 5)

        self.assertTrue(view["result_truncated"])
        self.assertEqual(len(verbose), view["result_chars"])
        self.assertLessEqual(
            len(view["result"]), runtime.WORKER_RESULT_MAX_CHARS + 200
        )
        self.assertTrue(view["result"].startswith("HEAD-SUMMARY"))
        self.assertTrue(view["result"].endswith("TAIL-CONCLUSION"))
        self.assertIn("chars elided", view["result"])
        # 영속 훅은 전문을 받는다 — 절단은 모델 컨텍스트 전용.
        self.assertEqual(1, len(outcomes))
        self.assertEqual(verbose, outcomes[0]["result"])


if __name__ == "__main__":
    unittest.main()
