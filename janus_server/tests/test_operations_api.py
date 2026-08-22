"""One operations snapshot supervises ten Tasks without opening each Task screen."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import server


class OperationsDashboardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "JANUS_DB_FILE": str(self.root / "janus.sqlite3"),
            "JANUS_WORKTREES_DIR": str(self.root / "workspaces"),
        })
        self.env.start()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}
        self.store = server.get_domain_store()
        self.project = self.store.create_project(
            name="Ten-task control", repo_path=str(self.root / "repo")
        )

    def tearDown(self):
        self.client.close()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        self.env.stop()
        self.temp.cleanup()

    def create_execution(self, index: int) -> tuple[dict, dict]:
        task = self.store.create_task(
            project_id=self.project["id"], title=f"Task {index:02d}",
            objective=f"Complete operation {index}", acceptance_command="true",
            base_ref="main",
        )
        workspace_root = self.root / f"workspace-{index}"
        workspace_root.mkdir()
        workspace = self.store.create_workspace(
            task_id=task["id"], repo_path=self.project["repo_path"], base_ref="main"
        )
        self.store.transition_workspace(
            workspace["id"], "ready", root_path=str(workspace_root),
            branch_name=f"janus/task-{index}",
        )
        execution = self.store.create_execution(
            task_id=task["id"], workspace_id=workspace["id"],
            agent_profile_id="agent_default",
        )
        return task, execution

    def test_ten_tasks_attention_queue_budget_resources_and_timeline_are_visible(self):
        created = [self.create_execution(index) for index in range(10)]

        # Four remain queued; three own running attempts.
        for _task, execution in created[4:7]:
            self.store.transition_dispatch(execution["dispatch"]["id"], "running")
            self.store.transition_session(execution["session"]["id"], "running")

        needs_task, needs = created[7]
        self.store.transition_dispatch(needs["dispatch"]["id"], "running")
        self.store.transition_dispatch(needs["dispatch"]["id"], "needs_you")
        self.store.transition_session(needs["session"]["id"], "running")
        self.store.transition_session(needs["session"]["id"], "idle")
        self.store.transition_task(needs_task["id"], "needs_you", expected="working")

        review_task, review = created[8]
        self.store.transition_dispatch(review["dispatch"]["id"], "running")
        self.store.transition_dispatch(review["dispatch"]["id"], "completed")
        self.store.transition_task(review_task["id"], "review", expected="working")

        failed_task, failed = created[9]
        self.store.transition_dispatch(failed["dispatch"]["id"], "running")
        self.store.transition_dispatch(failed["dispatch"]["id"], "failed", error="tool error")
        self.store.transition_task(failed_task["id"], "failed", expected="working")

        running_task, running = created[4]
        self.store.record_dispatch_budget(
            running["dispatch"]["id"],
            usage={
                "prompt_tokens": 4096, "completion_tokens": 4096, "steps": 15,
                "active_time_ms": 450_000, "workers_started": 2,
                "peak_concurrent_workers": 1,
            },
        )
        self.store.append_session_event(
            running["session"]["id"], kind="agent_event",
            payload={"type": "agent_event", "kind": "model_generation_start"},
            task_id=running_task["id"], dispatch_id=running["dispatch"]["id"],
            workspace_id=running["dispatch"]["workspace_id"],
        )
        verification = self.store.create_verification_run(
            task_id=running_task["id"], dispatch_id=running["dispatch"]["id"],
            kind="test", command="pytest -q", trigger="agent", head_commit="abc",
            revision="abc", agent_claim="unknown",
        )
        self.store.start_verification_run(verification["id"])

        response = self.client.get(
            f"/operations/dashboard?project_id={self.project['id']}",
            headers=self.headers,
        )
        self.assertEqual(200, response.status_code, response.text)
        snapshot = response.json()
        self.assertEqual(10, snapshot["summary"]["total"])
        self.assertEqual(
            {"queue": 4, "working": 3, "needs_you": 1, "review": 1, "failed": 1},
            snapshot["summary"]["lanes"],
        )
        self.assertEqual(3, snapshot["summary"]["attention"])
        self.assertIn("model_generation", snapshot["scheduler"]["resources"])
        self.assertGreater(snapshot["memory"]["janus_process_peak_rss_bytes"], 0)

        running_row = next(item for item in snapshot["tasks"] if item["id"] == running_task["id"])
        self.assertEqual(100.0, running_row["budget_progress"]["steps"])
        self.assertEqual(50.0, running_row["budget_progress"]["time"])
        self.assertEqual(
            {"generation", "verification"},
            {item["category"] for item in running_row["timeline"]},
        )


if __name__ == "__main__":
    unittest.main()
