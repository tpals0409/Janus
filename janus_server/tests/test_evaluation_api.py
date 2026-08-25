"""Evaluation Lab persistence, comparison, and export API tests."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import server
from janus_server.routers import evaluations


def report(label: str, passed: list[bool], wall: int, tokens: int) -> dict:
    return {
        "label": label,
        "conditions": {
            "model": "qwen3.8-27b", "quantization": "4-bit MLX",
            "platform": "macOS-arm64",
            "agent_profile": {"id": f"profile-{label}", "worker_policy": label},
        },
        "runs": [
            {
                "task_id": task_id, "repeat": repeat,
                "acceptance_passed": success, "wall_time_ms": wall + repeat,
                "tokens": {"prompt": tokens - 10, "completion": 10, "total": tokens},
                "user_inputs": 1, "approval_requests": 1, "worker_count": 0,
            }
            for task_id in ("bug", "refactor")
            for repeat, success in enumerate(passed, 1)
        ],
    }


class EvaluationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {
            "JANUS_DB_FILE": f"{self.temp.name}/janus.sqlite3",
            "JANUS_EVALUATIONS_DIR": f"{self.temp.name}/evaluations",
        })
        self.env.start()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        with server._EVALUATION_JOBS_LOCK:
            server._EVALUATION_JOBS.clear()
            server._EVALUATION_PROCESSES.clear()
            server._EVALUATION_CANCELLED.clear()
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}

    def tearDown(self):
        self.client.close()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        with server._EVALUATION_JOBS_LOCK:
            server._EVALUATION_JOBS.clear()
            server._EVALUATION_PROCESSES.clear()
            server._EVALUATION_CANCELLED.clear()
        self.env.stop()
        self.temp.cleanup()

    def _import(self, role: str, value: dict) -> dict:
        response = self.client.post(
            "/evaluations/experiments/import", headers=self.headers,
            json={"role": role, "report": value},
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_import_compare_persist_and_export_all_formats(self):
        baseline = self._import("baseline", report("baseline", [True, True], 100, 1000))
        candidate = self._import("candidate", report("candidate", [True, True], 75, 800))
        compared = self.client.post(
            "/evaluations/comparisons", headers=self.headers, json={
                "baseline_id": baseline["id"], "candidate_id": candidate["id"],
                "thresholds": {"max_wall_regression_pct": 10},
            },
        )
        self.assertEqual(200, compared.status_code, compared.text)
        comparison = compared.json()
        self.assertEqual("improved", comparison["result"]["verdict"])
        self.assertEqual(2, len(comparison["result"]["rows"]))
        self.assertEqual(2, len(self.client.get(
            "/evaluations/experiments", headers=self.headers
        ).json()))
        self.assertEqual(1, len(self.client.get(
            "/evaluations/comparisons", headers=self.headers
        ).json()))

        for format_name, fragment in (
            ("json", '"verdict": "improved"'),
            ("csv", "baseline_success_rate"),
            ("markdown", "Verdict: **improved**"),
        ):
            exported = self.client.get(
                f"/evaluations/comparisons/{comparison['id']}/export?format={format_name}",
                headers=self.headers,
            )
            self.assertEqual(200, exported.status_code, exported.text)
            self.assertIn(fragment, exported.text)
            self.assertIn("attachment", exported.headers["content-disposition"])

    def test_failed_acceptance_cannot_be_hidden_by_lower_cost(self):
        baseline = self._import("baseline", report("baseline", [True, True], 100, 1000))
        candidate = self._import("candidate", report("candidate", [True, False], 20, 100))
        compared = self.client.post(
            "/evaluations/comparisons", headers=self.headers, json={
                "baseline_id": baseline["id"], "candidate_id": candidate["id"],
            },
        ).json()
        self.assertEqual("regression", compared["result"]["verdict"])
        self.assertIn(
            "success_rate",
            {item["metric"] for item in compared["result"]["regressions"]},
        )

    def test_runner_snapshots_profile_prompt_budget_model_and_task_config(self):
        with patch.object(evaluations, "_start_evaluation_job") as start:
            response = self.client.post(
                "/evaluations/experiments/run", headers=self.headers, json={
                    "role": "candidate", "label": "profile-candidate",
                    "agent_profile_id": "agent_default", "repeats": 2,
                    "tasks": ["single_file_bug"], "turn_timeout_seconds": 60,
                    "model_startup_timeout_seconds": 90,
                },
            )
        self.assertEqual(202, response.status_code, response.text)
        item = response.json()
        self.assertEqual("queued", item["status"])
        self.assertEqual("runner", item["source"])
        self.assertEqual("qwen3.8-27b", item["profile_snapshot"]["model_key"])
        self.assertEqual("4-bit MLX", item["profile_snapshot"]["quantization"])
        self.assertTrue(item["profile_snapshot"]["system_prompt"])
        self.assertIn("dispatch", item["profile_snapshot"]["budget"])
        self.assertEqual(["single_file_bug"], item["config"]["tasks"])
        self.assertEqual(2, item["config"]["repeats"])
        start.assert_called_once_with(item["id"])

    def test_runner_job_persists_completed_report_without_losing_snapshot(self):
        with patch.object(evaluations, "_start_evaluation_job"):
            item = self.client.post(
                "/evaluations/experiments/run", headers=self.headers, json={
                    "role": "baseline", "label": "runner-baseline",
                    "agent_profile_id": "agent_default", "repeats": 1,
                    "tasks": ["single_file_bug"], "turn_timeout_seconds": 60,
                    "model_startup_timeout_seconds": 90,
                },
            ).json()

        class FakeProcess:
            returncode = 0

            def __init__(self, command, **_kwargs):
                output = command[command.index("--output-dir") + 1]
                os.makedirs(output)
                value = report("runner-baseline", [True], 50, 500)
                with open(f"{output}/result.json", "w", encoding="utf-8") as handle:
                    json.dump(value, handle)

            def communicate(self):
                return ("runner complete\n", None)

            def poll(self):
                return self.returncode

            def send_signal(self, _signal):
                return None

        with patch.object(evaluations.subprocess, "Popen", FakeProcess):
            evaluations._run_evaluation_job(item["id"])

        completed = self.client.get(
            f"/evaluations/experiments/{item['id']}", headers=self.headers
        ).json()
        self.assertEqual("completed", completed["status"])
        self.assertEqual("macOS-arm64", completed["conditions"]["platform"])
        self.assertEqual("qwen3.8-27b", completed["profile_snapshot"]["model_key"])
        self.assertTrue(completed["result_path"].endswith("result.json"))
        self.assertFalse(os.path.exists(
            f"{self.temp.name}/evaluations/.{item['id']}-profile.json"
        ))

    def test_improved_runner_candidate_can_be_promoted_with_provenance(self):
        store = server.get_domain_store()
        project = store.create_project(name="Promoted", repo_path=f"{self.temp.name}/repo")
        candidate_profile = store.create_agent_profile(
            name="Measured candidate", system_prompt="Measured candidate",
            tools=["read_file"], worker_policy="none", max_steps=12,
            model_profile_id="model_qwen38_27b_4bit",
        )
        baseline_report = report("baseline", [True, True], 100, 1000)
        candidate_report = report("candidate", [True, True], 70, 800)
        baseline = store.create_evaluation_experiment(
            role="baseline", label="baseline", source="runner", status="completed",
            agent_profile_id="agent_default", report=baseline_report,
            conditions=baseline_report["conditions"],
        )
        candidate = store.create_evaluation_experiment(
            role="candidate", label="candidate", source="runner", status="completed",
            agent_profile_id=candidate_profile["id"], report=candidate_report,
            conditions=candidate_report["conditions"],
        )
        comparison = self.client.post(
            "/evaluations/comparisons", headers=self.headers, json={
                "baseline_id": baseline["id"], "candidate_id": candidate["id"],
            },
        ).json()
        promoted = self.client.post(
            f"/projects/{project['id']}/agent-profile/promote",
            headers=self.headers, json={"comparison_id": comparison["id"]},
        )
        self.assertEqual(200, promoted.status_code, promoted.text)
        payload = promoted.json()
        self.assertEqual(candidate_profile["id"], payload["agent_profile"]["id"])
        self.assertEqual("improved", payload["verdict"])
        saved = self.client.get(
            f"/projects/{project['id']}", headers=self.headers
        ).json()
        self.assertEqual(candidate_profile["id"], saved["default_agent_profile_id"])
        self.assertEqual(comparison["id"], saved["promoted_comparison_id"])
        self.assertIsNotNone(saved["profile_promoted_at"])

    def test_regression_and_unlinked_import_cannot_be_promoted(self):
        store = server.get_domain_store()
        project = store.create_project(name="Guarded", repo_path=f"{self.temp.name}/guarded")
        baseline = self._import("baseline", report("baseline", [True, True], 100, 1000))
        regression = self._import("candidate", report("regression", [True, False], 50, 500))
        rejected_comparison = self.client.post(
            "/evaluations/comparisons", headers=self.headers, json={
                "baseline_id": baseline["id"], "candidate_id": regression["id"],
            },
        ).json()
        rejected = self.client.post(
            f"/projects/{project['id']}/agent-profile/promote",
            headers=self.headers, json={"comparison_id": rejected_comparison["id"]},
        )
        self.assertEqual(409, rejected.status_code)

        improved_import = self._import(
            "candidate", report("import-improved", [True, True], 50, 500)
        )
        unlinked_comparison = self.client.post(
            "/evaluations/comparisons", headers=self.headers, json={
                "baseline_id": baseline["id"], "candidate_id": improved_import["id"],
            },
        ).json()
        unlinked = self.client.post(
            f"/projects/{project['id']}/agent-profile/promote",
            headers=self.headers, json={"comparison_id": unlinked_comparison["id"]},
        )
        self.assertEqual(409, unlinked.status_code)


if __name__ == "__main__":
    unittest.main()
