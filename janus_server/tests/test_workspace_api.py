"""Task Workspace background lifecycle API integration tests."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import server


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check
    )


class WorkspaceApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.db = root / "janus.sqlite3"
        self.worktrees = root / "workspaces"
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "janus@example.test")
        git(self.repo, "config", "user.name", "Janus Test")
        (self.repo / "README.md").write_text("Janus\n", encoding="utf-8")
        git(self.repo, "add", "README.md")
        git(self.repo, "commit", "-m", "initial")
        self.main_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        self.env = patch.dict(os.environ, {
            "JANUS_DB_FILE": str(self.db),
            "JANUS_WORKTREES_DIR": str(self.worktrees),
        })
        self.env.start()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._WORKSPACE_SERVICE = None
        server._WORKSPACE_SERVICE_PATH = None
        with server._WORKSPACE_JOBS_LOCK:
            server._WORKSPACE_JOBS.clear()
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}
        project = self.client.post(
            "/projects", headers=self.headers,
            json={"name": "Workspace API", "repo_path": str(self.repo)},
        ).json()
        self.project_id = project["id"]

    def tearDown(self):
        with server._WORKSPACE_JOBS_LOCK:
            threads = list(server._WORKSPACE_JOBS.values())
        for thread in threads:
            thread.join(timeout=5)
        self.client.close()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._WORKSPACE_SERVICE = None
        server._WORKSPACE_SERVICE_PATH = None
        self.env.stop()
        self.temp.cleanup()

    def create_task(self, *, base_ref: str = "main", title: str = "Prepare") -> dict:
        response = self.client.post(
            f"/projects/{self.project_id}/tasks", headers=self.headers,
            json={
                "title": title,
                "objective": "Work in the project checkout",
                "acceptance_command": "git status --short",
                "base_ref": base_ref,
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def wait_workspace(self, task_id: str, state: str, timeout: float = 5) -> dict:
        deadline = time.monotonic() + timeout
        latest = None
        while time.monotonic() < deadline:
            response = self.client.get(
                f"/tasks/{task_id}/workspace", headers=self.headers
            )
            self.assertEqual(200, response.status_code, response.text)
            latest = response.json()
            if latest["state"] == state and not latest["job_active"]:
                return latest
            time.sleep(0.01)
        self.fail(f"workspace did not reach {state}: {latest}")

    def assert_main_unchanged(self):
        self.assertEqual("main", git(self.repo, "branch", "--show-current").stdout.strip())
        self.assertEqual(self.main_head, git(self.repo, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual("", git(self.repo, "status", "--porcelain").stdout.strip())

    def test_deleting_a_task_preserves_the_project_checkout(self):
        task = self.create_task()
        self.client.post(f"/tasks/{task['id']}/workspace/prepare", headers=self.headers)
        ready = self.wait_workspace(task["id"], "ready")
        root = Path(ready["root_path"])
        branch = ready["branch_name"]
        self.assertTrue(root.is_dir())

        deleted = self.client.delete(f"/tasks/{task['id']}", headers=self.headers)
        self.assertEqual(200, deleted.status_code, deleted.text)
        self.assertIsNotNone(deleted.json()["archived_at"])
        self.assertEqual(self.repo.resolve(), root.resolve())
        self.assertTrue(root.exists())
        self.assertEqual("main", branch)
        self.assert_main_unchanged()

    def test_uncommitted_work_survives_a_task_delete(self):
        """목록 정리가 커밋 안 된 변경을 지우면 안 된다."""
        task = self.create_task()
        self.client.post(f"/tasks/{task['id']}/workspace/prepare", headers=self.headers)
        ready = self.wait_workspace(task["id"], "ready")
        root = Path(ready["root_path"])
        (root / "wip.txt").write_text("아직 커밋 안 함", encoding="utf-8")

        deleted = self.client.delete(f"/tasks/{task['id']}", headers=self.headers)
        self.assertEqual(200, deleted.status_code, deleted.text)
        self.assertIsNotNone(deleted.json()["archived_at"])
        self.assertTrue((root / "wip.txt").is_file())

    def test_real_prepare_uses_project_checkout_and_blocks_removal(self):
        task = self.create_task()
        response = self.client.post(
            f"/tasks/{task['id']}/workspace/prepare", headers=self.headers
        )
        self.assertEqual(202, response.status_code, response.text)
        ready = self.wait_workspace(task["id"], "ready")
        self.assertEqual("ready", ready["progress"])
        root = Path(ready["root_path"])
        self.assertEqual(self.repo.resolve(), root.resolve())
        self.assertFalse(ready["owned"])
        self.assert_main_unchanged()

        status = self.client.get(
            f"/tasks/{task['id']}/workspace/status", headers=self.headers
        ).json()
        self.assertFalse(status["git_status"]["dirty"])
        denied = self.client.post(
            f"/tasks/{task['id']}/workspace/archive", headers=self.headers,
            json={"confirm_workspace_id": ready["id"]},
        )
        self.assertEqual(409, denied.status_code)
        self.assertIn("소유하지 않은", denied.json()["detail"])
        self.assertTrue(root.exists())
        self.assert_main_unchanged()

    def test_failure_can_retry_after_base_ref_is_corrected(self):
        task = self.create_task(base_ref="missing-ref", title="Retry")
        started = self.client.post(
            f"/tasks/{task['id']}/workspace/prepare", headers=self.headers
        )
        self.assertEqual(202, started.status_code)
        failed = self.wait_workspace(task["id"], "failed")
        self.assertEqual("failed", failed["progress"])
        self.assertIn("InvalidRepository", failed["error"])

        updated = self.client.patch(
            f"/tasks/{task['id']}", headers=self.headers, json={"base_ref": "main"}
        )
        self.assertEqual(200, updated.status_code)
        retried = self.client.post(
            f"/tasks/{task['id']}/workspace/retry", headers=self.headers
        )
        self.assertEqual(202, retried.status_code, retried.text)
        ready = self.wait_workspace(task["id"], "ready")
        self.assertEqual("main", ready["base_ref"])

        removed = self.client.request(
            "DELETE", f"/tasks/{task['id']}/workspace/force",
            headers=self.headers, json={"confirm_workspace_id": ready["id"]},
        )
        self.assertEqual(409, removed.status_code, removed.text)
        self.assertIn("소유하지 않은", removed.json()["detail"])
        self.assert_main_unchanged()

    def test_prepare_returns_while_background_job_is_still_running(self):
        task = self.create_task(title="Background")
        started = threading.Event()
        release = threading.Event()

        class BlockingService:
            def validate_repo(_self, repo_path, base_ref):
                started.set()
                release.wait(timeout=5)
                return {
                    "repo_path": str(self.repo),
                    "base_ref": base_ref,
                    "commit": self.main_head,
                }

        with patch.object(server, "get_workspace_service", return_value=BlockingService()):
            response = self.client.post(
                f"/tasks/{task['id']}/workspace/prepare", headers=self.headers
            )
            self.assertEqual(202, response.status_code, response.text)
            self.assertTrue(started.wait(timeout=2))
            current = self.client.get(
                f"/tasks/{task['id']}/workspace", headers=self.headers
            ).json()
            self.assertTrue(current["job_active"])
            self.assertEqual("validating", current["progress"])
            release.set()
            self.wait_workspace(task["id"], "ready")

    def test_changeset_endpoint_reads_current_git_state(self):
        task = self.create_task(title="Review changes")
        self.client.post(
            f"/tasks/{task['id']}/workspace/prepare", headers=self.headers
        )
        ready = self.wait_workspace(task["id"], "ready")
        root = Path(ready["root_path"])
        (root / "first.txt").write_text("first\n", encoding="utf-8")

        first = self.client.get(
            f"/tasks/{task['id']}/changeset", headers=self.headers
        )
        self.assertEqual(200, first.status_code, first.text)
        self.assertEqual(["first.txt"], [
            item["path"] for item in first.json()["sections"]["untracked"]
        ])

        (root / "second.txt").write_text("second\n", encoding="utf-8")
        second = self.client.get(
            f"/tasks/{task['id']}/changeset", headers=self.headers
        )
        self.assertEqual(2, second.json()["counts"]["untracked"])
        self.assertEqual(
            {"first.txt", "second.txt"},
            set(git(self.repo, "ls-files", "--others", "--exclude-standard").stdout.splitlines()),
        )


if __name__ == "__main__":
    unittest.main()
