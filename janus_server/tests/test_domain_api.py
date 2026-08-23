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
    def test_skill_library_import_and_agent_activation_api(self):
        with domain_api() as (client, path):
            root = path.parent / "skill-pack"
            skill = root / "skills" / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review changes\nallowed-tools: Read, Grep\n---\nReview $ARGUMENTS.\n",
                encoding="utf-8",
            )

            imported = client.post(
                "/skills/import/local", headers=HEADERS,
                json={"path": str(root), "source_kind": "claude"},
            )
            self.assertEqual(200, imported.status_code, imported.text)
            created = imported.json()["skills"][0]
            self.assertEqual("claude", created["source_kind"])
            self.assertEqual("native", created["compatibility"])

            library = client.get("/skills", headers=HEADERS).json()
            self.assertEqual(["review"], [item["name"] for item in library])
            activated = client.put(
                f"/profiles/agents/agent_default/skills/{created['skill_id']}",
                headers=HEADERS, json={"activation_mode": "auto"},
            )
            self.assertEqual(200, activated.status_code, activated.text)
            assigned = client.get(
                "/profiles/agents/agent_default/skills", headers=HEADERS,
            ).json()
            self.assertEqual("auto", assigned[0]["activation_mode"])
            self.assertEqual(created["id"], assigned[0]["skill_version_id"])

    def test_github_skill_import_records_revision_and_provenance(self):
        with domain_api() as (client, path):
            root = path.parent / "github-source"
            skill = root / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: github-review\ndescription: Review code\n---\nReview code.\n",
                encoding="utf-8",
            )
            revision = "c" * 40
            source = {
                "owner": "acme", "repository": "skills", "ref": None,
                "subpath": "", "canonical_url": "https://github.com/acme/skills",
                "requested_ref": "main", "revision": revision, "license": "MIT",
                "root": root, "skill_directories": [skill],
            }
            with patch.object(server.skill_mod, "download_github_skills", return_value=source):
                preview = client.post(
                    "/skills/preview/github", headers=HEADERS,
                    json={"url": "https://github.com/acme/skills"},
                )
                self.assertEqual(200, preview.status_code, preview.text)
                self.assertEqual(revision, preview.json()["revision"])
                self.assertEqual(["review"], [item["source_subpath"] for item in preview.json()["skills"]])

                changed = client.post(
                    "/skills/import/github", headers=HEADERS,
                    json={
                        "url": "https://github.com/acme/skills",
                        "expected_revision": "d" * 40,
                        "selected_subpaths": ["review"],
                    },
                )
                self.assertEqual(409, changed.status_code, changed.text)

                imported = client.post(
                    "/skills/import/github", headers=HEADERS,
                    json={
                        "url": "https://github.com/acme/skills",
                        "expected_revision": revision,
                        "selected_subpaths": ["review"],
                    },
                )
            self.assertEqual(200, imported.status_code, imported.text)
            created = imported.json()["skills"][0]
            self.assertEqual(revision, created["source_revision"])
            self.assertEqual("github-acme-skills", created["namespace"])
            detail = client.get(f"/skills/{created['skill_id']}", headers=HEADERS).json()
            self.assertEqual("MIT", detail["original"]["github"]["license"])

    def test_same_named_local_skills_from_different_sources_do_not_collide(self):
        with domain_api() as (client, path):
            roots = [path.parent / "first-source", path.parent / "second-source"]
            created = []
            for root in roots:
                skill = root / "review"
                skill.mkdir(parents=True)
                (skill / "SKILL.md").write_text(
                    "---\nname: review\ndescription: Review code\n---\nReview.\n",
                    encoding="utf-8",
                )
                response = client.post(
                    "/skills/import/local", headers=HEADERS, json={"path": str(root)},
                )
                self.assertEqual(200, response.status_code, response.text)
                created.append(response.json()["skills"][0])

            self.assertNotEqual(created[0]["skill_id"], created[1]["skill_id"])
            self.assertNotEqual(created[0]["namespace"], created[1]["namespace"])

    def test_agents_skill_folder_is_recognized_as_codex_source(self):
        with domain_api() as (client, path):
            root = path.parent / ".agents" / "skills"
            skill = root / "review"
            skill.mkdir(parents=True)
            (skill / "SKILL.md").write_text(
                "---\nname: review\ndescription: Review code\n---\nReview.\n",
                encoding="utf-8",
            )
            response = client.post(
                "/skills/import/local", headers=HEADERS, json={"path": str(root)},
            )
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual("codex", response.json()["skills"][0]["source_kind"])

    def test_adding_same_project_selects_existing_and_restores_archived_project(self):
        with domain_api() as (client, path):
            repo = path.parent / "same-repo"
            repo.mkdir()
            payload = {"name": "Same", "repo_path": str(repo)}

            first = client.post("/projects", headers=HEADERS, json=payload)
            duplicate = client.post("/projects", headers=HEADERS, json=payload)
            self.assertEqual(200, duplicate.status_code)
            self.assertEqual(first.json()["id"], duplicate.json()["id"])
            self.assertEqual(1, len(client.get("/projects", headers=HEADERS).json()))

            client.delete(f"/projects/{first.json()['id']}", headers=HEADERS)
            restored = client.post("/projects", headers=HEADERS, json=payload)
            self.assertEqual(200, restored.status_code)
            self.assertEqual(first.json()["id"], restored.json()["id"])
            self.assertIsNone(restored.json()["archived_at"])
            self.assertEqual(1, len(client.get("/projects", headers=HEADERS).json()))

    def test_project_file_explorer_is_scoped_to_selected_project(self):
        with domain_api() as (client, path):
            repo = path.parent / "repo"
            (repo / "src").mkdir(parents=True)
            (repo / "src" / "main.ts").write_text("export const ready = true\n")
            (repo / ".secret").write_text("hidden")
            project = client.post(
                "/projects", headers=HEADERS,
                json={"name": "Explorer", "repo_path": str(repo)},
            ).json()

            root = client.get(
                f"/projects/{project['id']}/tree", headers=HEADERS,
            )
            self.assertEqual(200, root.status_code)
            self.assertEqual([{"name": "src", "type": "dir", "size": None}], root.json()["entries"])

            source = client.get(
                f"/projects/{project['id']}/file", headers=HEADERS,
                params={"path": "src/main.ts"},
            )
            self.assertEqual("export const ready = true\n", source.json()["content"])
            escaped = client.get(
                f"/projects/{project['id']}/file", headers=HEADERS,
                params={"path": "../outside.txt"},
            )
            self.assertEqual(400, escaped.status_code)

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

            archived = client.delete(
                f"/projects/{project['id']}", headers=HEADERS,
            )
            self.assertEqual(200, archived.status_code)
            self.assertIsNotNone(archived.json()["archived_at"])
            self.assertEqual([], client.get("/projects", headers=HEADERS).json())
            with_archived = client.get(
                "/projects?include_archived=true", headers=HEADERS,
            ).json()
            self.assertEqual([project["id"]], [item["id"] for item in with_archived])

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
