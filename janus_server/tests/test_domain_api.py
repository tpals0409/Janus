"""P1 domain API가 DB를 사용하고 backend 재시작 후 복원되는지 검증한다."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
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
            self.assertCountEqual(
                ["clear", "compact", "interview", "review"],
                [item["name"] for item in library],
            )
            activated = client.put(
                f"/profiles/agents/agent_default/skills/{created['skill_id']}",
                headers=HEADERS, json={"activation_mode": "auto"},
            )
            self.assertEqual(200, activated.status_code, activated.text)
            assigned = client.get(
                "/profiles/agents/agent_default/skills", headers=HEADERS,
            ).json()
            review = next(item for item in assigned if item["name"] == "review")
            interview = next(item for item in assigned if item["name"] == "interview")
            clear = next(item for item in assigned if item["name"] == "clear")
            compact = next(item for item in assigned if item["name"] == "compact")
            self.assertEqual("auto", review["activation_mode"])
            self.assertEqual(created["id"], review["skill_version_id"])
            self.assertEqual("manual", interview["activation_mode"])
            self.assertTrue(interview["compiled"]["activation"]["user_invocable"])
            self.assertEqual("manual", clear["activation_mode"])
            self.assertEqual("manual", compact["activation_mode"])

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

    def test_project_delegation_infers_internal_task_contract(self):
        with domain_api() as (client, path):
            repo = path.parent / "delegated-repo"
            repo.mkdir()
            subprocess.run(
                ["git", "init", "-b", "develop", str(repo)],
                check=True, capture_output=True, text=True,
            )
            (repo / "package.json").write_text(
                json.dumps({"scripts": {"test": "vitest run"}}), encoding="utf-8",
            )
            (repo / "pnpm-lock.yaml").write_text("lockfileVersion: '9.0'\n", encoding="utf-8")
            project = client.post(
                "/projects", headers=HEADERS,
                json={"name": "Delegate", "repo_path": str(repo)},
            ).json()

            delegated = client.post(
                f"/projects/{project['id']}/delegations", headers=HEADERS,
                json={"objective": "인증 오류를 조사하고 수정해줘\n재현 테스트도 추가해줘"},
            )
            self.assertEqual(200, delegated.status_code, delegated.text)
            task = delegated.json()
            self.assertEqual("인증 오류를 조사하고 수정", task["title"])
            self.assertEqual("develop", task["base_ref"])
            self.assertEqual("pnpm test", task["acceptance_command"])
            self.assertEqual("todo", task["status"])
            self.assertEqual("direct", task["workflow_stage"])

            concise = client.post(
                f"/projects/{project['id']}/delegations", headers=HEADERS,
                json={"objective": "일단 에이전트 페이지에서 UI, UX 관점의 개선점을 말해줘"},
            )
            self.assertEqual(200, concise.status_code, concise.text)
            self.assertEqual(
                "에이전트 페이지에서 UI, UX 관점의 개선점",
                concise.json()["title"],
            )

            mockup = client.post(
                f"/projects/{project['id']}/delegations", headers=HEADERS,
                json={"objective": "프론트 화면을 만들어줘", "workflow_stage": "mockup"},
            )
            self.assertEqual(200, mockup.status_code, mockup.text)
            task = mockup.json()
            self.assertEqual("mockup", task["workflow_stage"])
            premature = client.post(
                f"/tasks/{task['id']}/mockup/reject", headers=HEADERS,
                json={"feedback": "아직 검토 요청 전"},
            )
            self.assertEqual(409, premature.status_code, premature.text)

            store = domain.DomainStore(path)
            workspace = store.create_workspace(
                task_id=task["id"], repo_path=str(repo), base_ref="develop",
            )
            store.transition_workspace(
                workspace["id"], "ready", root_path=str(repo), branch_name="develop",
            )
            execution = store.create_execution(
                task_id=task["id"], workspace_id=workspace["id"],
                agent_profile_id="agent_default",
            )
            store.transition_dispatch(execution["dispatch"]["id"], "running")
            store.transition_session(execution["session"]["id"], "running")
            store.settle_session_turn(execution["session"]["id"], outcome="mockup_review")
            rejected = client.post(
                f"/tasks/{task['id']}/mockup/reject", headers=HEADERS,
                json={"feedback": "버튼 대비를 높여주세요"},
            )
            self.assertEqual(200, rejected.status_code, rejected.text)
            self.assertEqual("버튼 대비를 높여주세요", rejected.json()["mockup_feedback"])
            detail = client.get(
                f"/sessions/{execution['session']['id']}", headers=HEADERS,
            )
            self.assertEqual([], detail.json()["approval_scopes"])
            store.grant_session_approval_scope(
                execution["session"]["id"], workspace["id"], "workspace_write",
            )
            revoked = client.delete(
                f"/sessions/{execution['session']['id']}/approvals/workspace_write",
                headers=HEADERS, params={"workspace_id": workspace["id"]},
            )
            self.assertEqual(200, revoked.status_code, revoked.text)
            self.assertEqual(
                [], client.get(
                    f"/sessions/{execution['session']['id']}", headers=HEADERS,
                ).json()["approval_scopes"],
            )
            approved = client.post(
                f"/tasks/{task['id']}/mockup/approve", headers=HEADERS,
            )
            self.assertEqual(200, approved.status_code, approved.text)
            self.assertEqual("implementation", approved.json()["workflow_stage"])

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
            self.assertIn("You are Janus", agents[0]["base_system_prompt"])
            self.assertIn("# Coding Rules", agents[0]["coding_rules_prompt"])
            self.assertEqual(
                agents[0]["base_system_prompt"], agents[0]["effective_system_prompt"]
            )
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


    def test_every_agent_profile_can_name_the_packaged_skills(self):
        with domain_api() as (client, _):
            created = client.post(
                "/profiles/agents", headers=HEADERS,
                json={"name": "Second", "system_prompt": "너는 Janus다.", "tools": []},
            )
            self.assertEqual(200, created.status_code, created.text)
            listed = client.get(
                f"/profiles/agents/{created.json()['id']}/skills", headers=HEADERS,
            ).json()
            self.assertEqual(
                ["clear", "compact", "interview"],
                sorted(item["name"] for item in listed),
            )
            self.assertEqual({"manual"}, {item["activation_mode"] for item in listed})

    def test_a_packaged_skill_turned_off_stays_off_after_restart(self):
        with domain_api() as (client, _):
            listed = client.get(
                "/profiles/agents/agent_default/skills", headers=HEADERS,
            ).json()
            target = next(item for item in listed if item["name"] == "clear")
            turned_off = client.put(
                f"/profiles/agents/agent_default/skills/{target['skill_id']}",
                headers=HEADERS, json={"activation_mode": "off"},
            )
            self.assertEqual(200, turned_off.status_code, turned_off.text)
            server._ensure_packaged_skills(server.get_domain_store())
            after = client.get(
                "/profiles/agents/agent_default/skills", headers=HEADERS,
            ).json()
            modes = {item["name"]: item["activation_mode"] for item in after}
            self.assertEqual("off", modes["clear"])
            self.assertEqual("manual", modes["compact"])


class GracefulShutdownTest(unittest.TestCase):
    def test_main_bounds_graceful_shutdown_under_supervisor_grace(self):
        """SIGTERM 뒤 5초(supervisor grace) 안에 끝나야 SIGKILL을 안 맞는다."""
        self.assertLess(server.GRACEFUL_SHUTDOWN_SECONDS * 1000, 5000)
        captured = {}
        real = sys.modules.get("uvicorn")
        fake = types.ModuleType("uvicorn")
        fake.run = lambda app, **kwargs: captured.update(kwargs)
        sys.modules["uvicorn"] = fake
        try:
            server.main()
        finally:
            if real is None:
                del sys.modules["uvicorn"]
            else:
                sys.modules["uvicorn"] = real
        self.assertEqual(
            server.GRACEFUL_SHUTDOWN_SECONDS, captured["timeout_graceful_shutdown"]
        )


if __name__ == "__main__":
    unittest.main()
