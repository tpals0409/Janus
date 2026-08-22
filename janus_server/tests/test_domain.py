"""P1 영속 도메인 모델, 상태 전이, migration과 재시작 복원."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path

from janus_server.domain import (
    CURRENT_SCHEMA_VERSION,
    Conflict,
    DomainStore,
    InvalidTransition,
)


class DomainStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "janus.sqlite3"
        self.store = DomainStore(self.db_path)
        self.project = self.store.create_project(
            name="Janus", repo_path=str(Path(self.temp.name) / "repo")
        )
        self.task = self.store.create_task(
            project_id=self.project["id"], title="Persistent task",
            objective="Prove restart recovery", acceptance_command="python -m unittest",
            base_ref="main",
        )
        self.workspace = self.store.create_workspace(
            task_id=self.task["id"], repo_path=self.project["repo_path"], base_ref="main"
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_schema_and_seed_profiles(self):
        self.assertEqual(CURRENT_SCHEMA_VERSION, self.store.schema_version())
        models = self.store.list_model_profiles()
        agents = self.store.list_agent_profiles()
        self.assertEqual(["model_qwen38_27b_4bit"], [item["id"] for item in models])
        self.assertEqual(["agent_default"], [item["id"] for item in agents])
        self.assertEqual("4-bit MLX", models[0]["quantization"])

    def test_task_workspace_dispatch_and_session_state_machines(self):
        with self.assertRaises(InvalidTransition):
            self.store.transition_task(self.task["id"], "review")
        self.store.transition_task(self.task["id"], "preparing", expected="todo")
        self.store.transition_workspace(
            self.workspace["id"], "ready", root_path="/tmp/worktree",
            branch_name="janus/task-persistent",
        )
        self.store.transition_task(self.task["id"], "working", expected="preparing")

        dispatch = self.store.create_dispatch(
            task_id=self.task["id"], workspace_id=self.workspace["id"],
            agent_profile_id="agent_default",
        )
        self.assertEqual(1, dispatch["attempt"])
        self.assertEqual(self.task["objective"], dispatch["objective_snapshot"])
        dispatch = self.store.transition_dispatch(dispatch["id"], "running", expected="queued")
        self.assertIsNotNone(dispatch["started_at"])

        session = self.store.create_session(
            task_id=self.task["id"], dispatch_id=dispatch["id"],
            agent_profile_id="agent_default",
        )
        self.store.transition_session(session["id"], "running")
        self.store.append_session_event(
            session["id"], kind="user", payload={"content": "hello"},
            task_id=self.task["id"], dispatch_id=dispatch["id"],
            workspace_id=self.workspace["id"],
        )
        self.store.transition_session(session["id"], "idle")
        self.store.transition_session(session["id"], "running")
        stopped = self.store.transition_session(session["id"], "stopped")
        completed = self.store.transition_dispatch(dispatch["id"], "completed")
        self.store.transition_task(self.task["id"], "review", expected="working")

        self.assertIsNotNone(stopped["stopped_at"])
        self.assertIsNotNone(completed["ended_at"])
        self.assertEqual("hello", self.store.list_session_events(session["id"])[0]["payload"]["content"])

    def test_cross_task_workspace_and_session_ownership_is_rejected(self):
        other = self.store.create_task(
            project_id=self.project["id"], title="Other", objective="Stay isolated",
            acceptance_command="true", base_ref="main",
        )
        with self.assertRaises(Conflict):
            self.store.create_dispatch(
                task_id=other["id"], workspace_id=self.workspace["id"],
                agent_profile_id="agent_default",
            )

    def test_dispatch_attempts_are_unique_under_concurrency(self):
        attempts: list[int] = []
        errors: list[BaseException] = []
        lock = threading.Lock()

        def create() -> None:
            try:
                dispatch = self.store.create_dispatch(
                    task_id=self.task["id"], workspace_id=self.workspace["id"],
                    agent_profile_id="agent_default",
                )
                with lock:
                    attempts.append(dispatch["attempt"])
            except BaseException as error:
                errors.append(error)

        threads = [threading.Thread(target=create) for _ in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual([], errors)
        self.assertEqual(list(range(1, 9)), sorted(attempts))

    def test_app_restart_restores_all_persisted_state(self):
        dispatch = self.store.create_dispatch(
            task_id=self.task["id"], workspace_id=self.workspace["id"],
            agent_profile_id="agent_default",
        )
        session = self.store.create_session(
            task_id=self.task["id"], dispatch_id=dispatch["id"],
            agent_profile_id="agent_default",
        )
        self.store.append_session_event(
            session["id"], kind="checkpoint", payload={"next": "resume"},
            task_id=self.task["id"], dispatch_id=dispatch["id"],
            workspace_id=self.workspace["id"],
        )

        reopened = DomainStore(self.db_path)
        self.assertEqual(self.project["id"], reopened.list_projects()[0]["id"])
        self.assertEqual(self.task["id"], reopened.list_tasks(self.project["id"])[0]["id"])
        self.assertEqual(self.workspace["id"], reopened.get_task_workspace(self.task["id"])["id"])
        self.assertEqual(dispatch["id"], reopened.list_dispatches(self.task["id"])[0]["id"])
        self.assertEqual("resume", reopened.list_session_events(session["id"])[0]["payload"]["next"])


if __name__ == "__main__":
    unittest.main()
