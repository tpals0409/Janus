"""재시도가 이전 실패를 알고 시작한다.

adaptive는 실패를 분류해 워커 토폴로지와 예산을 바꿔 놓지만, 그 판정이 모델에게
전달되지 않으면 재시도는 백지에서 시작해 같은 실패를 반복한다. retry 블록은
계산·영속만 되고 컨텍스트 어디에도 들어가지 않았다.
"""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server.routers import sessions


def snapshot(retry: dict | None) -> dict:
    dispatch = {
        "objective_snapshot": "테스트를 통과시킨다",
        "acceptance_snapshot": "pytest",
        "adaptive_decision": {"retry": retry} if retry is not None else {},
    }
    return sessions._task_context_snapshot(
        spec={"system_prompt": "orchestrate", "context_policy": None},
        dispatch=dispatch,
        workspace={"root_path": "/tmp/ws"},
        skills=[],
    )


class RetryContextTests(unittest.TestCase):
    def test_first_attempt_has_no_retry_block(self):
        result = snapshot(None)
        self.assertNotIn("PREVIOUS ATTEMPT FAILED", result["preamble"])
        self.assertEqual(
            [], [i for i in result["items"] if i["id"] == "retry_context"]
        )

    def test_verification_failure_reaches_the_model_with_a_usable_instruction(self):
        result = snapshot({
            "failure_type": "verification_failure",
            "evidence": "vr_1234",
            "strategy": "diagnose_then_repair",
            "previous_dispatch_id": "dispatch_prev",
        })
        preamble = result["preamble"]
        self.assertIn("PREVIOUS ATTEMPT FAILED", preamble)
        self.assertIn("verification_failure", preamble)
        self.assertIn("vr_1234", preamble)
        # 전략 이름이 아니라 실행 가능한 지시가 들어간다.
        self.assertIn("원인을 특정한 뒤", preamble)
        self.assertNotIn("diagnose_then_repair", preamble)

        item = next(i for i in result["items"] if i["id"] == "retry_context")
        self.assertEqual("verification_failure", item["detail"]["failure_type"])
        self.assertEqual("dispatch_prev", item["detail"]["previous_dispatch_id"])

    def test_every_strategy_adaptive_can_emit_carries_an_instruction(self):
        """새 전략을 추가하고 문구를 잊으면 모델은 무엇을 다르게 할지 못 듣는다."""
        from janus_server import adaptive
        from janus_server.budget import DEFAULT_BUDGET

        emitted = set()
        for failure_type, previous in (
            ("verification_failure", {"id": "d0", "status": "failed"}),
            ("budget_exhausted",
             {"id": "d0", "status": "failed", "budget_exhausted_reason": "dispatch:token_limit"}),
            ("timeout", {"id": "d0", "status": "failed", "error": "timed out"}),
            ("tool_error", {"id": "d0", "status": "failed", "error": "tool failed"}),
            ("runtime_failure", {"id": "d0", "status": "failed"}),
            ("cancelled", {"id": "d0", "status": "cancelled"}),
        ):
            runs = (
                [{"id": "vr_1", "status": "failed", "dispatch_id": "d0"}]
                if failure_type == "verification_failure" else []
            )
            decision = adaptive.decide(
                task={"title": "t", "objective": "o", "acceptance_command": "true"},
                base_profile={"worker_policy": "autonomous", "max_steps": 30,
                              "budget": DEFAULT_BUDGET},
                scheduler_snapshot={
                    "closed": False,
                    "resources": {"model_generation":
                                  {"cap": 3, "active": 0, "queued": 0}},
                },
                previous_dispatch=previous,
                verification_runs=runs,
            )
            strategy = decision["retry"]["strategy"]
            self.assertEqual(failure_type, decision["retry"]["failure_type"])
            emitted.add(strategy)

        missing = emitted - set(sessions.RETRY_STRATEGY_PROMPTS)
        self.assertEqual(set(), missing, f"문구 없는 재시도 전략: {missing}")

        for strategy in emitted:
            with self.subTest(strategy=strategy):
                preamble = snapshot({
                    "failure_type": "runtime_failure", "strategy": strategy,
                })["preamble"]
                self.assertIn(sessions.RETRY_STRATEGY_PROMPTS[strategy], preamble)

    def test_unknown_strategy_still_reports_the_failure(self):
        preamble = snapshot({
            "failure_type": "runtime_failure", "strategy": "unheard_of",
        })["preamble"]
        self.assertIn("PREVIOUS ATTEMPT FAILED", preamble)
        self.assertIn("runtime_failure", preamble)


if __name__ == "__main__":
    unittest.main()
