"""GitHub CLI adapter command safety, state normalization, and failed-log bounds."""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from janus_server.github_service import GitHubService, GitHubServiceError, MAX_FAILED_LOG_CHARS


def completed(args: list[str], output: object, *, returncode: int = 0) -> subprocess.CompletedProcess[str]:
    stdout = output if isinstance(output, str) else json.dumps(output)
    return subprocess.CompletedProcess(args, returncode, stdout=stdout, stderr="")


class GitHubServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.service = GitHubService()

    def tearDown(self):
        self.temp.cleanup()

    def test_create_uses_argument_vector_and_normalizes_pull_request(self):
        pr = {
            "number": 7, "url": "https://github.test/pull/7", "state": "OPEN",
            "isDraft": False, "mergedAt": None, "closedAt": None,
            "mergeStateStatus": "CLEAN", "reviewDecision": "APPROVED",
            "title": "Safe title", "headRefName": "janus/task-safe", "baseRefName": "main",
        }
        with patch("subprocess.run", side_effect=[
            completed([], "https://github.test/pull/7\n"), completed([], pr),
        ]) as run:
            result = self.service.create_pull_request(
                root_path=self.root, head="janus/task-safe", base="main",
                title="Safe title", body="Body; $(never shell-expanded)",
            )
        command = run.call_args_list[0].args[0]
        self.assertEqual("gh", command[0])
        self.assertIn("Body; $(never shell-expanded)", command)
        self.assertFalse(run.call_args_list[0].kwargs.get("shell", False))
        self.assertEqual("open", result["state"])
        self.assertEqual(7, result["number"])

    def test_refresh_collects_failed_logs_with_hard_truncation(self):
        pr = {
            "number": 8, "url": "https://github.test/pull/8", "state": "OPEN",
            "isDraft": False, "mergedAt": None, "closedAt": None,
            "mergeStateStatus": "BLOCKED", "reviewDecision": "REVIEW_REQUIRED",
            "title": "CI", "headRefName": "janus/ci", "baseRefName": "main",
        }
        checks = [{"name": "tests", "state": "FAILURE", "bucket": "fail"}]
        runs = [{
            "databaseId": 88, "name": "tests", "displayTitle": "CI", "status": "completed",
            "conclusion": "failure", "url": "https://github.test/run/88",
            "createdAt": "now", "updatedAt": "now",
        }]
        huge = "x" * (MAX_FAILED_LOG_CHARS + 100)
        with patch("subprocess.run", side_effect=[
            completed([], pr), completed([], checks), completed([], runs), completed([], huge),
        ]):
            result = self.service.refresh(root_path=self.root, branch="janus/ci")
        self.assertEqual("failure", result["runs"][0]["conclusion"])
        self.assertEqual(MAX_FAILED_LOG_CHARS, len(result["failed_logs"][0]["log"]))
        self.assertTrue(result["failed_logs"][0]["truncated"])

    def test_unsafe_branch_is_rejected_before_subprocess(self):
        with patch("subprocess.run") as run, self.assertRaises(GitHubServiceError):
            self.service.pull_request(root_path=self.root, branch="--repo=attacker/repo")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
