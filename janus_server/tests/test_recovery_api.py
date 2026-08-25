"""Authenticated maintenance API for integrity and backup operations."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from fastapi.testclient import TestClient

from janus_server import server, shared


class RecoveryApiTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.database = root / "janus.sqlite3"
        self.backups = root / "backups"
        self.logs = root / "logs"
        self.diagnostics = root / "diagnostics"
        self.logs.mkdir()
        self.environment = patch.dict(os.environ, {
            "JANUS_DB_FILE": str(self.database),
            "JANUS_BACKUPS_DIR": str(self.backups),
            "JANUS_LOG_DIR": str(self.logs),
            "JANUS_DIAGNOSTICS_DIR": str(self.diagnostics),
        })
        self.environment.start()
        shared._DOMAIN_STORE = None
        shared._DOMAIN_STORE_PATH = None
        shared._DOMAIN_RECOVERED_PATH = None
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}

    def tearDown(self):
        shared._DOMAIN_STORE = None
        shared._DOMAIN_STORE_PATH = None
        shared._DOMAIN_RECOVERED_PATH = None
        self.environment.stop()
        self.temp.cleanup()

    def test_integrity_policy_and_online_backup_are_exposed(self):
        status = self.client.get("/maintenance/recovery", headers=self.headers)
        self.assertEqual(200, status.status_code, status.text)
        self.assertTrue(status.json()["database"]["ok"])
        self.assertFalse(status.json()["policy"]["automatic_reset"])

        created = self.client.post(
            "/maintenance/backups", json={"retain": 2}, headers=self.headers,
        )
        self.assertEqual(201, created.status_code, created.text)
        self.assertTrue(created.json()["integrity"]["ok"])
        self.assertTrue(Path(created.json()["path"]).is_file())

    def test_backup_write_failure_is_recoverable_conflict(self):
        self.client.get("/health", headers=self.headers)
        with patch.object(
            server.recovery, "create_database_backup", side_effect=OSError("disk full")
        ):
            response = self.client.post(
                "/maintenance/backups", json={}, headers=self.headers,
            )
        self.assertEqual(409, response.status_code, response.text)
        self.assertIn("storage_write", response.json()["detail"])

    def test_diagnostics_api_creates_redacted_bundle(self):
        (self.logs / "janus-server.log").write_text(
            "generated auth token: do-not-leak", encoding="utf-8"
        )
        response = self.client.post("/maintenance/diagnostics", headers=self.headers)
        self.assertEqual(201, response.status_code, response.text)
        self.assertTrue(response.json()["redacted"])
        self.assertTrue(Path(response.json()["path"]).is_file())


if __name__ == "__main__":
    unittest.main()
