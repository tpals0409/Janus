"""P3 Verification Runner API and independent-result tests."""

from __future__ import annotations

import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import scheduler, server


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=True
    )


class VerificationApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "janus@example.test")
        git(self.repo, "config", "user.name", "Janus Test")
        (self.repo / "README.md").write_text("Janus\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        self.env = patch.dict(os.environ, {
            "JANUS_DB_FILE": str(root / "janus.sqlite3"),
            "JANUS_WORKTREES_DIR": str(root / "workspaces"),
        })
        self.env.start()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        server._WORKSPACE_SERVICE = None
        server._WORKSPACE_SERVICE_PATH = None
        scheduler._DEFAULT_SCHEDULER = scheduler.ResourceScheduler()
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}

        project = self.client.post("/projects", headers=self.headers, json={
            "name": "Verification", "repo_path": str(self.repo),
        }).json()
        self.project_id = project["id"]
        executable = shlex.quote(sys.executable)
        self.acceptance = f'{executable} -c "print(\'acceptance ok\')"'
        task = self.client.post(
            f"/projects/{self.project_id}/tasks", headers=self.headers, json={
                "title": "Verify", "objective": "Run independent checks",
                "acceptance_command": self.acceptance, "base_ref": "main",
            },
        ).json()
        self.task_id = task["id"]
        response = self.client.post(
            f"/tasks/{self.task_id}/workspace/prepare", headers=self.headers
        )
        self.assertEqual(202, response.status_code, response.text)
        self._wait_workspace()

    def tearDown(self):
        with server._VERIFICATION_JOBS_LOCK:
            threads = list(server._VERIFICATION_JOBS.values())
        for thread in threads:
            thread.join(timeout=5)
        self.client.close()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        server._WORKSPACE_SERVICE = None
        server._WORKSPACE_SERVICE_PATH = None
        self.env.stop()
        self.temp.cleanup()

    def _wait_workspace(self):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            item = self.client.get(
                f"/tasks/{self.task_id}/workspace", headers=self.headers
            ).json()
            if item["state"] == "ready" and not item["job_active"]:
                return item
            time.sleep(0.01)
        self.fail("workspace did not become ready")

    def _wait_runs(self, count: int) -> list[dict]:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            runs = self.client.get(
                f"/tasks/{self.task_id}/verifications", headers=self.headers
            ).json()
            if len(runs) >= count and all(
                item["status"] not in {"queued", "running"} for item in runs[:count]
            ):
                return runs
            time.sleep(0.02)
        self.fail(f"verification runs did not settle: {runs}")

    def test_project_commands_results_claim_separation_and_manual_rerun(self):
        executable = shlex.quote(sys.executable)
        configured = self.client.put(
            f"/projects/{self.project_id}/verification-commands",
            headers=self.headers,
            json={"commands": [
                {"kind": "test", "command": f'{executable} -c "print(\'tests ok\')"'},
                {"kind": "lint", "command": f'{executable} -c "import sys; print(\'lint failed\', file=sys.stderr); sys.exit(7)"'},
            ]},
        )
        self.assertEqual(200, configured.status_code, configured.text)
        self.assertEqual(2, len(configured.json()["verification_commands"]))

        started = self.client.post(
            f"/tasks/{self.task_id}/verifications", headers=self.headers,
            json={"trigger": "agent", "agent_claim": "passed"},
        )
        self.assertEqual(202, started.status_code, started.text)
        self.assertEqual(3, len(started.json()))
        runs = self._wait_runs(3)
        by_kind = {item["kind"]: item for item in runs[:3]}
        self.assertEqual("passed", by_kind["acceptance"]["status"])
        self.assertEqual(0, by_kind["test"]["exit_code"])
        self.assertIn("tests ok", by_kind["test"]["stdout"])
        self.assertEqual("failed", by_kind["lint"]["status"])
        self.assertEqual(7, by_kind["lint"]["exit_code"])
        self.assertIn("lint failed", by_kind["lint"]["stderr"])
        self.assertEqual("passed", by_kind["lint"]["agent_claim"])
        self.assertGreaterEqual(by_kind["lint"]["duration_ms"], 0)

        rerun = self.client.post(
            f"/verifications/{by_kind['lint']['id']}/rerun", headers=self.headers
        )
        self.assertEqual(202, rerun.status_code, rerun.text)
        rerun_id = rerun.json()["id"]
        latest = self._wait_runs(4)
        repeated = next(item for item in latest if item["id"] == rerun_id)
        self.assertEqual("failed", repeated["status"])
        self.assertIsNone(repeated["agent_claim"])


if __name__ == "__main__":
    unittest.main()
