"""worker_outcomes 영속 계약 — 프로세스가 죽어도 워커 성과를 복원한다."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from janus_server.domain import Conflict, DomainStore, NotFound


class WorkerOutcomeStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = DomainStore(Path(self.temp.name) / "janus.sqlite3")
        project = self.store.create_project(
            name="Janus", repo_path=str(Path(self.temp.name) / "repo")
        )
        self.task = self.store.create_task(
            project_id=project["id"], title="Durable task",
            objective="Recover worker outcomes after crashes",
            acceptance_command="pytest -q", base_ref="main",
        )

    def tearDown(self):
        self.temp.cleanup()

    def payload(self, **overrides) -> dict:
        base = {
            "task_id": self.task["id"],
            "workspace_id": "ws_1",
            "session_id": "sess_1",
            "dispatch_id": "dispatch_1",
            "worker": "w1-ed",
            "name": "ed",
            "role": "implementer",
            "status": "cancelled",
            "result": "edited two files before the parent turn ended",
            "error": None,
            "changed_paths": ["src/a.py", "src/b.py"],
            "owned_partitions": ["src/"],
        }
        base.update(overrides)
        return base

    def test_record_roundtrips_json_fields_and_lists_newest_first(self):
        first = self.store.record_worker_outcome(self.payload())
        self.store.record_worker_outcome(self.payload(
            worker="w2-eye", name="eye", role="scout", status="completed",
            result="scout summary", changed_paths=[], owned_partitions=[],
        ))

        rows = self.store.list_worker_outcomes(self.task["id"])
        self.assertEqual(["w2-eye", "w1-ed"], [row["worker_id"] for row in rows])
        oldest = rows[1]
        self.assertEqual("cancelled", oldest["status"])
        self.assertEqual(["src/a.py", "src/b.py"], oldest["changed_paths"])
        self.assertEqual(["src/"], oldest["owned_partitions"])
        self.assertIsNone(oldest["error"])
        self.assertEqual("implementer", oldest["role"])

        fetched = self.store.get_worker_outcome(first["id"])
        self.assertEqual(first["id"], fetched["id"])
        self.assertEqual(["src/a.py", "src/b.py"], fetched["changed_paths"])
        self.assertEqual("sess_1", fetched["session_id"])

    def test_invalid_status_and_unknown_task_are_rejected(self):
        with self.assertRaises(Conflict):
            self.store.record_worker_outcome(self.payload(status="running"))
        stray = self.payload()
        stray["task_id"] = "task_missing"
        with self.assertRaises(NotFound):
            self.store.record_worker_outcome(stray)

    def test_limit_bounds_the_history_window(self):
        for index in range(5):
            self.store.record_worker_outcome(
                self.payload(worker=f"w{index}", name=f"n{index}"))
        rows = self.store.list_worker_outcomes(self.task["id"], limit=3)
        self.assertEqual(3, len(rows))
        self.assertEqual(["w4", "w3", "w2"], [row["worker_id"] for row in rows])

    def test_tables_survive_a_store_restart(self):
        self.store.record_worker_outcome(self.payload())
        reopened = DomainStore(self.store.path)
        rows = reopened.list_worker_outcomes(self.task["id"])
        self.assertEqual(1, len(rows))
        self.assertEqual("w1-ed", rows[0]["worker_id"])
        self.assertEqual(["src/a.py", "src/b.py"], rows[0]["changed_paths"])