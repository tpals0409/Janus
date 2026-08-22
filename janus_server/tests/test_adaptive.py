"""Adaptive orchestration decisions are deterministic and evidence driven."""

from __future__ import annotations

import unittest

from janus_server import adaptive
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
            ("Investigate the cache invalidation cause", "investigation", "fixed_one", ["researcher"]),
            ("Refactor architecture across multiple files", "multi_file_refactor", "autonomous", ["researcher", "implementer", "verifier"]),
            ("Add pytest regression coverage", "test_heavy", "autonomous", ["implementer", "verifier"]),
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
        self.assertEqual(45, decision["effective"]["budget"]["dispatch"]["step_limit"])

    def test_cancelled_attempt_is_not_auto_retryable(self):
        decision = adaptive.decide(
            task={"title": "Continue", "objective": "Continue", "acceptance_command": "true"},
            base_profile=profile(), scheduler_snapshot=scheduler(),
            previous_dispatch={"id": "dispatch_1", "status": "cancelled", "error": "user cancelled"},
        )
        self.assertFalse(decision["retry"]["allowed"])
        self.assertEqual("manual_only", decision["retry"]["strategy"])


if __name__ == "__main__":
    unittest.main()
