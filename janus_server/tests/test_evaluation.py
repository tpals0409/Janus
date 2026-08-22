"""P4 Evaluation Lab comparison and export tests."""

from __future__ import annotations

import json
import unittest

from janus_server import evaluation


def report(label: str, task_runs: dict[str, list[tuple[bool, float, int, int]]], **conditions):
    runs = []
    for task_id, values in task_runs.items():
        for repeat, (passed, wall, tokens, interventions) in enumerate(values, 1):
            runs.append({
                "task_id": task_id, "repeat": repeat,
                "acceptance_passed": passed, "wall_time_ms": wall,
                "tokens": {"prompt": tokens - 10, "completion": 10, "total": tokens},
                "user_inputs": 1, "approval_requests": interventions - 1,
                "worker_count": int(label != "baseline"),
            })
    return {
        "label": label,
        "conditions": {
            "model": "qwen3.8-27b", "quantization": "4-bit MLX",
            "platform": "macOS-arm64", **conditions,
        },
        "runs": runs,
    }


class EvaluationTests(unittest.TestCase):
    def setUp(self):
        self.baseline = report("baseline", {
            "bug": [(True, 100, 1000, 2), (True, 120, 1100, 2)],
            "refactor": [(True, 200, 2000, 3), (True, 220, 2200, 3)],
        })

    def test_improvement_includes_success_variance_and_cost(self):
        candidate = report("candidate", {
            "bug": [(True, 80, 800, 2), (True, 90, 900, 2)],
            "refactor": [(True, 160, 1700, 3), (True, 180, 1800, 3)],
        })
        result = evaluation.compare(self.baseline, candidate)
        self.assertEqual("improved", result["verdict"])
        self.assertEqual(1.0, result["overall"]["baseline"]["success_rate"])
        self.assertGreater(result["overall"]["baseline"]["wall_stdev_ms"], 0)
        self.assertLess(result["overall"]["token_delta_pct"], 0)
        self.assertTrue(result["improvements"])

    def test_acceptance_drop_is_regression_even_when_faster(self):
        candidate = report("candidate", {
            "bug": [(True, 50, 500, 2), (False, 50, 500, 2)],
            "refactor": [(True, 80, 800, 3), (True, 90, 900, 3)],
        })
        result = evaluation.compare(self.baseline, candidate)
        self.assertEqual("regression", result["verdict"])
        self.assertIn("success_rate", {item["metric"] for item in result["regressions"]})

    def test_thresholds_and_condition_mismatch_are_explicit(self):
        slightly_slower = report("candidate", {
            "bug": [(True, 110, 1100, 2), (True, 130, 1200, 2)],
            "refactor": [(True, 220, 2200, 3), (True, 240, 2400, 3)],
        })
        accepted = evaluation.compare(
            self.baseline, slightly_slower,
            {"max_wall_regression_pct": 20, "max_token_regression_pct": 20},
        )
        self.assertEqual("equivalent", accepted["verdict"])

        other_hardware = report("candidate", {
            "bug": [(True, 80, 800, 2), (True, 90, 900, 2)],
            "refactor": [(True, 160, 1700, 3), (True, 180, 1800, 3)],
        }, platform="linux-x86_64")
        mismatch = evaluation.compare(self.baseline, other_hardware)
        self.assertEqual("incomparable_conditions", mismatch["verdict"])
        self.assertEqual("platform", mismatch["condition_mismatches"][0]["field"])

    def test_json_csv_and_markdown_exports(self):
        result = evaluation.compare(self.baseline, self.baseline)
        self.assertEqual("equivalent", result["verdict"])
        self.assertEqual("equivalent", json.loads(evaluation.export_json(result))["verdict"])
        self.assertIn("baseline_success_rate", evaluation.export_csv(result))
        self.assertIn("Verdict: **equivalent**", evaluation.export_markdown(result))


if __name__ == "__main__":
    unittest.main()
