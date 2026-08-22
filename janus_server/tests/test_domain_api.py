"""P1 domain API가 DB를 사용하고 backend 재시작 후 복원되는지 검증한다."""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import domain, server


HEADERS = {"x-janus-token": server.AUTH_TOKEN}


@contextmanager
def domain_api():
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "janus.sqlite3"
        with (
            patch.dict(os.environ, {"JANUS_DB_FILE": str(path)}),
            patch.object(server, "_DOMAIN_STORE", None),
            patch.object(server, "_DOMAIN_STORE_PATH", None),
        ):
            yield TestClient(server.app), path


class DomainApiTests(unittest.TestCase):
    def test_project_task_profile_crud_and_transition(self):
        with domain_api() as (client, _path):
            health = client.get("/health", headers=HEADERS)
            self.assertEqual(domain.CURRENT_SCHEMA_VERSION, health.json()["schema_version"])

            project = client.post(
                "/projects", headers=HEADERS,
                json={"name": "Janus", "repo_path": "/tmp/janus-api-repo"},
            ).json()
            task = client.post(
                f"/projects/{project['id']}/tasks", headers=HEADERS,
                json={
                    "title": "API task", "objective": "Persist me",
                    "acceptance_command": "python -m unittest", "base_ref": "main",
                },
            ).json()
            self.assertEqual("todo", task["status"])
            moved = client.post(
                f"/tasks/{task['id']}/transition", headers=HEADERS,
                json={"status": "preparing", "expected": "todo"},
            )
            self.assertEqual(200, moved.status_code)
            stale = client.post(
                f"/tasks/{task['id']}/transition", headers=HEADERS,
                json={"status": "working", "expected": "todo"},
            )
            self.assertEqual(409, stale.status_code)

            agents = client.get("/profiles/agents", headers=HEADERS).json()
            models = client.get("/profiles/models", headers=HEADERS).json()
            self.assertEqual("agent_default", agents[0]["id"])
            self.assertIn("read_file", agents[0]["tools"])
            self.assertEqual("qwen3.8-27b", models[0]["model_key"])

    def test_backend_restart_reopens_same_database(self):
        with domain_api() as (client, path):
            project = client.post(
                "/projects", headers=HEADERS,
                json={"name": "Restart", "repo_path": "/tmp/janus-restart-repo"},
            ).json()
            self.assertTrue(path.is_file())
            server._DOMAIN_STORE = None
            server._DOMAIN_STORE_PATH = None
            restored = client.get("/projects", headers=HEADERS).json()
            self.assertEqual([project["id"]], [item["id"] for item in restored])

    def test_domain_routes_still_require_authentication(self):
        with domain_api() as (client, _path):
            self.assertEqual(401, client.get("/projects").status_code)


if __name__ == "__main__":
    unittest.main()
