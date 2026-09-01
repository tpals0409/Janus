"""Persistent Task–Dispatch–AgentSession runtime integration tests."""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from fastapi.testclient import TestClient

from janus_server import domain, runtime, server, shared
from janus_server import scheduler as scheduler_mod
from janus_server.routers import sessions
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
        shared._DOMAIN_STORE = None
        shared._DOMAIN_STORE_PATH = None
        shared._DOMAIN_RECOVERED_PATH = None
        with shared._TASK_RUNTIMES_LOCK:
            shared._TASK_RUNTIMES.clear()
        self.client = TestClient(server.app)
        self.headers = {"x-janus-token": server.AUTH_TOKEN}
        self.store = server.get_domain_store()
        self.project = self.store.create_project(
            name="Runtime", repo_path=str(root / "repo")
        )
        self.root = root

    def tearDown(self):
        self.client.close()
        with shared._TASK_RUNTIMES_LOCK:
            for orch in shared._TASK_RUNTIMES.values():
                orch.cancel_all()
            shared._TASK_RUNTIMES.clear()
        shared._DOMAIN_STORE = None
        shared._DOMAIN_STORE_PATH = None
        shared._DOMAIN_RECOVERED_PATH = None
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
        with shared._TASK_RUNTIMES_LOCK:
            shared._TASK_RUNTIMES["shutdown-test"] = LiveRuntime()  # type: ignore[assignment]

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

    # ── 토큰 델타 영속화 ──
    # 화면에는 즉시, 저장소에는 합쳐서 간다. 하나씩 영속하면 매 토큰이 새
    # connection + BEGIN IMMEDIATE + MAX(seq) + INSERT + COMMIT이 되고, 그
    # 전역 쓰기 락에 모든 워커의 생성이 직렬화된다.

    def test_streamed_deltas_are_coalesced_into_one_stored_event(self):
        task = self.create_ready_task("delta batching")
        started = self.start(task["id"])
        session_id = started["id"]
        pieces = ["안녕", "하세", "요 ", "결과", "입니다"]
        fake = FakeClient([[*(text_chunk(piece) for piece in pieces)]])

        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], session_id) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "stream it"})
            streamed = [
                event for event in self.drain_turn(ws)
                if event.get("kind") == "text_delta"
            ]

        # 화면에는 토큰 단위 그대로 흘렀다.
        self.assertEqual(pieces, [event["text"] for event in streamed])

        # 저장소에는 하나로 합쳐졌다.
        stored = [
            item["payload"] for item in self.store.list_session_events(session_id)
            if item["payload"].get("kind") == "text_delta"
        ]
        self.assertEqual(1, len(stored), stored)
        self.assertEqual("".join(pieces), stored[0]["text"])

    def test_a_non_delta_event_flushes_the_buffer_in_order(self):
        task = self.create_ready_task("delta ordering")
        started = self.start(task["id"])
        session_id = started["id"]
        fake = FakeClient([{"text": "먼저 답하고"}, {"text": "끝"}])

        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], session_id) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "go"})
            self.drain_turn(ws)

        kinds = [
            item["payload"].get("kind")
            for item in self.store.list_session_events(session_id)
            if item["payload"].get("type") == "agent_event"
        ]
        # 델타가 그 뒤의 assistant 이벤트보다 앞선 seq를 받는다 — 재접속 복원이
        # 순서를 그대로 다시 그릴 수 있어야 한다.
        self.assertLess(kinds.index("text_delta"), kinds.index("assistant"))

    def test_finish_turn_completed_moves_task_to_review(self):
        task = self.create_ready_task("structured completion")
        started = self.start(task["id"])
        session_id = started["id"]
        fake = FakeClient([
            {"calls": [("finish_turn", json.dumps({
                "outcome": "completed", "summary": "Implemented and verified",
                "evidence": ["tests passed"],
            }))]},
            {"text": "완료했습니다."},
        ])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], session_id) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "finish it"})
            events = self.drain_turn(ws)

        turn_end = next(event for event in events if event["type"] == "turn_end")
        self.assertEqual("completed", turn_end["outcome"]["outcome"])
        done = next(
            event for event in events
            if event.get("type") == "agent_event" and event.get("kind") == "done"
        )
        self.assertEqual("terminal_tool:finish_turn", done["reason"])
        self.assertEqual(1, len(fake.captured))
        self.assertTrue(any(
            event.get("type") == "agent_event"
            and event.get("kind") == "assistant"
            and event.get("content") == "Implemented and verified"
            for event in events
        ))
        updated = self.client.get(f"/tasks/{task['id']}", headers=self.headers).json()
        self.assertEqual("review", updated["status"])
        self.assertIsNone(updated["attention_reason"])

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
        task_after_first = self.client.get(
            f"/tasks/{task['id']}", headers=self.headers,
        ).json()
        self.assertEqual("conversation_idle", task_after_first["attention_reason"])
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

    def test_request_changes_comments_reach_the_next_turn_context(self):
        task = self.create_ready_task("Review loop")
        detail = self.start(task["id"])
        session_id = detail["id"]
        comment = self.store.create_review_comment(
            task_id=task["id"], revision="rev-1", layer="unstaged",
            file_path="src/auth.ts", old_line=None, new_line=42,
            hunk_header="@@ -40,6 +40,8 @@", body="토큰 만료 검사를 추가하세요",
        )
        self.store.create_review_decision(
            task_id=task["id"], revision="rev-1", decision="request_changes",
            comment_ids=[comment["id"]],
        )

        shown = self.client.get(
            f"/sessions/{session_id}", headers=self.headers
        ).json()
        feedback_items = [
            item for item in shown["context"]["items"] if item["id"] == "review_feedback"
        ]
        self.assertEqual(1, len(feedback_items))
        self.assertIn("src/auth.ts:42", feedback_items[0]["content"])

        fake = FakeClient([{"text": "fixed"}])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], session_id) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "리뷰 반영해줘"})
            self.drain_turn(ws)

        contents = "\n".join(
            str(item["content"]) for item in fake.captured[0]["messages"]
        )
        self.assertIn("토큰 만료 검사를 추가하세요", contents)
        self.assertIn("src/auth.ts:42", contents)

    def test_a_message_during_an_active_turn_runs_on_the_next_turn(self):
        task = self.create_ready_task("Queued steering")
        detail = self.start(task["id"])
        session_id = detail["id"]

        fake = FakeClient([{"text": "answer one"}, {"text": "answer two"}])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], session_id) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "question one"})
            ws.send_json({"type": "message", "text": "question two"})
            first = self.drain_turn(ws)
            second = self.drain_turn(ws)

        queued = [event for event in first if event["type"] == "turn_queued"]
        self.assertEqual(["question two"], [event["text"] for event in queued])
        self.assertEqual(
            "run_start", second[0]["type"],
            "큐에 쌓인 지시는 턴이 끝나면 자동으로 다음 턴을 시작해야 한다",
        )
        self.assertIn(
            "question two",
            [item["content"] for item in fake.captured[1]["messages"]],
        )

    def test_renderer_disconnect_does_not_cancel_persisted_turn(self):
        task = self.create_ready_task("Detached persistent chat")
        detail = self.start(task["id"])

        generation_started = threading.Event()

        def delayed_answer():
            generation_started.set()
            time.sleep(0.1)
            return {"text": "answer survived disconnect"}

        fake = FakeClient([delayed_answer])
        with (
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
            self.connect(task["id"], detail["id"]) as ws,
        ):
            self.assertEqual("session_ready", ws.receive_json()["type"])
            ws.send_json({"type": "message", "text": "keep working"})
            self.assertTrue(generation_started.wait(1))
            # Leaving the context closes the renderer socket while generation
            # is still in flight. The Dispatch-owned turn must keep running.

        deadline = time.monotonic() + 2
        while True:
            settled = self.client.get(
                f"/sessions/{detail['id']}", headers=self.headers,
            ).json()
            if any(
                event["kind"] == "transcript"
                and event["payload"].get("kind") == "assistant"
                for event in settled["events"]
            ):
                break
            if time.monotonic() >= deadline:
                self.fail("detached turn did not persist its assistant response")
            time.sleep(0.02)
        self.assertEqual("idle", settled["status"])
        transcript = [
            event["payload"] for event in settled["events"]
            if event["kind"] == "transcript"
        ]
        self.assertEqual(["user", "assistant"], [event["kind"] for event in transcript])
        self.assertEqual("answer survived disconnect", transcript[-1]["content"])

    def test_a_pending_approval_is_replayed_to_a_reconnected_client(self):
        task = self.create_ready_task("Approval survives reconnect")
        started = self.start(task["id"])
        session_id = started["id"]
        answered = threading.Event()
        request = {
            "type": "approval_request", "id": "req-1", "node_id": "w1-impl",
            "tool": "edit_file", "args": {"path": "a.ts"}, "rememberable": True,
            "approval_scope": "workspace_write",
        }
        shared._PENDING_APPROVALS[session_id] = {
            "req-1": [answered, False, ("workspace_write", started["workspace_id"]), request]
        }
        try:
            with self.connect(task["id"], session_id) as ws:
                # 재연결한 창은 대기 중인 승인을 다시 봐야 한다. 못 보면 워커는
                # 답을 받을 길 없이 APPROVAL_TIMEOUT을 그대로 태운다.
                seen = []
                for _ in range(6):
                    event = ws.receive_json()
                    seen.append(event)
                    if event["type"] == "approval_request":
                        break
                replayed = next(item for item in seen if item["type"] == "approval_request")
                self.assertEqual("req-1", replayed["id"])
                self.assertEqual("edit_file", replayed["tool"])

                # 그리고 새 연결에서 답할 수 있어야 한다 — 승인 대기는 연결이 아니라
                # 세션에 속하기 때문이다.
                ws.send_json({"type": "approval_response", "id": "req-1", "approved": True})
                self.assertTrue(answered.wait(timeout=5))
                self.assertTrue(shared._PENDING_APPROVALS[session_id]["req-1"][1])
        finally:
            shared._PENDING_APPROVALS.pop(session_id, None)

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
        self.assertEqual(["scout"], decision["effective"]["worker_roles"])
        self.assertEqual(decision["effective"]["budget"], dispatch["budget"])

        spec = sessions._task_runtime_spec(
            self.store, "agent_default", budget=dispatch["budget"],
            adaptive_decision=decision,
        )
        self.assertEqual("fixed_one", spec["worker_policy"])
        self.assertEqual(["scout"], spec["worker_roles"])
        self.assertEqual(["scout"], spec["worker_role_sequence"])

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
        self.assertIn("소유하지 않은 Workspace", denied.json()["detail"])

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

        shared._DOMAIN_STORE = None
        shared._DOMAIN_STORE_PATH = None
        shared._DOMAIN_RECOVERED_PATH = None
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
        # 이 테스트는 model slot 경합을 검증한다 — 전역 기본 슬롯 수가 바뀌어도
        # 시나리오가 성립하도록 여기서 1로 고정한다.
        with (
            patch.dict(
                scheduler_mod.default_scheduler().caps,
                {scheduler_mod.ResourceClass.MODEL_GENERATION: 1},
            ),
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

