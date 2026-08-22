"""P5 diagnostic bundles stay useful, bounded, and secret-free."""

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from janus_server import diagnostics


class DiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.database = self.root / "janus.sqlite3"
        connection = sqlite3.connect(self.database)
        connection.execute("PRAGMA user_version=11")
        connection.execute("CREATE TABLE healthy(id INTEGER PRIMARY KEY)")
        connection.close()
        self.logs = self.root / "logs"
        self.logs.mkdir()

    def tearDown(self):
        self.temp.cleanup()

    def test_bundle_redacts_secrets_bounds_logs_and_excludes_database(self):
        secret = "super-secret-token-value"
        (self.logs / "janus-server.log").write_text(
            "x" * (diagnostics.MAX_LOG_BYTES + 100)
            + f"\n[janus] generated auth token: {secret}\n"
            + f"Authorization: Bearer {secret}\n",
            encoding="utf-8",
        )
        created = diagnostics.create_diagnostic_bundle(
            database=self.database, log_dir=self.logs,
            output_dir=self.root / "diagnostics",
        )

        with zipfile.ZipFile(created["path"]) as archive:
            names = archive.namelist()
            combined = "\n".join(
                archive.read(name).decode("utf-8", errors="replace") for name in names
            )
            manifest = json.loads(archive.read("manifest.json"))
        self.assertNotIn(secret, combined)
        self.assertIn("[REDACTED]", combined)
        self.assertNotIn("janus.sqlite3", names)
        self.assertFalse(manifest["privacy"]["database_included"])
        self.assertTrue(manifest["logs"][0]["truncated"])
        self.assertLessEqual(manifest["logs"][0]["included_bytes"], diagnostics.MAX_LOG_BYTES)
        self.assertEqual(11, manifest["schema_version"])
        self.assertEqual(0o600, Path(created["path"]).stat().st_mode & 0o777)

    def test_plain_redaction_hides_home_and_common_secret_forms(self):
        value = diagnostics.redact(
            f'{Path.home()}/work x-janus-token=abc password: hunter2 "token": "json-secret"'
        )
        self.assertNotIn(str(Path.home()), value)
        self.assertNotIn("abc", value)
        self.assertNotIn("hunter2", value)
        self.assertNotIn("json-secret", value)


if __name__ == "__main__":
    unittest.main()
