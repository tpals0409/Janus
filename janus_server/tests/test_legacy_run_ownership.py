"""Legacy Agent slug reuse cannot inherit another instance's run history."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import server


class LegacyRunOwnershipTests(unittest.TestCase):
    def test_deleted_agent_slug_reuse_gets_a_new_immutable_owner(self):
        client = TestClient(server.app)
        headers = {"x-janus-token": server.AUTH_TOKEN}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            runs = root / "runs"
            agents.mkdir()
            with patch.object(server, "AGENTS_DIR", agents), patch.object(server, "RUNS_DIR", runs):
                first = client.post(
                    "/agents", headers=headers, json={"name": "Repeat Agent"}
                ).json()
                owner_one = first["spec"]["_instance_id"]
                owner_dir = runs / owner_one
                owner_dir.mkdir(parents=True)
                (owner_dir / "old.json").write_text(
                    json.dumps({"id": "old", "summary": "old owner"}), encoding="utf-8"
                )

                forged = {**first["spec"], "_instance_id": "agent_instance_000000000000000000000000"}
                updated = client.put(
                    f"/agents/{first['id']}", headers=headers, json={"spec": forged}
                )
                self.assertEqual(200, updated.status_code, updated.text)
                reread = client.get(
                    f"/agents/{first['id']}", headers=headers
                ).json()
                self.assertEqual(owner_one, reread["spec"]["_instance_id"])

                client.delete(f"/agents/{first['id']}", headers=headers)
                second = client.post(
                    "/agents", headers=headers, json={"name": "Repeat Agent"}
                ).json()
                owner_two = second["spec"]["_instance_id"]

                self.assertEqual(first["id"], second["id"])
                self.assertNotEqual(owner_one, owner_two)
                self.assertEqual(
                    [], client.get(f"/runs/{second['id']}", headers=headers).json()
                )
                self.assertTrue((owner_dir / "old.json").is_file())

        client.close()


if __name__ == "__main__":
    unittest.main()
