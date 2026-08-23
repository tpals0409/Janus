from __future__ import annotations

import unittest

from scripts import compare_tasksuite_results as comparison
from scripts import run_tasksuite_v0 as harness


class TaskSuiteSkillHarnessTests(unittest.TestCase):
    def test_build_run_spec_exposes_lazy_skill_catalog(self):
        skill = {"skill_version_id": "skill_v1"}
        spec, budget = harness.build_run_spec("task_1", "none", None, [skill])
        self.assertIsNone(budget)
        self.assertEqual([skill], spec["skills"])

    def test_summary_and_comparison_report_skill_cost_and_load_rate(self):
        def run(loaded: int, tokens: int) -> dict:
            return {
                "task_id": "task_1", "policy": "none",
                "acceptance_passed": True, "policy_conformant": True,
                "wall_time_ms": 100, "tokens": {"prompt": 20, "completion": 5},
                "approval_requests": 0, "worker_count": 0,
                "skill_usage": {"available": 1, "loaded": loaded, "prompt_tokens": tokens},
            }

        row = harness.summarize([run(1, 12), run(0, 0)])[0]
        self.assertEqual(0.5, row["skill_load_rate"])
        self.assertEqual(6.0, row["skill_prompt_tokens_mean"])

        before = {"label": "none", "runs": [run(0, 0)]}
        after = {"label": "relevant", "runs": [run(1, 12)]}
        result = comparison.compare(before, after)
        self.assertEqual(1.0, result["rows"][0]["skill_load_rate_delta"])
        self.assertEqual(12.0, result["rows"][0]["candidate"]["skill_prompt_tokens_mean"])


if __name__ == "__main__":
    unittest.main()
