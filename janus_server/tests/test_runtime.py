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

from janus_server import runtime, server
from janus_server import spec as S
from tests.fakes import FakeClient, text_chunk

ORIGIN = "http://localhost:5173"
SPEC = {"name": "Orch", "model": "qwen3.8-27b", "system_prompt": "orchestrate",
        "tools": ["echo"], "approval": "auto", "max_steps": 6}


def worker_args(name="helper", tools=None, task="do it"):
    return json.dumps({"name": name, "system_prompt": "work", "task": task,
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

    def test_parallel_workers_run_concurrently(self):
        barrier = threading.Barrier(2, timeout=10)  # 순차 실행이면 타임아웃으로 깨진다

        def worker_turn():
            barrier.wait()
            return {"text": "wdone"}

        fake = FakeClient([
            {"calls": [("create_worker", worker_args("a")),
                       ("create_worker", worker_args("b"))]},
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


if __name__ == "__main__":
    unittest.main()
