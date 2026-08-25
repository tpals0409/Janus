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

from janus_server import github_service, scheduler, server, shared
from janus_server.routers import shipping


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
        shared._DOMAIN_STORE = None
        shared._DOMAIN_STORE_PATH = None
        shared._DOMAIN_RECOVERED_PATH = None
        shared._WORKSPACE_SERVICE = None
        shared._WORKSPACE_SERVICE_PATH = None
        shared._GITHUB_SERVICE = None
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
        with shared._VERIFICATION_JOBS_LOCK:
            threads = list(shared._VERIFICATION_JOBS.values())
        for thread in threads:
            thread.join(timeout=5)
        self.client.close()
        shared._DOMAIN_STORE = None
        shared._DOMAIN_STORE_PATH = None
        shared._DOMAIN_RECOVERED_PATH = None
        shared._WORKSPACE_SERVICE = None
        shared._WORKSPACE_SERVICE_PATH = None
        shared._GITHUB_SERVICE = None
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

    def _wait_runs(self, count: int, task_id: str | None = None) -> list[dict]:
        task_id = task_id or self.task_id
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline:
            runs = self.client.get(
                f"/tasks/{task_id}/verifications", headers=self.headers
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

    def test_review_comments_batch_decisions_stale_guard_accept_and_discard(self):
        workspace = self.client.get(
            f"/tasks/{self.task_id}/workspace", headers=self.headers
        ).json()
        root = Path(workspace["root_path"])
        changed = root / "review.txt"
        changed.write_text("one\ntwo\n", encoding="utf-8")
        change_set = self.client.get(
            f"/tasks/{self.task_id}/changeset", headers=self.headers
        ).json()
        revision = change_set["revision"]

        first = self.client.post(
            f"/tasks/{self.task_id}/review/comments", headers=self.headers, json={
                "revision": revision, "layer": "untracked", "file_path": "review.txt",
                "new_line": 1, "hunk_header": "@@ -0,0 +1,2 @@", "body": "Rename this",
            },
        )
        self.assertEqual(200, first.status_code, first.text)
        second = self.client.post(
            f"/tasks/{self.task_id}/review/comments", headers=self.headers, json={
                "revision": revision, "layer": "untracked", "file_path": "review.txt",
                "new_line": 2, "hunk_header": "@@ -0,0 +1,2 @@", "body": "Add a test",
            },
        )
        self.assertEqual(200, second.status_code, second.text)

        changed.write_text("one\ntwo\nthree\n", encoding="utf-8")
        stale = self.client.post(
            f"/tasks/{self.task_id}/review/comments", headers=self.headers, json={
                "revision": revision, "layer": "untracked", "file_path": "review.txt",
                "new_line": 3, "body": "stale",
            },
        )
        self.assertEqual(409, stale.status_code)
        refreshed = self.client.get(
            f"/tasks/{self.task_id}/changeset", headers=self.headers
        ).json()
        self.assertNotEqual(revision, refreshed["revision"])

        requested = self.client.post(
            f"/tasks/{self.task_id}/review/decision", headers=self.headers, json={
                "revision": refreshed["revision"], "decision": "request_changes",
                "comment_ids": [first.json()["id"], second.json()["id"]],
                "message": "Address both comments",
            },
        )
        self.assertEqual(200, requested.status_code, requested.text)
        self.assertEqual("working", requested.json()["task"]["status"])
        self.assertEqual(2, len(requested.json()["decision"]["comment_ids"]))

        for comment in (first.json(), second.json()):
            resolved = self.client.patch(
                f"/review/comments/{comment['id']}", headers=self.headers,
                json={"resolved": True},
            )
            self.assertIsNotNone(resolved.json()["resolved_at"])

        self.client.post(
            f"/tasks/{self.task_id}/verifications", headers=self.headers, json={}
        )
        self._wait_runs(1)
        accepted = self.client.post(
            f"/tasks/{self.task_id}/review/decision", headers=self.headers, json={
                "revision": refreshed["revision"], "decision": "accept",
            },
        )
        self.assertEqual(200, accepted.status_code, accepted.text)
        self.assertEqual("review", accepted.json()["task"]["status"])

        (root / "discard-me.txt").write_text("temporary\n", encoding="utf-8")
        discard_revision = self.client.get(
            f"/tasks/{self.task_id}/changeset", headers=self.headers
        ).json()["revision"]
        discarded = self.client.post(
            f"/tasks/{self.task_id}/review/decision", headers=self.headers, json={
                "revision": discard_revision, "decision": "discard",
                "confirm_workspace_id": workspace["id"],
                "confirm_discard": self.task_id,
            },
        )
        self.assertEqual(409, discarded.status_code, discarded.text)
        self.assertIn("소유 저장 루트 밖", discarded.json()["detail"])
        self.assertTrue((root / "review.txt").exists())
        self.assertTrue((root / "discard-me.txt").exists())

    def test_task_create_run_verify_review_commit_push_e2e_uses_main_checkout(self):
        main_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        session = self.client.post(
            f"/tasks/{self.task_id}/sessions", headers=self.headers,
            json={"agent_profile_id": "agent_default"},
        )
        self.assertEqual(200, session.status_code, session.text)
        self.assertEqual("created", session.json()["status"])

        workspace = self.client.get(
            f"/tasks/{self.task_id}/workspace", headers=self.headers
        ).json()
        root = Path(workspace["root_path"])
        (root / "finished.txt").write_text("verified work\n", encoding="utf-8")
        changes = self.client.get(
            f"/tasks/{self.task_id}/changeset", headers=self.headers
        ).json()
        verified = self.client.post(
            f"/tasks/{self.task_id}/verifications", headers=self.headers, json={}
        )
        self.assertEqual(202, verified.status_code, verified.text)
        self.assertEqual("passed", self._wait_runs(1)[0]["status"])

        accepted = self.client.post(
            f"/tasks/{self.task_id}/review/decision", headers=self.headers, json={
                "revision": changes["revision"], "decision": "accept",
            },
        )
        self.assertEqual(200, accepted.status_code, accepted.text)
        committed = self.client.post(
            f"/tasks/{self.task_id}/ship/commit", headers=self.headers, json={
                "revision": changes["revision"], "message": "feat: finish verified task",
            },
        )
        self.assertEqual(200, committed.status_code, committed.text)
        commit_sha = committed.json()["result"]["commit_sha"]
        self.assertEqual(commit_sha, git(root, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual("main", git(self.repo, "branch", "--show-current").stdout.strip())
        self.assertNotEqual(main_head, git(self.repo, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual(commit_sha, git(self.repo, "rev-parse", "HEAD").stdout.strip())

        self.assertEqual("", git(self.repo, "status", "--porcelain").stdout.strip())

        failed_push = self.client.post(
            f"/tasks/{self.task_id}/ship/push", headers=self.headers, json={
                "confirm_commit_sha": commit_sha, "remote": "origin",
            },
        )
        self.assertEqual(409, failed_push.status_code)
        failed_record = self.client.get(
            f"/tasks/{self.task_id}/shipments", headers=self.headers
        ).json()[-1]
        self.assertEqual("push", failed_record["action"])
        self.assertEqual("failed", failed_record["status"])
        self.assertIn("remote", failed_record["error"])

        remote = Path(self.temp.name) / "remote.git"
        subprocess.run(["git", "init", "--bare", str(remote)], check=True, capture_output=True)
        git(self.repo, "remote", "add", "origin", str(remote))
        pushed = self.client.post(
            f"/tasks/{self.task_id}/ship/push", headers=self.headers, json={
                "confirm_commit_sha": commit_sha, "remote": "origin",
            },
        )
        self.assertEqual(200, pushed.status_code, pushed.text)
        branch = workspace["branch_name"]
        remote_sha = subprocess.run(
            ["git", "--git-dir", str(remote), "rev-parse", f"refs/heads/{branch}"],
            check=True, capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(commit_sha, remote_sha)

        handoff = self.client.get(
            f"/tasks/{self.task_id}/ship/handoff", headers=self.headers
        ).json()
        self.assertTrue(handoff["executed"])
        self.assertIsNone(handoff["local_apply_command"])
        shipments = self.client.get(
            f"/tasks/{self.task_id}/shipments", headers=self.headers
        ).json()
        self.assertEqual(["commit", "push", "push"], [item["action"] for item in shipments])
        self.assertEqual(["completed", "failed", "completed"], [item["status"] for item in shipments])

        class FakeGitHub:
            def create_pull_request(self, **kwargs):
                self.created = kwargs
                return {
                    "number": 17, "url": "https://github.test/acme/repo/pull/17",
                    "state": "open", "draft": False, "merged_at": None,
                    "closed_at": None, "merge_state": "BLOCKED",
                    "review_decision": "REVIEW_REQUIRED", "title": "Verify",
                    "head_branch": branch, "base_branch": "main",
                }

            def checks(self, **_kwargs):
                return {
                    "checks": [{"name": "tests", "state": "FAILURE", "bucket": "fail"}],
                    "runs": [{"databaseId": 99, "conclusion": "failure"}],
                    "failed_logs": [{
                        "run_id": 99, "name": "tests", "conclusion": "failure",
                        "url": "https://github.test/run/99", "log": "assertion failed",
                        "truncated": False,
                    }],
                }

            def refresh(self, **_kwargs):
                pull_request = {
                    "number": 17, "url": "https://github.test/acme/repo/pull/17",
                    "state": "merged", "draft": False,
                    "merged_at": "2026-08-22T12:00:00Z", "closed_at": None,
                    "merge_state": "CLEAN", "review_decision": "APPROVED",
                    "title": "Verify", "head_branch": branch, "base_branch": "main",
                }
                return {
                    "pull_request": pull_request,
                    "checks": [{"name": "tests", "state": "SUCCESS", "bucket": "pass"}],
                    "runs": [{"databaseId": 100, "conclusion": "success"}],
                    "failed_logs": [],
                }

        fake_github = FakeGitHub()
        shared._GITHUB_SERVICE = fake_github
        pull_request = self.client.post(
            f"/tasks/{self.task_id}/pull-request", headers=self.headers, json={
                "title": "Verify", "body": "Verified work", "base": "main",
            },
        )
        self.assertEqual(200, pull_request.status_code, pull_request.text)
        linked = pull_request.json()["pull_request"]
        self.assertEqual(17, linked["number"])
        self.assertEqual("open", linked["state"])
        self.assertEqual("assertion failed", linked["failed_logs"][0]["log"])
        self.assertFalse(pull_request.json()["archive_recommended"])

        merged = self.client.post(
            f"/tasks/{self.task_id}/pull-request/refresh", headers=self.headers
        )
        self.assertEqual(200, merged.status_code, merged.text)
        self.assertEqual("merged", merged.json()["pull_request"]["state"])
        self.assertTrue(merged.json()["archive_recommended"])
        self.assertTrue(merged.json()["branch_preserved"])
        self.assertEqual("ready", self.client.get(
            f"/tasks/{self.task_id}/workspace", headers=self.headers
        ).json()["state"])
        self.assertEqual(0, git(self.repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}").returncode)
        self.assertEqual(commit_sha, git(self.repo, "rev-parse", "HEAD").stdout.strip())

    def test_pull_request_auth_failure_is_persisted_for_recovery(self):
        store = server.get_domain_store()
        task = store.get_task(self.task_id)
        workspace = store.get_task_workspace(self.task_id)
        assert workspace is not None
        head = {"commit_sha": "a" * 40, "branch_name": workspace["branch_name"], "dirty": False}

        class UnauthorizedGitHub:
            def create_pull_request(self, **_kwargs):
                raise github_service.GitHubServiceError(
                    "gh pr create 실패(exit 4): authentication required"
                )

        shared._GITHUB_SERVICE = UnauthorizedGitHub()
        with patch.object(shipping, "_pushed_task_head", return_value=(task, workspace, head)):
            response = self.client.post(
                f"/tasks/{self.task_id}/pull-request", headers=self.headers,
                json={"title": "Recoverable PR", "body": "body", "base": "main"},
            )
        self.assertEqual(409, response.status_code)
        persisted = self.client.get(
            f"/tasks/{self.task_id}/pull-request", headers=self.headers
        ).json()["pull_request"]
        self.assertEqual("error", persisted["state"])
        self.assertIn("authentication required", persisted["error"])
        self.assertEqual(workspace["branch_name"], persisted["head_branch"])

    def test_two_tasks_share_the_project_checkout(self):
        second = self.client.post(
            f"/projects/{self.project_id}/tasks", headers=self.headers, json={
                "title": "Second", "objective": "Independent second task",
                "acceptance_command": self.acceptance, "base_ref": "main",
            },
        ).json()
        self.client.post(
            f"/tasks/{second['id']}/workspace/prepare", headers=self.headers
        )
        deadline = time.monotonic() + 5
        second_workspace = None
        while time.monotonic() < deadline:
            candidate = self.client.get(
                f"/tasks/{second['id']}/workspace", headers=self.headers
            ).json()
            if candidate["state"] == "ready" and not candidate["job_active"]:
                second_workspace = candidate
                break
            time.sleep(0.01)
        self.assertIsNotNone(second_workspace)
        first_workspace = self.client.get(
            f"/tasks/{self.task_id}/workspace", headers=self.headers
        ).json()
        self.assertEqual(first_workspace["root_path"], second_workspace["root_path"])
        self.assertEqual(self.repo.resolve(), Path(first_workspace["root_path"]).resolve())
        self.assertEqual(first_workspace["branch_name"], second_workspace["branch_name"])

        for task_id in (self.task_id, second["id"]):
            started = self.client.post(
                f"/tasks/{task_id}/sessions", headers=self.headers,
                json={"agent_profile_id": "agent_default"},
            )
            self.assertEqual(200, started.status_code, started.text)
        Path(first_workspace["root_path"], "first-only.txt").write_text(
            "first\n", encoding="utf-8"
        )
        Path(second_workspace["root_path"], "second-only.txt").write_text(
            "second\n", encoding="utf-8"
        )
        first_changes = self.client.get(
            f"/tasks/{self.task_id}/changeset", headers=self.headers
        ).json()
        second_changes = self.client.get(
            f"/tasks/{second['id']}/changeset", headers=self.headers
        ).json()
        expected = {"first-only.txt", "second-only.txt"}
        self.assertEqual(
            expected, {item["path"] for item in first_changes["sections"]["untracked"]}
        )
        self.assertEqual(
            expected, {item["path"] for item in second_changes["sections"]["untracked"]}
        )


if __name__ == "__main__":
    unittest.main()
