"""TaskSuite v0 manifest와 fixture 격리 계약."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from scripts.compare_tasksuite_results import compare
from scripts.publish_tasksuite_summary import public_report
from scripts.run_tasksuite_v0 import build_run_spec, efficiency_summary


SUITE = Path(__file__).parents[1] / "tasksuite" / "v0"


class TaskSuiteTests(unittest.TestCase):
    def test_agent_profile_snapshot_controls_prompt_policy_model_tools_and_budget(self):
        profile = {
            "id": "candidate", "name": "Candidate", "model_key": "qwen3.8-27b",
            "quantization": "4-bit MLX", "system_prompt": "candidate prompt",
            "tools": ["read_file", "edit_file"], "approval": "ask",
            "worker_policy": "autonomous", "max_steps": 12,
            "budget": {"dispatch": {"step_limit": 12}},
        }
        spec, budget = build_run_spec("bug", "autonomous", profile)
        self.assertEqual("qwen3.8-27b", spec["model"])
        self.assertEqual(["read_file", "edit_file"], spec["tools"])
        self.assertTrue(spec["allow_autonomous_workers"])
        self.assertIn("candidate prompt", spec["system_prompt"])
        self.assertEqual(profile["budget"], budget)

    def test_efficiency_summary_keeps_context_and_backpressure_metrics(self):
        summary = efficiency_summary({"events": [
            {"kind": "context_window", "baseline_chars": 100, "sent_chars": 60,
             "saved_chars": 40, "saved_token_estimate": 10, "compacted": True},
            {"kind": "prompt_cache_probe", "prefix_reused": True},
            {"kind": "worker_spawn_suppressed", "reason": "model_queue_backpressure"},
        ]})
        self.assertEqual(40, summary["saved_input_chars"])
        self.assertEqual(10, summary["saved_token_estimate"])
        self.assertEqual(1, summary["compacted_calls"])
        self.assertEqual(1, summary["stable_prefix_reuses"])
        self.assertEqual(
            {"model_queue_backpressure": 1},
            summary["worker_spawn_suppression_reasons"],
        )

    def test_comparison_flags_acceptance_regression(self):
        def report(successes: list[bool], wall: float) -> dict:
            return {"runs": [{
                "task_id": "task", "policy": "none", "acceptance_passed": success,
                "wall_time_ms": wall, "tokens": {"prompt": 100, "completion": 10},
            } for success in successes]}

        result = compare(report([True, True], 100), report([True, False], 90))
        self.assertEqual("acceptance_regression", result["verdict"])
        self.assertEqual(["task/none"], result["acceptance_regressions"])

    def test_public_report_removes_paths_and_detailed_payloads(self):
        report = public_report({
            "suite": "suite", "conditions": {"model_path": "/Users/me/model"},
            "model_server": {"pid": 123, "orphan_processes": 0},
            "runs": [{
                "task_id": "task", "policy": "none", "repeat": 1,
                "acceptance_passed": True, "wall_time_ms": 10,
                "tokens": {"prompt": 1, "completion": 1},
                "acceptance": {"workspace_root": "/Users/me/work"},
                "telemetry": {"events": ["private"]}, "reply": "private",
            }],
        })
        encoded = json.dumps(report)
        self.assertNotIn("/Users/", encoded)
        self.assertNotIn("workspace_root", encoded)
        self.assertNotIn("telemetry", encoded)
        self.assertNotIn("reply", encoded)
        self.assertEqual(0, report["model_server"]["orphan_processes"])

    def test_manifest_has_three_fixed_task_shapes(self):
        manifest = json.loads((SUITE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(5, manifest["repeats"])
        self.assertEqual(
            {"single_file_bug", "multi_file_refactor", "investigate_code_tests"},
            {task["id"] for task in manifest["tasks"]},
        )
        for task in manifest["tasks"]:
            self.assertTrue(task["objective"])
            self.assertTrue(task["constraints"])
            self.assertTrue(task["acceptance_command"])
            self.assertTrue(task["required_changed_files"])
            self.assertTrue((SUITE / "fixtures" / task["id"]).is_dir())

    def test_every_pristine_fixture_fails_its_acceptance(self):
        manifest = json.loads((SUITE / "manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            for task in manifest["tasks"]:
                workspace = Path(tmp) / task["id"]
                shutil.copytree(SUITE / "fixtures" / task["id"], workspace)
                result = subprocess.run(
                    task["acceptance_command"], cwd=workspace, shell=True,
                    capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(0, result.returncode, task["id"])


if __name__ == "__main__":
    unittest.main()
