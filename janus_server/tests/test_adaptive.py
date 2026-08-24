"""Adaptive orchestration decisions are deterministic and evidence driven."""

from __future__ import annotations

import unittest

from janus_server import adaptive
from janus_server.budget import normalize_budget
from janus_server.budget import DEFAULT_BUDGET


def scheduler(*, cap: int = 1, active: int = 0, queued: int = 0, closed: bool = False) -> dict:
    return {
        "closed": closed,
        "resources": {
            "model_generation": {"cap": cap, "active": active, "queued": queued}
        },
    }


def profile(*, worker_policy: str = "autonomous") -> dict:
    return {
        "worker_policy": worker_policy,
        "max_steps": 30,
        "budget": DEFAULT_BUDGET,
    }


class AdaptiveDecisionTests(unittest.TestCase):
    def test_task_classes_choose_distinct_worker_topologies(self):
        cases = (
            ("Fix a bug in one file", "single_file_bug", "none", []),
            ("Investigate the cache invalidation cause", "investigation", "fixed_one", ["scout"]),
            ("Refactor architecture across multiple files", "multi_file_refactor", "autonomous", ["scout", "implementer", "verifier"]),
            ("Build a full web service with backend and browser UI", "multi_component_build", "autonomous", ["scout", "implementer", "verifier"]),
            ("Add pytest regression coverage", "test_heavy", "autonomous", ["implementer", "verifier"]),
            ("Create an implementation plan and task breakdown", "planning", "fixed_one", ["planner"]),
            ("Create a UI wireframe", "visual_prototype", "fixed_one", ["prototyper"]),
            ("Repair the model server health check", "operations", "fixed_one", ["operator"]),
        )
        for objective, task_class, policy, roles in cases:
            with self.subTest(task_class=task_class):
                decision = adaptive.decide(
                    task={"title": objective, "objective": objective, "acceptance_command": "true"},
                    base_profile=profile(), scheduler_snapshot=scheduler(cap=3),
                )
                self.assertEqual(task_class, decision["task_class"])
                self.assertEqual(policy, decision["effective"]["worker_policy"])
                self.assertEqual(roles, decision["effective"]["worker_roles"])

    def test_mockup_workflow_routes_to_one_prototyper_without_keyword_guessing(self):
        decision = adaptive.decide(
            task={
                "title": "첫 화면을 만들어줘", "objective": "사용자가 확인할 화면",
                "acceptance_command": "true", "workflow_stage": "mockup",
            },
            base_profile=profile(), scheduler_snapshot=scheduler(cap=3),
        )
        self.assertEqual("visual_prototype", decision["task_class"])
        self.assertEqual(["prototyper"], decision["effective"]["worker_role_sequence"])
        self.assertEqual(1, decision["effective"]["budget"]["workers"]["total_limit"])

    def test_queue_pressure_suppresses_fanout_and_is_snapshotted(self):
        decision = adaptive.decide(
            task={"title": "Refactor", "objective": "Refactor multiple files", "acceptance_command": "pytest"},
            base_profile=profile(), scheduler_snapshot=scheduler(cap=2, active=2, queued=2),
        )
        self.assertEqual("none", decision["effective"]["worker_policy"])
        self.assertEqual([], decision["effective"]["worker_roles"])
        self.assertEqual(1, decision["effective"]["budget"]["workers"]["concurrent_limit"])
        self.assertEqual(2, decision["scheduler"]["model_generation"]["queued"])
        self.assertIn("queue:closed_or_saturated", decision["reasons"])

    def test_explicit_two_worker_investigation_preserves_sequential_fanout(self):
        decision = adaptive.decide(
            task={
                "title": "워커 2개를 배치해",
                "objective": "첫 번째는 README, 두 번째는 테스트 구성을 조사해",
                "acceptance_command": "true",
            },
            base_profile=profile(), scheduler_snapshot=scheduler(cap=1),
        )
        effective = decision["effective"]
        self.assertEqual("investigation", decision["task_class"])
        self.assertEqual("autonomous", effective["worker_policy"])
        self.assertEqual(["scout", "scout"], effective["worker_role_sequence"])
        self.assertEqual(2, effective["budget"]["workers"]["total_limit"])
        self.assertEqual(2, effective["budget"]["workers"]["concurrent_limit"])
        self.assertTrue(effective["allow_autonomous_workers"])
        self.assertEqual(16_384, effective["budget"]["worker"]["token_limit"])
        self.assertEqual(8, effective["budget"]["worker"]["step_limit"])
        self.assertIn("queue:single_generation_slot_sequential_workers", decision["reasons"])

    def test_multi_component_build_keeps_sequential_workers_on_one_model_slot(self):
        decision = adaptive.decide(
            task={
                "title": "Build ForgeBoard local-first web service",
                "objective": "Build the full service with backend, browser UI, and tests",
                "acceptance_command": "python benchmark_acceptance.py",
            },
            base_profile=profile(), scheduler_snapshot=scheduler(cap=1),
        )

        effective = decision["effective"]
        self.assertEqual("multi_component_build", decision["task_class"])
        self.assertEqual("autonomous", effective["worker_policy"])
        self.assertTrue(effective["allow_autonomous_workers"])
        self.assertEqual(4, effective["budget"]["workers"]["total_limit"])
        self.assertEqual(1, effective["budget"]["workers"]["concurrent_limit"])
        self.assertEqual(49_152, effective["budget"]["worker"]["token_limit"])
        self.assertEqual(1_200_000, effective["budget"]["worker"]["time_limit_ms"])
        self.assertEqual(12, effective["budget"]["worker"]["step_limit"])

    def test_requested_worker_count_supports_korean_and_english_order(self):
        self.assertEqual(2, adaptive.requested_worker_count({"title": "워커 2개를 배치해"}))
        self.assertEqual(3, adaptive.requested_worker_count({"objective": "spawn 3 workers"}))

    def test_verification_failure_uses_read_only_diagnosis_before_repair(self):
        previous = {"id": "dispatch_1", "status": "completed", "error": None}
        decision = adaptive.decide(
            task={"title": "Fix behavior", "objective": "Fix behavior", "acceptance_command": "true"},
            base_profile=profile(), scheduler_snapshot=scheduler(),
            previous_dispatch=previous,
            verification_runs=[{
                "id": "verification_1", "dispatch_id": "dispatch_1", "status": "failed",
            }],
        )
        self.assertEqual("verification_failure", decision["retry"]["failure_type"])
        self.assertEqual("diagnose_then_repair", decision["retry"]["strategy"])
        self.assertEqual(["verifier", "implementer"], decision["effective"]["worker_role_sequence"])
        self.assertEqual(1, decision["effective"]["budget"]["workers"]["concurrent_limit"])

    def test_budget_failure_expands_parent_budget_and_removes_worker_overhead(self):
        previous = {
            "id": "dispatch_1", "status": "failed", "error": "budget exhausted",
            "budget_exhausted_reason": "dispatch:step_limit",
        }
        decision = adaptive.decide(
            task={"title": "Continue", "objective": "Continue", "acceptance_command": "true"},
            base_profile=profile(), scheduler_snapshot=scheduler(cap=2),
            previous_dispatch=previous,
        )
        self.assertEqual("budget_exhausted", decision["retry"]["failure_type"])
        self.assertEqual("expanded_parent_budget", decision["retry"]["strategy"])
        self.assertEqual("none", decision["effective"]["worker_policy"])
        expanded = normalize_budget(None)["dispatch"]["step_limit"] * 3 // 2
        self.assertEqual(expanded, decision["effective"]["budget"]["dispatch"]["step_limit"])

    def test_cancelled_attempt_is_not_auto_retryable(self):
        decision = adaptive.decide(
            task={"title": "Continue", "objective": "Continue", "acceptance_command": "true"},
            base_profile=profile(), scheduler_snapshot=scheduler(),
            previous_dispatch={"id": "dispatch_1", "status": "cancelled", "error": "user cancelled"},
        )
        self.assertFalse(decision["retry"]["allowed"])
        self.assertEqual("manual_only", decision["retry"]["strategy"])


class ReadOnlyVocabularyTests(unittest.TestCase):
    """읽기 전용 요청을 놓치면 general로 떨어져 두 번째 worker가 즉시 거부된다."""

    def decide(self, text: str) -> dict:
        return adaptive.decide(
            task={"title": text, "objective": text, "acceptance_command": "true"},
            base_profile=profile(), scheduler_snapshot=scheduler(),
        )

    def test_common_korean_read_only_verbs_allow_the_requested_fanout(self):
        for text in (
            "감시 워커 2개 생성해서 현재 디렉토리 파악하자",
            "워커 2개로 구조를 살펴봐",
            "워커 2개로 코드베이스 탐색해",
        ):
            with self.subTest(text=text):
                decision = self.decide(text)
                self.assertEqual("investigation", decision["task_class"])
                self.assertEqual(
                    2,
                    decision["effective"]["budget"]["workers"]["concurrent_limit"],
                )

    def test_korean_numerals_count_as_an_explicit_worker_request(self):
        """한국어로는 "워커 2개"보다 "워커 두개"라고 더 자주 쓴다."""
        for text, expected in (
            ("워커 두개 배치해서, 이 프로젝트 조사해", 2),
            ("감시 워커 세개 만들어", 3),
            ("워커 2개 배치해", 2),
            ("spawn 3 workers", 3),
            ("워커 없이 직접 해줘", None),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    expected, adaptive.requested_worker_count({"objective": text})
                )

    def test_mutating_and_test_requests_are_not_reclassified(self):
        self.assertEqual("general", self.decide("README 오타 수정해줘")["task_class"])
        self.assertEqual("test_heavy", self.decide("테스트 돌려서 검증해")["task_class"])


if __name__ == "__main__":
    unittest.main()
