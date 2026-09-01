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

    def test_delivered_outcomes_leave_the_recovery_window(self):
        """회수 노트는 한 번만 주입돼야 한다.

        WS 접속마다 새 Orchestration이 만들어지므로, 메모리 소비 플래그만으로는
        브라우저를 새로고침할 때마다 같은 다이제스트가 컨텍스트 맨 앞에 다시
        실렸다 — 모델은 이미 통합한 작업을 다시 통합하라는 지시를 받았다.
        """
        first = self.store.record_worker_outcome(self.payload())
        second = self.store.record_worker_outcome(
            self.payload(worker="w2-eye", status="completed"))

        pending = self.store.list_worker_outcomes(
            self.task["id"], undelivered_only=True)
        self.assertEqual({first["id"], second["id"]}, {r["id"] for r in pending})

        marked = self.store.mark_worker_outcomes_delivered([first["id"]])
        self.assertEqual(1, marked)

        pending = self.store.list_worker_outcomes(
            self.task["id"], undelivered_only=True)
        self.assertEqual([second["id"]], [r["id"] for r in pending])
        # 전체 이력은 그대로 남는다 — 소비 표시는 회수 노트에만 영향을 준다.
        self.assertEqual(2, len(self.store.list_worker_outcomes(self.task["id"])))

        # 두 번 표시해도 새로 소비되는 행은 없다.
        self.assertEqual(
            0, self.store.mark_worker_outcomes_delivered([first["id"]]))
        self.assertEqual(0, self.store.mark_worker_outcomes_delivered([]))

    def test_spawn_counts_survive_a_reconnect(self):
        """스폰 상한이 재접속마다 0으로 되돌아가면 role_limit이 무의미하다."""
        for role in ("implementer", "implementer", "scout"):
            self.store.record_worker_outcome(self.payload(
                worker=f"w-{role}-{id(role)}", role=role, status="completed"))
        # 다른 Dispatch의 성과는 이 Dispatch의 상한에 들어가지 않는다.
        self.store.record_worker_outcome(self.payload(
            worker="w-other", role="implementer", status="completed",
            dispatch_id="dispatch_other"))

        counts = self.store.worker_spawn_counts("dispatch_1")
        self.assertEqual(3, counts["total"])
        self.assertEqual({"implementer": 2, "scout": 1}, counts["by_role"])

        self.assertEqual(
            {"total": 0, "by_role": {}},
            self.store.worker_spawn_counts("dispatch_missing"),
        )

    def test_tables_survive_a_store_restart(self):
        self.store.record_worker_outcome(self.payload())
        reopened = DomainStore(self.store.path)
        rows = reopened.list_worker_outcomes(self.task["id"])
        self.assertEqual(1, len(rows))
        self.assertEqual("w1-ed", rows[0]["worker_id"])
        self.assertEqual(["src/a.py", "src/b.py"], rows[0]["changed_paths"])