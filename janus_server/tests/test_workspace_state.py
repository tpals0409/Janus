"""워크스페이스 영속화와 안전한 폴립 회귀 테스트."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")
os.environ.setdefault(
    "JANUS_STATE_FILE",
    str(Path(tempfile.gettempdir()) / f"janus-workspace-test-{os.getpid()}.json"),
)

from fastapi.testclient import TestClient

from janus_server import server
from janus_server import tools as T


class WorkspaceStateTests(unittest.TestCase):
    def setUp(self):
        self.previous_workspace = T.WORKSPACE
        self.previous_state_file = server.STATE_FILE
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.state_file = root / "settings" / "state.json"
        self.project = root / "project"
        self.fallback = root / "fallback"
        self.project.mkdir()
        self.fallback.mkdir()
        server.STATE_FILE = self.state_file
        T.set_workspace(str(self.fallback))

    def tearDown(self):
        T.WORKSPACE = self.previous_workspace
        server.STATE_FILE = self.previous_state_file
        self.temp.cleanup()

    def test_persist_is_atomic_private_and_restorable(self):
        self.state_file.parent.mkdir(parents=True)
        self.state_file.write_text('{"future_setting": 7}\n', encoding="utf-8")

        server._persist_workspace(self.project)

        state = json.loads(self.state_file.read_text(encoding="utf-8"))
        self.assertEqual(7, state["future_setting"])
        self.assertEqual(str(self.project), state["workspace"])
        self.assertEqual(0o600, stat.S_IMODE(self.state_file.stat().st_mode))
        self.assertEqual([], list(self.state_file.parent.glob("*.tmp")))

        T.set_workspace(str(self.fallback))
        self.assertTrue(server._restore_workspace())
        self.assertEqual(self.project.resolve(), T.get_workspace())

    def test_invalid_saved_workspace_keeps_safe_fallback(self):
        missing = Path(self.temp.name) / "deleted-project"
        self.state_file.parent.mkdir(parents=True)
        self.state_file.write_text(
            json.dumps({"workspace": str(missing)}), encoding="utf-8"
        )

        self.assertFalse(server._restore_workspace())
        self.assertEqual(self.fallback.resolve(), T.get_workspace())

    def test_api_rolls_back_when_state_write_fails(self):
        client = TestClient(server.app)
        headers = {"x-janus-token": server.AUTH_TOKEN}

        with patch.object(server, "_persist_workspace", side_effect=OSError("disk full")):
            response = client.post(
                "/workspace", json={"path": str(self.project)}, headers=headers
            )

        self.assertEqual(500, response.status_code)
        self.assertEqual(self.fallback.resolve(), T.get_workspace())


if __name__ == "__main__":
    unittest.main()
