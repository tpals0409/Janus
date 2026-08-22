"""오케스트레이터-워커 런타임 회귀 테스트 — 전부 FakeClient, MLX 불필요."""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from fastapi.testclient import TestClient

from janus_server import agent, runtime, server
from janus_server import spec as S
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient, text_chunk

ORIGIN = "http://localhost:5173"
SPEC = {"name": "Orch", "model": "qwen3.8-27b", "system_prompt": "orchestrate",
        "tools": ["echo"], "approval": "auto", "max_steps": 6,
        "allow_autonomous_workers": True}


def worker_args(name="helper", tools=None, task="do it"):
    return json.dumps({"name": name, "system_prompt": "work", "task": task,
                       "role": "researcher",
                       "tools": tools if tools is not None else ["echo"]})


@contextmanager
def orch_env(fake: FakeClient, spec: dict = SPEC):
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        agents, runs = root / "agents", root / "runs"
        agents.mkdir()
        (agents / "orch.yaml").write_text(S.dumps(spec), encoding="utf-8")
        with (
            patch.object(server, "AGENTS_DIR", agents),
            patch.object(server, "RUNS_DIR", runs),
            patch.object(runtime, "resolve_local_model", lambda n: n),
            patch.object(runtime, "make_client", lambda: fake),
        ):
            yield runs


class RuntimeTests(unittest.TestCase):
    client = TestClient(server.app)

    def connect(self):
        return self.client.websocket_connect(
            "/run/orch", headers={"origin": ORIGIN},
            subprotocols=["janus", "test-token"])

    def drain_turn(self, ws, on_event=None) -> list[dict]:
        """turn_end까지 수신. run_error는 즉시 실패."""
        seen = []
        while True:
            m = ws.receive_json()
            if m["type"] == "run_error":
                self.fail(m["error"])
            seen.append(m)
            if on_event:
                on_event(m)
            if m["type"] == "turn_end":
                return seen

    @staticmethod
    def saved_run(runs: Path) -> dict:
        files = list((runs / "orch").glob("*.json"))
        assert len(files) == 1, f"실행 파일 1개 기대, 실제 {len(files)}"
        return json.loads(files[0].read_text(encoding="utf-8"))

    def test_single_turn_streams_and_saves(self):
        fake = FakeClient([{"text": "hello there"}])
        with orch_env(fake) as runs, self.connect() as ws:
            ws.send_json({"type": "message", "text": "hi"})
            seen = self.drain_turn(ws)

            kinds = {m["type"] for m in seen}
            self.assertIn("run_start", kinds)
            self.assertIn("span_start", kinds)
            self.assertIn("agent_event", kinds)
            task_events = [
                message for message in seen
                if message["type"] in {"span_start", "agent_event", "turn_end"}
            ]
            for message in task_events:
                payload = message.get("span", message)
                self.assertTrue(payload["task_id"])
                self.assertTrue(payload["workspace_id"])
                self.assertTrue(payload["dispatch_id"])

            r = self.saved_run(runs)  # turn_end는 저장 후에 온다
            self.assertEqual({"task": "hi"}, r["inputs"])
            spans = r["spans"]
            self.assertEqual("orchestrator", spans[0]["node_id"])
            self.assertEqual("success", spans[0]["status"])
            self.assertIsNone(spans[0]["parent_id"])
            telemetry = r["telemetry"]
            self.assertEqual(2, telemetry["schema_version"])
            self.assertEqual("monotonic_ns", telemetry["clock"])
            self.assertEqual(0, telemetry["top_level_unaccounted_ms"])
            self.assertEqual(1, telemetry["tokens"]["prompt"])
            self.assertEqual(1, telemetry["tokens"]["completion"])
            kinds = {event["kind"] for event in telemetry["events"]}
            self.assertIn("resource_queue_enter", kinds)
            self.assertIn("resource_lease_acquired", kinds)
            self.assertIn("model_generation_start", kinds)
            self.assertIn("model_generation_end", kinds)
            self.assertTrue(telemetry["memory_snapshots"])
            self.assertEqual(spans[0]["workspace_id"], telemetry["workspace_id"])

    def test_multi_turn_keeps_session_and_overwrites_one_file(self):
        fake = FakeClient([{"text": "A"}, {"text": "B"}])
        with orch_env(fake) as runs, self.connect() as ws:
            ws.send_json({"type": "message", "text": "one"})
            self.drain_turn(ws)
            ws.send_json({"type": "message", "text": "two"})
            self.drain_turn(ws)

            second = [m["content"] for m in fake.captured[1]["messages"]]
            self.assertIn("one", second)   # 1턴 user
            self.assertIn("A", second)     # 1턴 assistant
            self.assertIn("two", second)   # 2턴 user
            self.saved_run(runs)           # 파일은 여전히 1개 (덮어쓰기)

    def test_worker_none_policy_does_not_expose_create_worker(self):
        fake = FakeClient([{"text": "direct"}])
        spec = {**SPEC, "worker_policy": "none"}
        with orch_env(fake, spec), self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})
            self.drain_turn(ws)

        names = [tool["function"]["name"] for tool in fake.captured[0]["tools"]]
        self.assertNotIn("create_worker", names)

    def test_worker_tools_subset_and_no_spawn_depth(self):
        # 워커가 run_bash를 요청해도 오케스트레이터 tools(echo)와의 교집합만 받는다
        fake = FakeClient([
            {"calls": [("create_worker", worker_args(tools=["echo", "run_bash"]))]},
            {"text": "worker done"},
            {"text": "final"},
        ])
        with orch_env(fake), self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})
            seen = self.drain_turn(ws)

            worker_call = fake.captured[1]  # 호출 순서: 오케 → 워커 → 오케
            names = [t["function"]["name"] for t in worker_call["tools"]]
            self.assertEqual(["echo"], names)
            self.assertNotIn("create_worker", names)  # 깊이 1 — 워커는 스폰 불가

            spans = [m["span"] for m in seen if m["type"] == "span_end"]
            worker = next(s for s in spans if s["node_id"].startswith("w1-"))
            self.assertEqual("success", worker["status"])
            orch_span = next(m["span"] for m in seen
                             if m["type"] == "span_start" and m["span"]["node_id"] == "orchestrator")
            self.assertEqual(orch_span["id"], worker["parent_id"])

            # 워커 결과가 다음 오케스트레이터 호출의 tool 메시지로 렌더됐다 (registry 인지 렌더)
            final_call = fake.captured[2]["messages"]
            tool_msgs = [m for m in final_call if m["role"] == "tool"]
            self.assertEqual(["worker done"], [m["content"] for m in tool_msgs])

    def test_duplicate_worker_request_is_suppressed(self):
        duplicate = worker_args("same", task="same isolated task")
        fake = FakeClient([
            {"calls": [("create_worker", duplicate), ("create_worker", duplicate)]},
            {"text": "worker done"},
            {"text": "final"},
        ])
        with orch_env(fake) as runs, self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})
            seen = self.drain_turn(ws)
            saved = self.saved_run(runs)

        workers = [span for span in saved["spans"] if span["worker_id"] is not None]
        self.assertEqual(1, len(workers))
        suppressed = [
            message for message in seen
            if message["type"] == "agent_event"
            and message["kind"] == "worker_spawn_suppressed"
        ]
        reused = [
            message for message in seen
            if message["type"] == "agent_event"
            and message["kind"] == "worker_result_reused"
        ]
        self.assertEqual(1, len(suppressed) + len(reused))
        if suppressed:
            self.assertEqual("duplicate_worker_running", suppressed[0]["reason"])

    def test_autonomous_implementer_is_suppressed_without_explicit_delegation(self):
        args = json.dumps({
            "name": "coder", "role": "implementer", "system_prompt": "work",
            "task": "implement the whole task", "tools": ["echo"],
        })
        fake = FakeClient([
            {"calls": [("create_worker", args)]},
            {"text": "completed directly"},
        ])
        spec = {**SPEC, "allow_autonomous_workers": False}
        with orch_env(fake, spec), self.connect() as ws:
            ws.send_json({"type": "message", "text": "fix this task"})
            seen = self.drain_turn(ws)

        self.assertEqual(0, sum(
            message["type"] == "span_start"
            and message["span"]["node_id"].startswith("w")
            for message in seen
        ))
        event = next(
            message for message in seen
            if message["type"] == "agent_event"
            and message["kind"] == "worker_spawn_suppressed"
        )
        self.assertEqual("autonomous_implementer_overhead", event["reason"])
        tool_message = next(
            message for message in fake.captured[1]["messages"]
            if message["role"] == "tool"
        )
        self.assertIn("complete/integrate the task directly", tool_message["content"])

    def test_verifier_worker_is_read_only_and_receives_bounded_context(self):
        args = json.dumps({
            "name": "verify", "role": "verifier", "system_prompt": "s" * 3_000,
            "task": "check result", "context": "c" * 6_000,
            "tools": ["grep", "run_bash"],
        })
        fake = FakeClient([
            {"calls": [("create_worker", args)]},
            {"text": "verified"},
            {"text": "final"},
        ])
        spec = {
            **SPEC, "tools": ["grep", "run_bash"], "approval": "ask",
        }
        with orch_env(fake, spec), self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})
            seen = self.drain_turn(ws)

        worker_call = fake.captured[1]
        self.assertEqual(
            ["grep"], [tool["function"]["name"] for tool in worker_call["tools"]]
        )
        self.assertIn("read-only verifier", worker_call["messages"][0]["content"])
        prepared = next(
            message for message in seen
            if message["type"] == "agent_event"
            and message["kind"] == "worker_context_prepared"
        )
        self.assertEqual("verifier", prepared["role"])
        self.assertEqual(4_000, prepared["context_chars"])
        self.assertTrue(prepared["truncated"])

    def test_fixed_one_policy_rejects_additional_workers(self):
        fake = FakeClient([
            {"calls": [("create_worker", worker_args("first")),
                       ("create_worker", worker_args("second"))]},
            {"text": "worker done"},
            {"text": "final"},
        ])
        spec = {**SPEC, "worker_policy": "fixed_one"}
        with orch_env(fake, spec) as runs, self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})
            self.drain_turn(ws)
            saved = self.saved_run(runs)

        workers = [span for span in saved["spans"] if span["worker_id"] is not None]
        self.assertEqual(1, len(workers))
        self.assertEqual(1, saved["telemetry"]["worker_count"])

    def test_parallel_workers_share_one_model_generation_slot(self):
        active = 0
        max_active = 0
        generation_lock = threading.Lock()

        def worker_turn():
            nonlocal active, max_active
            with generation_lock:
                active += 1
                max_active = max(max_active, active)
            time.sleep(0.02)
            with generation_lock:
                active -= 1
            return {"text": "wdone"}

        fake = FakeClient([
            {"calls": [("create_worker", worker_args("a", task="task a")),
                       ("create_worker", worker_args("b", task="task b"))]},
            worker_turn, worker_turn,
            {"text": "final"},
        ])
        with orch_env(fake), self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})
            seen = self.drain_turn(ws)

            workers = [m["span"] for m in seen
                       if m["type"] == "span_end" and m["span"]["node_id"] != "orchestrator"]
            self.assertEqual(2, len(workers))
            self.assertEqual({"success"}, {s["status"] for s in workers})
            self.assertEqual(1, max_active)

    def test_stop_worker_cancels_only_that_worker(self):
        def endless():
            while True:
                yield text_chunk("x")
                time.sleep(0.01)

        fake = FakeClient([
            {"calls": [("create_worker", worker_args())]},
            lambda: endless(),
            {"text": "final"},
        ])
        with orch_env(fake), self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})

            stopped = []

            def on_event(m):
                if (m["type"] == "span_start" and m["span"]["node_id"].startswith("w1-")
                        and not stopped):
                    stopped.append(m["span"]["node_id"])
                    ws.send_json({"type": "stop_worker", "node_id": m["span"]["node_id"]})

            seen = self.drain_turn(ws, on_event=on_event)  # turn_end 도달 = 오케는 살았다

            worker = next(m["span"] for m in seen
                          if m["type"] == "span_end" and m["span"]["node_id"] == stopped[0])
            self.assertEqual("error", worker["status"])
            self.assertIn("중단", worker["output"]["error"])

    def test_cancel_keeps_session_alive(self):
        def endless():
            while True:
                yield text_chunk("y")
                time.sleep(0.01)

        fake = FakeClient([lambda: endless(), {"text": "again"}])
        with orch_env(fake), self.connect() as ws:
            ws.send_json({"type": "message", "text": "first"})
            # 스트리밍이 시작된 걸 확인하고 끊는다
            while True:
                m = ws.receive_json()
                if m["type"] == "agent_event" and m["kind"] == "text_delta":
                    break
            ws.send_json({"type": "cancel"})
            self.drain_turn(ws)

            ws.send_json({"type": "message", "text": "second"})
            self.drain_turn(ws)

            history = [m["content"] for m in fake.captured[1]["messages"]]
            self.assertIn("first", history)   # 중단된 턴도 세션에 남아 있다
            self.assertIn("second", history)

    def test_implementer_without_shell_is_told_to_finish_after_edits(self):
        args = json.dumps({
            "name": "edit", "role": "implementer", "system_prompt": "work",
            "task": "edit the file", "tools": ["echo"],
        })
        fake = FakeClient([
            {"calls": [("create_worker", args)]},
            {"text": "worker done"},
            {"text": "final"},
        ])
        with orch_env(fake), self.connect() as ws:
            ws.send_json({"type": "message", "text": "delegate this to a worker"})
            self.drain_turn(ws)

        worker_system = fake.captured[1]["messages"][0]["content"]
        self.assertIn("do not search for a shell", worker_system)
        self.assertIn("Do not broaden the original contract", worker_system)

    def test_tight_fixed_one_implementer_is_adapted_to_read_only_scout(self):
        args = json.dumps({
            "name": "scout", "role": "implementer", "system_prompt": "work",
            "task": "inspect before editing", "tools": ["echo"],
        })
        fake = FakeClient([
            {"calls": [("create_worker", args)]},
            {"text": "evidence"},
            {"text": "final"},
        ])
        spec = {**SPEC, "worker_policy": "fixed_one", "max_steps": 10}
        with orch_env(fake, spec), self.connect() as ws:
            ws.send_json({"type": "message", "text": "go"})
            seen = self.drain_turn(ws)

        worker_call = fake.captured[1]
        self.assertIn("You are a read-only scout", worker_call["messages"][0]["content"])
        self.assertNotIn("You are an implementer", worker_call["messages"][0]["content"])
        adapted = next(
            message for message in seen
            if message["type"] == "agent_event"
            and message["kind"] == "worker_role_adapted"
        )
        self.assertEqual("implementer", adapted["requested_role"])
        self.assertEqual("researcher", adapted["effective_role"])
        tool_message = next(
            message for message in fake.captured[2]["messages"]
            if message["role"] == "tool"
        )
        self.assertIn("single_slot_tight_dispatch_scout", tool_message["content"])

    def test_worker_cannot_call_tool_outside_its_enforced_subset(self):
        args = json.dumps({
            "name": "scout", "role": "researcher", "system_prompt": "inspect",
            "task": "inspect only", "tools": ["echo"],
        })
        forbidden = json.dumps({"path": "escaped.txt", "content": "no"})
        fake = FakeClient([
            {"calls": [("create_worker", args)]},
            {"calls": [("write_file", forbidden)]},
            {"text": "could not write"},
            {"text": "final"},
        ])
        with orch_env(fake), self.connect() as ws:
            ws.send_json({"type": "message", "text": "delegate this to a worker"})
            seen = self.drain_turn(ws)

        rejected = next(
            message for message in seen
            if message["type"] == "agent_event" and message["kind"] == "tool_rejected"
        )
        self.assertEqual("write_file", rejected["name"])
        self.assertEqual("tool_not_in_node_subset", rejected["reason"])
        rejected_result = next(
            message for message in seen
            if message["type"] == "agent_event"
            and message["kind"] == "tool_result"
            and message["node_id"].startswith("w1-")
        )
        self.assertIn("not available to this agent node", rejected_result["value"]["error"])


class SessionContextTests(unittest.TestCase):
    def test_compaction_preserves_recent_objective_and_tool_pairs(self):
        session = agent.Session(
            "system", context_max_chars=4_000, context_recent_blocks=4,
        )
        for index in range(12):
            call_id = f"call-{index}"
            session.append("user", content=f"objective {index} " + "u" * 240)
            session.append("assistant", content=f"decision {index}", tool_calls=[{
                "id": call_id, "type": "function",
                "function": {"name": "echo", "arguments": "{}"},
            }])
            session.append(
                "tool_result", tool_call_id=call_id, name="echo",
                value={"text": "r" * 240},
            )
        session.append("user", content="current objective must survive")

        baseline = session.derive_messages(compact=False)
        baseline_chars = session._chars(baseline)
        compacted = session.derive_messages()
        stats = session.context_stats

        self.assertTrue(stats["compacted"])
        self.assertLess(stats["sent_chars"], baseline_chars)
        self.assertGreater(stats["saved_chars"], 0)
        self.assertIn("current objective must survive", [
            message["content"] for message in compacted if message["role"] == "user"
        ])
        call_ids = {
            call["id"] for message in compacted if message["role"] == "assistant"
            for call in message.get("tool_calls") or []
        }
        self.assertTrue(all(
            message["tool_call_id"] in call_ids
            for message in compacted if message["role"] == "tool"
        ))

    def test_stable_prefix_probe_reports_reuse_without_claiming_cache_hit(self):
        session = agent.Session("stable system")
        session.append("user", content="one")
        session.derive_messages()
        self.assertFalse(session.context_stats["prefix_reused"])
        session.append("assistant", content="answer")
        session.append("user", content="two")
        session.derive_messages()
        self.assertTrue(session.context_stats["prefix_reused"])

    def test_compaction_keeps_acceptance_result_with_less_input(self):
        compact = agent.Session("system", context_max_chars=3_000)
        baseline = agent.Session("system", context_max_chars=None)
        for index in range(20):
            for session in (compact, baseline):
                session.append("user", content=f"old request {index} " + "u" * 220)
                session.append("assistant", content=f"old result {index} " + "a" * 220)
        compact_client = FakeClient([{"text": "ACCEPTED"}])
        baseline_client = FakeClient([{"text": "ACCEPTED"}])
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceContext(
                Path(tmp), "task-context", "workspace-context", "dispatch-context",
            )
            common = {
                "model": "fake", "system_prompt": "", "task": "finish current task",
                "tool_names": [], "workspace_context": workspace,
                "approve": lambda _name, _args: True,
                "emit": lambda _kind, **_data: None,
            }
            compact_result, _ = agent.run(
                client=compact_client, session=compact, **common,
            )
            baseline_result, _ = agent.run(
                client=baseline_client, session=baseline, **common,
            )

        self.assertEqual("ACCEPTED", compact_result)
        self.assertEqual(compact_result, baseline_result)
        compact_chars = agent.Session._chars(compact_client.captured[0]["messages"])
        baseline_chars = agent.Session._chars(baseline_client.captured[0]["messages"])
        self.assertLess(compact_chars, baseline_chars)
        self.assertGreater((baseline_chars - compact_chars) / baseline_chars, 0.4)

    def test_worker_spawn_pressure_uses_model_queue_state(self):
        snapshot = {
            "closed": False,
            "resources": {"model_generation": {"active": 1, "queued": 1, "cap": 1}},
        }
        self.assertEqual(
            "model_queue_backpressure", runtime.worker_spawn_pressure(snapshot)
        )
        snapshot["resources"]["model_generation"]["queued"] = 0
        self.assertIsNone(runtime.worker_spawn_pressure(snapshot))

    def test_single_model_slot_reserves_tight_dispatch_steps_for_parent(self):
        dispatch = {
            "limits": {"step_limit": 10},
            "usage": {"steps": 1},
        }
        one_slot = {
            "resources": {"model_generation": {"cap": 1}},
        }
        two_slots = {
            "resources": {"model_generation": {"cap": 2}},
        }

        self.assertEqual(
            3,
            runtime.effective_worker_step_limit(14, 8, dispatch, one_slot),
        )
        self.assertEqual(
            8,
            runtime.effective_worker_step_limit(14, 8, dispatch, two_slots),
        )

        roomy = {
            "limits": {"step_limit": 30},
            "usage": {"steps": 1},
        }
        self.assertEqual(
            8,
            runtime.effective_worker_step_limit(14, 8, roomy, one_slot),
        )

        self.assertEqual(
            ("researcher", "single_slot_tight_dispatch_scout"),
            runtime.effective_worker_role("fixed_one", "implementer", 10, one_slot),
        )
        self.assertEqual(
            ("researcher", "single_slot_tight_dispatch_scout"),
            runtime.effective_worker_role("fixed_one", "implementer", 15, one_slot),
        )
        self.assertEqual(
            ("implementer", None),
            runtime.effective_worker_role("fixed_one", "implementer", 30, one_slot),
        )



if __name__ == "__main__":
    unittest.main()
