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
        csv_text = evaluation.export_csv(result)
        self.assertIn("baseline_success_rate", csv_text)
        # 계산해 둔 산포와 워커·메모리 평균이 CSV에도 나온다 — 없으면 델타 하나가
        # 노이즈인지 실재인지 CSV만 보고는 판단할 수 없다.
        for column in ("baseline_wall_stdev_ms", "candidate_tokens_stdev",
                       "baseline_worker_count_mean",
                       "candidate_memory_peak_bytes_mean"):
            self.assertIn(column, csv_text)
        self.assertIn("Verdict: **equivalent**", evaluation.export_markdown(result))

    def test_a_delta_inside_baseline_variance_is_not_a_regression(self):
        """실행이 흔들리는 구간에서 임계만 보면 노이즈가 회귀로 찍힌다."""
        noisy_baseline = report("baseline", {
            "bug": [(True, 100, 1000, 2), (True, 300, 1000, 2)],
        })
        candidate = report("candidate", {
            "bug": [(True, 150, 1000, 2), (True, 330, 1000, 2)],
        })
        result = evaluation.compare(noisy_baseline, candidate)

        self.assertEqual("equivalent", result["verdict"])
        self.assertEqual([], result["regressions"])
        # 버리지 않고 "산포 안"으로 보고한다.
        self.assertIn("wall_delta_pct",
                      {item["metric"] for item in result["within_noise"]})

    def test_a_delta_beyond_baseline_variance_is_still_a_regression(self):
        stable_baseline = report("baseline", {
            "bug": [(True, 100, 1000, 2), (True, 102, 1000, 2)],
        })
        candidate = report("candidate", {
            "bug": [(True, 300, 1000, 2), (True, 305, 1000, 2)],
        })
        result = evaluation.compare(stable_baseline, candidate)

        self.assertEqual("regression", result["verdict"])
        self.assertIn("wall_delta_pct",
                      {item["metric"] for item in result["regressions"]})

    def test_os_patch_level_does_not_invalidate_a_baseline(self):
        """platform.platform()은 빌드 번호를 품는다 — 마이너 업데이트 한 번에
        저장된 모든 baseline이 incomparable이 되면 안 된다."""
        before = report("baseline", {
            "bug": [(True, 100, 1000, 2), (True, 120, 1100, 2)],
        }, platform="macOS-26.6.2-arm64-arm-64bit")
        after = report("candidate", {
            "bug": [(True, 100, 1000, 2), (True, 120, 1100, 2)],
        }, platform="macOS-26.6.3-arm64-arm-64bit")

        result = evaluation.compare(before, after)
        self.assertEqual("equivalent", result["verdict"])
        self.assertEqual([], result["condition_mismatches"])

        # 아키텍처가 실제로 다르면 여전히 비교 불가다.
        other_arch = report("candidate", {
            "bug": [(True, 100, 1000, 2), (True, 120, 1100, 2)],
        }, platform="Linux-6.5.0-x86_64-with-glibc2.35")
        self.assertEqual(
            "incomparable_conditions",
            evaluation.compare(before, other_arch)["verdict"],
        )


if __name__ == "__main__":
    unittest.main()
