"""P1 영속 도메인 모델, 상태 전이, migration과 재시작 복원."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path

from janus_server.domain import (
    CURRENT_SCHEMA_VERSION,
    Conflict,
    DomainStore,
    InvalidTransition,
    MIGRATION_1,
    MIGRATIONS,
    MigrationError,
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
        self.assertIn("run_bash", json.loads(agents[0]["tools_json"]))

    def test_skill_versions_activation_and_session_snapshot_are_durable(self):
        artifact = {
            "namespace": "claude", "name": "review", "description": "Review code",
            "source_kind": "claude", "source_locator": "/skills/review",
            "source_subpath": "", "source_key": "source-review",
            "content_hash": "a" * 64, "source_revision": None,
            "original": {"entrypoint": "Review"},
            "compiled": {
                "format": "janus.skill.v1", "name": "review",
                "description": "Review code", "instructions": "Review",
            },
            "report": {"warnings": []}, "compatibility": "native",
        }
        first = self.store.import_skill_version(**artifact)
        duplicate = self.store.import_skill_version(**artifact)
        self.assertEqual(first["id"], duplicate["id"])
        self.assertEqual(1, len(self.store.list_skills()))

        second = self.store.import_skill_version(
            **{**artifact, "content_hash": "b" * 64,
               "compiled": {
                   **artifact["compiled"], "instructions": "Review carefully",
               }},
        )
        self.assertEqual(2, second["version"])
        enabled = self.store.set_agent_profile_skill(
            agent_profile_id="agent_default", skill_id=first["skill_id"],
            activation_mode="auto",
        )
        self.assertEqual(second["id"], enabled["skill_version_id"])

        dispatch = self.store.create_dispatch(
            task_id=self.task["id"], workspace_id=self.workspace["id"],
            agent_profile_id="agent_default",
        )
        session = self.store.create_session(
            task_id=self.task["id"], dispatch_id=dispatch["id"],
            agent_profile_id="agent_default",
        )
        snapshot = self.store.snapshot_session_skills(session["id"])
        self.assertEqual([second["id"]], [item["skill_version_id"] for item in snapshot])

        third = self.store.import_skill_version(
            **{**artifact, "name": "review-renamed", "description": "New description",
               "content_hash": "c" * 64,
               "compiled": {
                   "format": "janus.skill.v1", "name": "review-renamed",
                   "description": "New description", "instructions": "Newest",
               }},
        )
        self.store.set_agent_profile_skill(
            agent_profile_id="agent_default", skill_id=first["skill_id"],
            activation_mode="manual", skill_version_id=third["id"],
        )
        unchanged = self.store.snapshot_session_skills(session["id"])
        self.assertEqual([second["id"]], [item["skill_version_id"] for item in unchanged])
        self.assertEqual("review", unchanged[0]["name"])
        self.assertEqual("Review code", unchanged[0]["description"])

        another = self.store.import_skill_version(
            **{**artifact, "name": "test-writer", "source_key": "source-test-writer",
               "source_locator": "/skills/test-writer", "content_hash": "d" * 64,
               "compiled": {
                   **artifact["compiled"], "name": "test-writer",
                   "description": "Write tests",
               }},
        )
        self.store.set_agent_profile_skill(
            agent_profile_id="agent_default", skill_id=another["skill_id"],
            activation_mode="auto",
        )
        still_frozen = self.store.snapshot_session_skills(session["id"])
        self.assertEqual([second["id"]], [item["skill_version_id"] for item in still_frozen])

    def test_version_one_database_migrates_workspace_progress(self):
        old_path = Path(self.temp.name) / "version-one.sqlite3"
        connection = sqlite3.connect(old_path)
        connection.executescript(
            "CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT);"
            + MIGRATION_1
            + "INSERT INTO schema_migrations VALUES (1, 'old'); PRAGMA user_version=1;"
        )
        connection.close()

        upgraded = DomainStore(old_path)
        with upgraded._connect() as reopened:
            columns = {
                row["name"] for row in reopened.execute("PRAGMA table_info(workspaces)")
            }
            profile_columns = {
                row["name"] for row in reopened.execute("PRAGMA table_info(agent_profiles)")
            }
            dispatch_columns = {
                row["name"] for row in reopened.execute("PRAGMA table_info(dispatches)")
            }
        self.assertEqual(CURRENT_SCHEMA_VERSION, upgraded.schema_version())
        self.assertIn("progress", columns)
        self.assertIn("context_policy_json", profile_columns)
        self.assertIn("agent_profile_snapshot_json", dispatch_columns)

    def test_every_historical_schema_version_migrates_to_current(self):
        for starting_version in range(1, CURRENT_SCHEMA_VERSION):
            with self.subTest(starting_version=starting_version):
                old_path = Path(self.temp.name) / f"version-{starting_version}.sqlite3"
                connection = sqlite3.connect(old_path)
                connection.execute(
                    "CREATE TABLE schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
                )
                for version in range(1, starting_version + 1):
                    connection.executescript(MIGRATIONS[version])
                    connection.execute(
                        "INSERT INTO schema_migrations VALUES (?, 'fixture')", (version,)
                    )
                connection.execute(f"PRAGMA user_version={starting_version}")
                connection.commit()
                connection.close()

                upgraded = DomainStore(old_path)
                self.assertEqual(CURRENT_SCHEMA_VERSION, upgraded.schema_version())
                with upgraded._connect() as checked:
                    self.assertEqual("ok", checked.execute(
                        "PRAGMA integrity_check"
                    ).fetchone()[0])

    def test_future_or_discontinuous_schema_is_rejected_without_mutation(self):
        future_path = Path(self.temp.name) / "future.sqlite3"
        connection = sqlite3.connect(future_path)
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO schema_migrations VALUES (?, 'future')",
            (CURRENT_SCHEMA_VERSION + 1,),
        )
        connection.execute(f"PRAGMA user_version={CURRENT_SCHEMA_VERSION + 1}")
        connection.commit()
        connection.close()

        with self.assertRaises(MigrationError):
            DomainStore(future_path)
        reopened = sqlite3.connect(future_path)
        try:
            self.assertEqual(
                CURRENT_SCHEMA_VERSION + 1,
                reopened.execute("PRAGMA user_version").fetchone()[0],
            )
        finally:
            reopened.close()

        discontinuous_path = Path(self.temp.name) / "discontinuous.sqlite3"
        connection = sqlite3.connect(discontinuous_path)
        connection.execute(
            "CREATE TABLE schema_migrations "
            "(version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO schema_migrations VALUES (?, 'broken')", [(1,), (3,)]
        )
        connection.execute("PRAGMA user_version=3")
        connection.commit()
        connection.close()

        with self.assertRaises(MigrationError):
            DomainStore(discontinuous_path)
        reopened = sqlite3.connect(discontinuous_path)
        try:
            self.assertEqual(
                [(1,), (3,)],
                reopened.execute(
                    "SELECT version FROM schema_migrations ORDER BY version"
                ).fetchall(),
            )
        finally:
            reopened.close()

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

    def test_storage_write_failure_rolls_back_the_whole_transaction(self):
        with self.assertRaises(sqlite3.OperationalError):
            with self.store.transaction(immediate=True) as connection:
                connection.execute(
                    "INSERT INTO projects(id,name,repo_path,created_at,updated_at) "
                    "VALUES ('project_partial','partial','/tmp/partial','now','now')"
                )
                raise sqlite3.OperationalError("database or disk is full")

        with self.store._connect() as connection:
            count = connection.execute(
                "SELECT count(*) FROM projects WHERE id='project_partial'"
            ).fetchone()[0]
        self.assertEqual(0, count)

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

    def test_restart_marks_interrupted_workspace_preparation_retryable(self):
        self.store.transition_task(self.task["id"], "preparing", expected="todo")
        recovered = self.store.recover_interrupted_runtime()

        workspace = self.store.get_workspace(self.workspace["id"])
        task = self.store.get_task(self.task["id"])
        self.assertEqual("failed", workspace["state"])
        self.assertEqual("interrupted", workspace["progress"])
        self.assertIn("server restarted", workspace["error"])
        self.assertEqual("failed", task["status"])
        self.assertEqual(1, recovered["workspaces"])
        self.assertEqual(1, recovered["preparing_tasks"])

    def test_dispatch_snapshots_profile_budget_and_persists_usage(self):
        profile = self.store.create_agent_profile(
            name="Budget", system_prompt="work", tools=[],
            model_profile_id="model_qwen38_27b_4bit", max_steps=9,
            budget={
                "dispatch": {"token_limit": 99, "step_limit": 3},
                "workers": {"total_limit": 2, "concurrent_limit": 1},
            },
        )
        dispatch = self.store.create_dispatch(
            task_id=self.task["id"], workspace_id=self.workspace["id"],
            agent_profile_id=profile["id"],
        )
        snapshot = json.loads(dispatch["budget_json"])
        self.assertEqual(99, snapshot["dispatch"]["token_limit"])
        self.assertEqual(3, snapshot["dispatch"]["step_limit"])

        self.store.update_agent_profile(
            profile["id"], budget={"dispatch": {"token_limit": 1000}}
        )
        self.assertEqual(
            99, json.loads(self.store.get_dispatch(dispatch["id"])["budget_json"])
            ["dispatch"]["token_limit"]
        )
        usage = {
            "prompt_tokens": 7, "completion_tokens": 2, "steps": 1,
            "active_time_ms": 12.5, "workers_started": 0,
            "peak_concurrent_workers": 0,
        }
        self.store.record_dispatch_budget(
            dispatch["id"], usage=usage, exhausted_reason="dispatch:token_limit"
        )
        reopened = DomainStore(self.db_path).get_dispatch(dispatch["id"])
        self.assertEqual(usage, json.loads(reopened["usage_json"]))
        self.assertEqual("dispatch:token_limit", reopened["budget_exhausted_reason"])


if __name__ == "__main__":
    unittest.main()
