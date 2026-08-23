"""Persistent Task–Dispatch–AgentSession runtime integration tests."""

from __future__ import annotations

import os
import asyncio
import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from fastapi.testclient import TestClient

from janus_server import domain, runtime, server
from janus_server.scheduler import ResourceClass, ResourceScheduler
from tests.fakes import FakeClient, text_chunk

ORIGIN = "http://localhost:5173"


class TaskRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.env = patch.dict(os.environ, {
            "JANUS_DB_FILE": str(root / "janus.sqlite3"),
            "JANUS_WORKTREES_DIR": str(root / "workspaces"),
        })
        self.env.start()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        with server._TASK_RUNTIMES_LOCK:
            server._TASK_RUNTIMES.clear()
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}
        self.store = server.get_domain_store()
        self.project = self.store.create_project(
            name="Runtime", repo_path=str(root / "repo")
        )
        self.root = root

    def tearDown(self):
        self.client.close()
        with server._TASK_RUNTIMES_LOCK:
            for orch in server._TASK_RUNTIMES.values():
                orch.cancel_all()
            server._TASK_RUNTIMES.clear()
        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        self.env.stop()
        self.temp.cleanup()

    def create_ready_task(self, title: str) -> dict:
        task = self.store.create_task(
            project_id=self.project["id"],
            title=title,
            objective=f"Complete {title}",
            acceptance_command="true",
            base_ref="main",
        )
        workspace_root = self.root / f"workspace-{task['id']}"
        workspace_root.mkdir()
        workspace = self.store.create_workspace(
            task_id=task["id"], repo_path=self.project["repo_path"], base_ref="main"
        )
        self.store.transition_workspace(
            workspace["id"], "ready",
            root_path=str(workspace_root), branch_name=f"janus/{task['id']}",
        )
        return task

    def start(self, task_id: str) -> dict:
        response = self.client.post(
            f"/tasks/{task_id}/sessions",
            headers=self.headers,
            json={"agent_profile_id": "agent_default"},
        )
        self.assertEqual(200, response.status_code, response.text)
        return response.json()

    def test_app_shutdown_cancels_runtime_and_waits_for_active_lease_return(self):
        scheduler = ResourceScheduler()
        cancel = threading.Event()
        acquired = threading.Event()

        class LiveRuntime:
            def cancel_all(self) -> None:
                cancel.set()

        def active_work() -> None:
            with scheduler.acquire(ResourceClass.MODEL_GENERATION, cancel=cancel):
                acquired.set()
                cancel.wait(2)

        worker = threading.Thread(target=active_work)
        worker.start()
        self.assertTrue(acquired.wait(2))
        with server._TASK_RUNTIMES_LOCK:
            server._TASK_RUNTIMES["shutdown-test"] = LiveRuntime()  # type: ignore[assignment]

        idle = asyncio.run(server.shutdown_local_resources(scheduler, timeout=2))
        worker.join(2)

        self.assertTrue(idle)
        self.assertTrue(cancel.is_set())
        self.assertEqual(0, scheduler.snapshot()["active_leases"])

    def connect(self, task_id: str, session_id: str):
        return self.client.websocket_connect(
            f"/tasks/{task_id}/sessions/{session_id}",
            headers={"origin": ORIGIN},
            subprotocols=["janus", "test-token"],
        )

    def drain_turn(self, ws) -> list[dict]:
        seen = []
        while True:
            event = ws.receive_json()
            seen.append(event)
            if event["type"] == "turn_end":
                return seen

    def test_agent_context_policy_controls_snapshot_and_runtime_prompt(self):
        updated = self.client.put(
            "/profiles/agents/agent_default", headers=self.headers,
            json={
                "system_prompt": "Agent policy marker.",
                "context_policy": {
                    "max_chars": 12_000,
                    "recent_blocks": 3,
                    "summary_max_chars": 1_000,
                    "include_task_objective": True,
                    "include_acceptance": False,
                    "include_workspace_root": True,
                },
            },
        )
        self.assertEqual(200, updated.status_code, updated.text)
        self.assertEqual(12_000, updated.json()["context_policy"]["max_chars"])

        task = self.create_ready_task("Context policy")
        detail = self.start(task["id"])
        self.assertEqual(12_000, detail["context"]["policy"]["max_chars"])
        statuses = {item["id"]: item["status"] for item in detail["context"]["items"]}
        self.assertEqual("included", statuses["agent_prompt"])
        self.assertEqual("included", statuses["task_objective"])
        self.assertEqual("excluded", statuses["acceptance"])
        self.assertEqual("included", statuses["workspace_root"])

        changed = self.client.put(
            "/profiles/agents/agent_default", headers=self.headers,
            json={
                "system_prompt": "Changed after dispatch.",
                "context_policy": {
                    "max_chars": 30_000,
                    "include_task_objective": False,
                    "include_acceptance": True,
                    "include_workspace_root": False,
                },
            },
        )
        self.assertEqual(200, changed.status_code, changed.text)
        frozen = self.client.get(
            f"/sessions/{detail['id']}", headers=self.headers,
        )
        self.assertEqual(200, frozen.status_code, frozen.text)
        self.assertEqual(12_000, frozen.json()["context"]["policy"]["max_chars"])

        fake = FakeClient([{"text": "done"}])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], detail["id"]) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "start"})
            self.drain_turn(ws)

        system_prompt = fake.captured[0]["messages"][0]["content"]
        self.assertIn("Agent policy marker.", system_prompt)
        self.assertNotIn("Changed after dispatch.", system_prompt)
        self.assertIn("Task objective:\nComplete Context policy", system_prompt)
        self.assertIn("Workspace root:", system_prompt)
        self.assertNotIn("Acceptance command:\ntrue", system_prompt)

    def test_start_send_reconnect_resume_and_stop_are_persistent(self):
        task = self.create_ready_task("Persistent chat")
        detail = self.start(task["id"])
        session_id = detail["id"]
        dispatch_id = detail["dispatch_id"]
        self.assertEqual("agent_default", detail["agent_profile_id"])
        self.assertEqual(1, detail["dispatch"]["attempt"])

        first = FakeClient([{"text": "answer one"}])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: first),
            self.connect(task["id"], session_id) as ws,
        ):
            ready = ws.receive_json()
            self.assertEqual("session_ready", ready["type"])
            ws.send_json({"type": "message", "text": "question one"})
            events = self.drain_turn(ws)

        for event in events:
            self.assertEqual(task["id"], event["task_id"])
            self.assertEqual(session_id, event["session_id"])
            self.assertEqual(dispatch_id, event["dispatch_id"])
        after_first = self.client.get(
            f"/sessions/{session_id}", headers=self.headers
        ).json()
        self.assertEqual("idle", after_first["status"])
        self.assertEqual("needs_you", after_first["dispatch"]["status"])
        transcript = [
            event["payload"] for event in after_first["events"]
            if event["kind"] == "transcript"
        ]
        self.assertEqual(["user", "assistant"], [event["kind"] for event in transcript])

        resumed = self.client.post(
            f"/sessions/{session_id}/resume", headers=self.headers
        )
        self.assertEqual(200, resumed.status_code, resumed.text)
        second = FakeClient([{"text": "answer two"}])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: second),
            self.connect(task["id"], session_id) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "resume", "text": "question two"})
            self.drain_turn(ws)

        messages = [item["content"] for item in second.captured[0]["messages"]]
        self.assertIn("question one", messages)
        self.assertIn("answer one", messages)
        self.assertIn("question two", messages)

        stopped = self.client.post(
            f"/sessions/{session_id}/stop", headers=self.headers
        )
        self.assertEqual(200, stopped.status_code, stopped.text)
        self.assertEqual("stopped", stopped.json()["status"])
        denied = self.client.post(
            f"/sessions/{session_id}/resume", headers=self.headers
        )
        self.assertEqual(409, denied.status_code)

    def test_selected_agent_profile_is_saved_on_dispatch_and_session(self):
        task = self.create_ready_task("Profile")
        profile = self.store.create_agent_profile(
            name="Focused local",
            description="No workers",
            system_prompt="Work directly in the assigned Task workspace.",
            tools=["read_file"],
            approval="ask",
            worker_policy="none",
            max_steps=7,
            model_profile_id="model_qwen38_27b_4bit",
        )
        response = self.client.post(
            f"/tasks/{task['id']}/sessions",
            headers=self.headers,
            json={
                "agent_profile_id": profile["id"],
                "priority": 7,
                "queue_timeout_ms": 1234,
            },
        )
        self.assertEqual(200, response.status_code, response.text)
        detail = response.json()
        self.assertEqual(profile["id"], detail["agent_profile_id"])
        self.assertEqual(profile["id"], detail["dispatch"]["agent_profile_id"])
        self.assertEqual(7, detail["dispatch"]["budget"]["queue"]["priority"])
        self.assertEqual(1234, detail["dispatch"]["budget"]["queue"]["timeout_ms"])
        persisted = self.store.get_session(detail["id"])
        self.assertEqual(profile["id"], persisted["agent_profile_id"])

    def test_adaptive_decision_is_persisted_and_drives_runtime_spec(self):
        task = self.create_ready_task("Investigate cache invalidation cause")
        response = self.client.post(
            f"/tasks/{task['id']}/sessions",
            headers=self.headers,
            json={"agent_profile_id": "agent_default"},
        )
        self.assertEqual(200, response.status_code, response.text)
        dispatch = response.json()["dispatch"]
        decision = dispatch["adaptive_decision"]
        self.assertEqual("investigation", decision["task_class"])
        self.assertEqual("fixed_one", decision["effective"]["worker_policy"])
        self.assertEqual(["researcher"], decision["effective"]["worker_roles"])
        self.assertEqual(decision["effective"]["budget"], dispatch["budget"])

        spec = server._task_runtime_spec(
            self.store, "agent_default", budget=dispatch["budget"],
            adaptive_decision=decision,
        )
        self.assertEqual("fixed_one", spec["worker_policy"])
        self.assertEqual(["researcher"], spec["worker_roles"])
        self.assertEqual(["researcher"], spec["worker_role_sequence"])

    def test_project_promoted_profile_is_used_when_session_omits_profile(self):
        task = self.create_ready_task("Project default profile")
        profile = self.store.create_agent_profile(
            name="Promoted", system_prompt="Promoted default", tools=["read_file"],
            worker_policy="none", max_steps=9,
            model_profile_id="model_qwen38_27b_4bit",
        )
        baseline = self.store.create_evaluation_experiment(
            role="baseline", label="base", source="runner", status="completed",
            agent_profile_id="agent_default",
        )
        candidate = self.store.create_evaluation_experiment(
            role="candidate", label="candidate", source="runner", status="completed",
            agent_profile_id=profile["id"],
        )
        comparison = self.store.create_evaluation_comparison(
            baseline_experiment_id=baseline["id"], candidate_experiment_id=candidate["id"],
            thresholds={}, result={"verdict": "improved"},
        )
        self.store.promote_project_agent_profile(
            self.project["id"], comparison_id=comparison["id"],
        )
        response = self.client.post(
            f"/tasks/{task['id']}/sessions", headers=self.headers, json={},
        )
        self.assertEqual(200, response.status_code, response.text)
        self.assertEqual(profile["id"], response.json()["agent_profile_id"])

    def test_dispatch_step_budget_exhaustion_is_persisted_and_fails_only_attempt(self):
        task = self.create_ready_task("Budgeted")
        other_task = self.create_ready_task("Unaffected")
        profile = self.store.create_agent_profile(
            name="One step",
            system_prompt="Use one tool step.",
            tools=["echo"],
            approval="auto",
            worker_policy="none",
            max_steps=5,
            model_profile_id="model_qwen38_27b_4bit",
            budget={"dispatch": {"step_limit": 1}},
        )
        detail = self.client.post(
            f"/tasks/{task['id']}/sessions", headers=self.headers,
            json={"agent_profile_id": profile["id"]},
        ).json()
        other = self.start(other_task["id"])
        fake = FakeClient([
            {"calls": [("echo", json.dumps({"text": "one"}))]},
            {"text": "must not run"},
        ])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], detail["id"]) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "run"})
            events = self.drain_turn(ws)

        self.assertEqual(1, len(fake.captured))
        self.assertTrue(any(
            event.get("type") == "agent_event"
            and event.get("kind") == "budget_exhausted"
            and event.get("reason") == "dispatch:step_limit"
            for event in events
        ))
        persisted = self.client.get(
            f"/sessions/{detail['id']}", headers=self.headers
        ).json()
        self.assertEqual("failed", persisted["status"])
        self.assertEqual("failed", persisted["dispatch"]["status"])
        self.assertEqual(
            "dispatch:step_limit", persisted["dispatch"]["budget_exhausted_reason"]
        )
        self.assertEqual(1, persisted["dispatch"]["usage"]["steps"])
        self.assertEqual("created", self.store.get_session(other["id"])["status"])

    def test_active_session_blocks_workspace_removal(self):
        task = self.create_ready_task("Protect workspace")
        detail = self.start(task["id"])
        denied = self.client.post(
            f"/tasks/{task['id']}/workspace/archive",
            headers=self.headers,
            json={"confirm_workspace_id": detail["workspace_id"]},
        )
        self.assertEqual(409, denied.status_code, denied.text)
        self.assertIn("AgentSession", denied.json()["detail"])

    def test_new_attempt_rejects_old_dispatch_events(self):
        task = self.create_ready_task("Attempts")
        old = self.start(task["id"])
        with self.connect(task["id"], old["id"]) as old_ws:
            self.assertEqual("session_ready", old_ws.receive_json()["type"])
            newer = self.start(task["id"])
            self.assertEqual(2, newer["dispatch"]["attempt"])
            old_ws.send_json({"type": "message", "text": "late work"})
            self.assertEqual("stale_dispatch", old_ws.receive_json()["type"])

        self.assertEqual("stopped", self.store.get_session(old["id"])["status"])
        self.assertEqual("cancelled", self.store.get_dispatch(old["dispatch_id"])["status"])
        with self.assertRaises(domain.StaleDispatch):
            self.store.append_session_event(
                old["id"], kind="late", payload={"bad": True},
                task_id=task["id"], dispatch_id=old["dispatch_id"],
                workspace_id=old["workspace_id"], require_latest=True,
            )

    def test_server_restart_recovers_running_session_for_resume(self):
        task = self.create_ready_task("Recovery")
        detail = self.start(task["id"])
        self.store.activate_session_turn(detail["id"])
        self.assertEqual("running", self.store.get_session(detail["id"])["status"])

        server._DOMAIN_STORE = None
        server._DOMAIN_STORE_PATH = None
        server._DOMAIN_RECOVERED_PATH = None
        reopened = server.get_domain_store()
        self.store = reopened
        self.assertEqual("idle", reopened.get_session(detail["id"])["status"])
        self.assertEqual("needs_you", reopened.get_dispatch(detail["dispatch_id"])["status"])
        self.assertEqual("needs_you", reopened.get_task(task["id"])["status"])
        response = self.client.post(
            f"/sessions/{detail['id']}/resume", headers=self.headers
        )
        self.assertEqual(200, response.status_code, response.text)

    def test_cancelling_one_task_does_not_stop_another(self):
        first_task = self.create_ready_task("Endless")
        second_task = self.create_ready_task("Independent")
        first_session = self.start(first_task["id"])
        second_session = self.start(second_task["id"])

        def endless():
            while True:
                yield text_chunk("x")
                time.sleep(0.005)

        first_client = FakeClient([lambda: endless()])
        second_client = FakeClient([{"text": "finished independently"}])
        clients = iter([first_client, second_client])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", side_effect=lambda: next(clients)),
            self.connect(first_task["id"], first_session["id"]) as first_ws,
            self.connect(second_task["id"], second_session["id"]) as second_ws,
        ):
            self.assertEqual("session_ready", first_ws.receive_json()["type"])
            self.assertEqual("session_ready", second_ws.receive_json()["type"])
            first_ws.send_json({"type": "message", "text": "keep generating"})
            while True:
                event = first_ws.receive_json()
                if event["type"] == "agent_event" and event["kind"] == "text_delta":
                    break

            second_ws.send_json({"type": "message", "text": "finish"})
            second_events = []
            while True:
                event = second_ws.receive_json()
                second_events.append(event)
                if (event["type"] == "agent_event"
                        and event["kind"] == "resource_queue_enter"):
                    break

            # 두 번째 Task는 같은 model slot에서 기다린다. 첫 Task만 취소하면
            # lease가 반환되고 대기 중인 Task가 독립적으로 이어서 완료돼야 한다.
            first_ws.send_json({"type": "cancel"})
            first_events = self.drain_turn(first_ws)
            self.assertTrue(first_events[-1]["cancelled"])

            second_events.extend(self.drain_turn(second_ws))
            queue_wait = next(
                event for event in second_events
                if event.get("type") == "agent_event"
                and event.get("kind") == "resource_queue_wait"
            )
            self.assertEqual("capacity_exhausted", queue_wait["reason"])
            self.assertEqual("model_generation", queue_wait["resource"])
            self.assertEqual(1, queue_wait["cap"])
            self.assertTrue(any(
                event.get("type") == "agent_event"
                and event.get("content") == "finished independently"
                for event in second_events
            ))

        self.assertEqual("idle", self.store.get_session(first_session["id"])["status"])
        self.assertEqual("idle", self.store.get_session(second_session["id"])["status"])

    def test_stop_during_active_turn_closes_the_persisted_session(self):
        task = self.create_ready_task("Stop")
        detail = self.start(task["id"])

        def endless():
            while True:
                yield text_chunk("z")
                time.sleep(0.005)

        fake = FakeClient([lambda: endless()])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], detail["id"]) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "work until stopped"})
            while True:
                event = ws.receive_json()
                if event["type"] == "agent_event" and event["kind"] == "text_delta":
                    break
            ws.send_json({"type": "stop"})
            terminal = []
            while len(terminal) < 2:
                event = ws.receive_json()
                if event["type"] in {"session_stopped", "turn_end"}:
                    terminal.append(event["type"])
            self.assertEqual(["session_stopped", "turn_end"], terminal)

        persisted = self.client.get(
            f"/sessions/{detail['id']}", headers=self.headers
        ).json()
        self.assertEqual("stopped", persisted["status"])
        self.assertEqual("cancelled", persisted["dispatch"]["status"])
        self.assertEqual("todo", self.store.get_task(task["id"])["status"])


if __name__ == "__main__":
    unittest.main()
