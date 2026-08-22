"""P5 robustness: actionable failures and recoverable local data backups."""

from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from janus_server import recovery, scheduler, verification
from janus_server.workspace import WorkspaceContext


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "janus.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.executescript(
            "CREATE TABLE records(id INTEGER PRIMARY KEY, value TEXT);"
            "INSERT INTO records(value) VALUES ('durable');"
        )
        connection.close()
        self.backups = self.root / "backups"

    def tearDown(self):
        self.temp.cleanup()

    def test_oom_disk_and_worktree_failures_have_distinct_recovery(self):
        oom = recovery.classify_failure(RuntimeError("Metal out of memory"))
        disk = recovery.classify_failure(OSError("database or disk is full"))
        worktree = recovery.classify_failure("worktree branch is already checked out")

        self.assertEqual("model_oom", oom["kind"])
        self.assertTrue(oom["retryable"])
        self.assertEqual("storage_write", disk["kind"])
        self.assertEqual("worktree_conflict", worktree["kind"])
        self.assertFalse(worktree["retryable"])

    def test_online_backup_is_private_integral_restorable_and_retained(self):
        created = [
            recovery.create_database_backup(self.database, self.backups, retain=2)
            for _ in range(3)
        ]
        available = recovery.list_database_backups(self.backups)

        self.assertEqual(2, len(available))
        self.assertTrue(created[-1]["integrity"]["ok"])
        self.assertEqual(64, len(created[-1]["sha256"]))
        restored = sqlite3.connect(created[-1]["path"])
        try:
            self.assertEqual("durable", restored.execute(
                "SELECT value FROM records"
            ).fetchone()[0])
        finally:
            restored.close()
        self.assertEqual(0o600, Path(created[-1]["path"]).stat().st_mode & 0o777)

    def test_backup_publish_failure_leaves_no_partial_file(self):
        with patch.object(recovery.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                recovery.create_database_backup(self.database, self.backups)

        self.assertEqual([], list(self.backups.iterdir()))

    def test_failure_detail_is_bounded_for_huge_logs(self):
        failure = recovery.classify_failure("x" * 100_000)
        self.assertLessEqual(len(failure["detail"]), recovery.MAX_FAILURE_CHARS + 32)

    def test_corrupt_database_integrity_is_reported_without_crashing(self):
        corrupt = self.root / "corrupt.sqlite3"
        corrupt.write_bytes(b"not a sqlite database")
        result = recovery.database_integrity(corrupt)
        self.assertFalse(result["ok"])
        self.assertIn("DatabaseError", result["result"])

    def test_huge_verification_log_keeps_tail_and_releases_lease(self):
        resource_scheduler = scheduler.ResourceScheduler()
        context = WorkspaceContext(
            root=self.root, task_id="task_large", workspace_id="workspace_large",
            dispatch_id="dispatch_large",
        )
        result = verification.run(
            "python -c \"print('x' * 100000 + 'END')\"", context,
            output_limit=1024, scheduler=resource_scheduler,
        )

        self.assertEqual(0, result["exit_code"])
        self.assertLessEqual(len(result["stdout"]), 1024)
        self.assertTrue(result["stdout"].strip().endswith("END"))
        active = resource_scheduler.snapshot()["resources"]["verification"]["active"]
        self.assertEqual(0, active)


if __name__ == "__main__":
    unittest.main()
