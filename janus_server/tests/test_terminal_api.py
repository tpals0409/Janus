"""Task-scoped split PTY terminals and reconnectable output buffers."""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import scheduler, server


class TerminalApiTests(unittest.TestCase):
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
        server._TERMINAL_MANAGER = None
        server._TERMINAL_MANAGER_PATH = None
        scheduler._DEFAULT_SCHEDULER = scheduler.ResourceScheduler()
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}
        self.store = server.get_domain_store()
        self.project = self.store.create_project(
            name="Terminal", repo_path=str(self.root / "repo")
        )

    def tearDown(self):
        if server._TERMINAL_MANAGER is not None:
            server._TERMINAL_MANAGER.stop_all()
        self.client.close()
        server._TERMINAL_MANAGER = None
        server._TERMINAL_MANAGER_PATH = None
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        self.env.stop()
        self.temp.cleanup()

    def ready_task(self, name: str) -> tuple[dict, Path]:
        task = self.store.create_task(
            project_id=self.project["id"], title=name, objective=f"Work on {name}",
            acceptance_command="true", base_ref="main",
        )
        root = self.root / name.replace(" ", "-")
        root.mkdir()
        workspace = self.store.create_workspace(
            task_id=task["id"], repo_path=self.project["repo_path"], base_ref="main"
        )
        self.store.transition_workspace(
            workspace["id"], "ready", root_path=str(root),
            branch_name=f"janus/{name.replace(' ', '-').lower()}",
        )
        return task, root

    def wait_for(
        self, task_id: str, terminal_id: str, text: str | tuple[str, ...]
    ) -> dict:
        deadline = time.monotonic() + 4
        item = {}
        expected = (text,) if isinstance(text, str) else text
        while time.monotonic() < deadline:
            item = self.client.get(
                f"/tasks/{task_id}/terminals/{terminal_id}?after_offset=0",
                headers=self.headers,
            ).json()
            if all(value in item.get("output", "") for value in expected):
                return item
            time.sleep(0.03)
        self.fail(f"terminal output missing {expected!r}: {item}")

    def test_split_terminals_stay_in_worktree_and_restore_buffer(self):
        task, root = self.ready_task("First Task")
        primary = self.client.post(
            f"/tasks/{task['id']}/terminals", headers=self.headers,
            json={"pane_id": "primary"},
        )
        secondary = self.client.post(
            f"/tasks/{task['id']}/terminals", headers=self.headers,
            json={"pane_id": "secondary"},
        )
        self.assertEqual(200, primary.status_code, primary.text)
        self.assertEqual(200, secondary.status_code, secondary.text)
        self.assertNotEqual(primary.json()["id"], secondary.json()["id"])
        self.assertTrue(Path(primary.json()["cwd"]).samefile(root))

        terminal_id = primary.json()["id"]
        marker = "JANUS_TERMINAL_RESTORE_MARKER"
        sent = self.client.post(
            f"/tasks/{task['id']}/terminals/{terminal_id}/input",
            headers=self.headers, json={"data": f"pwd; printf '{marker}\\n'\n"},
        )
        self.assertEqual(200, sent.status_code, sent.text)
        output = self.wait_for(task["id"], terminal_id, (root.name, marker))
        self.assertIn(root.name, output["output"])
        offset = output["output_offset"]

        restored = self.client.get(
            f"/tasks/{task['id']}/terminals", headers=self.headers
        ).json()
        self.assertEqual({"primary", "secondary"}, {item["pane_id"] for item in restored})
        self.assertIn(marker, next(item for item in restored if item["id"] == terminal_id)["output"])
        delta = self.client.get(
            f"/tasks/{task['id']}/terminals/{terminal_id}?after_offset={offset}",
            headers=self.headers,
        ).json()
        self.assertEqual("", delta["output"])

    def test_terminal_id_cannot_cross_task_boundary_and_stop_is_explicit(self):
        first, _root = self.ready_task("One")
        second, _other_root = self.ready_task("Two")
        terminal = self.client.post(
            f"/tasks/{first['id']}/terminals", headers=self.headers,
            json={"pane_id": "primary"},
        ).json()
        crossed = self.client.post(
            f"/tasks/{second['id']}/terminals/{terminal['id']}/input",
            headers=self.headers, json={"data": "pwd\n"},
        )
        self.assertEqual(409, crossed.status_code)
        stopped = self.client.delete(
            f"/tasks/{first['id']}/terminals/{terminal['id']}", headers=self.headers
        )
        self.assertEqual(200, stopped.status_code, stopped.text)
        self.assertEqual("stopped", stopped.json()["state"])
        denied = self.client.post(
            f"/tasks/{first['id']}/terminals/{terminal['id']}/input",
            headers=self.headers, json={"data": "pwd\n"},
        )
        self.assertEqual(409, denied.status_code)

    def test_monaco_file_search_atomic_save_and_stale_guard_are_task_scoped(self):
        task, root = self.ready_task("Editor")
        source = root / "src"
        source.mkdir()
        target = source / "app.ts"
        target.write_text("export const answer = 41\n", encoding="utf-8")

        listed = self.client.get(
            f"/tasks/{task['id']}/development/files?path=src", headers=self.headers
        )
        self.assertEqual(200, listed.status_code, listed.text)
        self.assertEqual(["src/app.ts"], [item["path"] for item in listed.json()["entries"]])
        searched = self.client.get(
            f"/tasks/{task['id']}/development/search?q=answer", headers=self.headers
        )
        self.assertEqual(200, searched.status_code, searched.text)
        self.assertEqual("src/app.ts", searched.json()["matches"][0]["path"])

        opened = self.client.get(
            f"/tasks/{task['id']}/development/file?path=src/app.ts", headers=self.headers
        ).json()
        target.write_text("external change\n", encoding="utf-8")
        stale = self.client.put(
            f"/tasks/{task['id']}/development/file", headers=self.headers, json={
                "path": "src/app.ts", "content": "export const answer = 42\n",
                "expected_mtime_ns": opened["mtime_ns"],
            },
        )
        self.assertEqual(409, stale.status_code)
        self.assertEqual("external change\n", target.read_text(encoding="utf-8"))

        refreshed = self.client.get(
            f"/tasks/{task['id']}/development/file?path=src/app.ts", headers=self.headers
        ).json()
        with patch.object(server.os, "replace", side_effect=OSError("disk full")):
            failed = self.client.put(
                f"/tasks/{task['id']}/development/file", headers=self.headers, json={
                    "path": "src/app.ts", "content": "must not publish\n",
                    "expected_mtime_ns": refreshed["mtime_ns"],
                },
            )
        self.assertEqual(409, failed.status_code, failed.text)
        self.assertEqual("external change\n", target.read_text(encoding="utf-8"))
        self.assertFalse(any(path.name.endswith(".tmp") for path in source.iterdir()))

        saved = self.client.put(
            f"/tasks/{task['id']}/development/file", headers=self.headers, json={
                "path": "src/app.ts", "content": "export const answer = 42\n",
                "expected_mtime_ns": refreshed["mtime_ns"],
            },
        )
        self.assertEqual(200, saved.status_code, saved.text)
        self.assertEqual("export const answer = 42\n", target.read_text(encoding="utf-8"))
        self.assertFalse(any(path.name.endswith(".tmp") for path in source.iterdir()))

        escaped = self.client.get(
            f"/tasks/{task['id']}/development/file?path=../secret", headers=self.headers
        )
        self.assertEqual(409, escaped.status_code)


if __name__ == "__main__":
    unittest.main()
