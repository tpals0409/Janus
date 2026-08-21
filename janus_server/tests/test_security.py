"""Janus의 로컬 RCE 방어선 회귀 테스트."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ["JANUS_AUTH_TOKEN"] = "test-token"
os.environ["JANUS_ALLOWED_ORIGINS"] = "http://localhost:5173"
os.environ["JANUS_STATE_FILE"] = str(
    Path(tempfile.gettempdir()) / f"janus-test-state-{os.getpid()}.json"
)

from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from janus_server import compile as C
from janus_server import server
from janus_server import spec as S
from janus_server import tools as T


class DispatchApprovalTests(unittest.TestCase):
    def setUp(self):
        self.previous_workspace = T.WORKSPACE
        self.temp = tempfile.TemporaryDirectory()
        T.set_workspace(self.temp.name)

    def tearDown(self):
        T.WORKSPACE = self.previous_workspace
        self.temp.cleanup()

    @staticmethod
    def tool_spec(command: str) -> dict:
        return {
            "name": "approval-test",
            "nodes": [
                {"id": "start", "type": "start", "outputs": ["command"]},
                {
                    "id": "shell",
                    "type": "tool",
                    "tool": "run_bash",
                    "inputs": {"command": command},
                    "output": {"name": "result"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "inputs": {"result": "{{ shell.result }}"},
                },
            ],
            "edges": [
                {"from": "start", "to": "shell"},
                {"from": "shell", "to": "end"},
            ],
        }

    def test_dangerous_dispatch_defaults_to_deny(self):
        marker = Path(self.temp.name) / "should-not-exist"

        result = T.dispatch("run_bash", {"command": f"touch {marker.name}"})

        self.assertIn("승인하지 않음", result["error"])
        self.assertFalse(marker.exists())

    def test_tool_node_without_approver_defaults_to_deny(self):
        marker = Path(self.temp.name) / "tool-node-should-not-exist"
        spec = self.tool_spec(f"touch {marker.name}")

        state = C.build(spec).invoke(C.initial_state(spec, {}))

        value = state["outputs"]["shell"]["result"]
        self.assertIn("승인하지 않음", value["error"])
        self.assertFalse(marker.exists())

    def test_tool_node_uses_shared_approver(self):
        calls: list[tuple[str, str, dict]] = []
        spec = self.tool_spec("printf approved")
        graph = C.build(spec)

        state = graph.invoke(
            C.initial_state(spec, {}),
            config={
                "configurable": {
                    "approver": lambda node, tool, args: calls.append((node, tool, args)) or True
                }
            },
        )

        value = state["outputs"]["shell"]["result"]
        self.assertEqual([("shell", "run_bash", {"command": "printf approved"})], calls)
        self.assertEqual(0, value["exit_code"])
        self.assertEqual("approved", value["stdout"])


class ServerBoundaryTests(unittest.TestCase):
    client = TestClient(server.app)
    origin = "http://localhost:5173"

    def test_http_requires_token(self):
        response = self.client.get("/health", headers={"origin": self.origin})
        self.assertEqual(401, response.status_code)

    def test_http_rejects_untrusted_origin_even_with_token(self):
        response = self.client.get(
            "/health",
            headers={"origin": "https://attacker.example", "x-janus-token": "test-token"},
        )
        self.assertEqual(403, response.status_code)

    def test_http_accepts_trusted_origin_and_token(self):
        response = self.client.get(
            "/health",
            headers={"origin": self.origin, "x-janus-token": "test-token"},
        )
        self.assertEqual(200, response.status_code)
        self.assertTrue(response.json()["ok"])

    def test_cors_preflight_accepts_only_configured_origin(self):
        trusted = self.client.options(
            "/workspace",
            headers={
                "origin": self.origin,
                "access-control-request-method": "POST",
                "access-control-request-headers": "x-janus-token,content-type",
            },
        )
        untrusted = self.client.options(
            "/workspace",
            headers={
                "origin": "https://attacker.example",
                "access-control-request-method": "POST",
                "access-control-request-headers": "x-janus-token,content-type",
            },
        )

        self.assertEqual(200, trusted.status_code)
        self.assertEqual(self.origin, trusted.headers["access-control-allow-origin"])
        self.assertEqual(400, untrusted.status_code)

    def test_websocket_accepts_only_authenticated_renderer(self):
        with self.client.websocket_connect(
            "/run/not-started",
            headers={"origin": self.origin},
            subprotocols=["janus", "test-token"],
        ) as ws:
            self.assertEqual("janus", ws.accepted_subprotocol)

        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/run/not-started",
                headers={"origin": self.origin},
                subprotocols=["janus", "wrong-token"],
            ):
                pass

        self.assertFalse(server._origin_allowed("https://attacker.example"))
        self.assertFalse(server._token_valid("wrong-token"))

    def test_parallel_tools_emit_two_independent_approval_requests(self):
        spec = {
            "name": "parallel-approval",
            "nodes": [
                {"id": "start", "type": "start", "outputs": []},
                {
                    "id": "write_a",
                    "type": "tool",
                    "tool": "write_file",
                    "inputs": {"path": "a.txt", "content": "A"},
                    "output": {"name": "result"},
                },
                {
                    "id": "write_b",
                    "type": "tool",
                    "tool": "write_file",
                    "inputs": {"path": "b.txt", "content": "B"},
                    "output": {"name": "result"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "inputs": {
                        "a": "{{ write_a.result }}",
                        "b": "{{ write_b.result }}",
                    },
                },
            ],
            "edges": [
                {"from": "start", "to": "write_a"},
                {"from": "start", "to": "write_b"},
                {"from": "write_a", "to": "end"},
                {"from": "write_b", "to": "end"},
            ],
        }

        previous_workspace = T.WORKSPACE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agents = root / "agents"
            runs = root / "runs"
            workspace = root / "workspace"
            agents.mkdir()
            workspace.mkdir()
            (agents / "parallel.yaml").write_text(S.dumps(spec), encoding="utf-8")
            T.set_workspace(str(workspace))
            try:
                with (
                    patch.object(server, "AGENTS_DIR", agents),
                    patch.object(server, "RUNS_DIR", runs),
                    self.client.websocket_connect(
                        "/run/parallel",
                        headers={"origin": self.origin},
                        subprotocols=["janus", "test-token"],
                    ) as ws,
                ):
                    ws.send_json({"type": "run", "inputs": {}})
                    approvals = []
                    while len(approvals) < 2:
                        message = ws.receive_json()
                        if message["type"] == "run_error":
                            self.fail(message["error"])
                        if message["type"] == "approval_request":
                            approvals.append(message)

                    self.assertEqual({"write_a", "write_b"}, {r["node_id"] for r in approvals})
                    for request in approvals:
                        ws.send_json(
                            {"type": "approval_response", "id": request["id"], "approved": True}
                        )

                    while True:
                        message = ws.receive_json()
                        if message["type"] == "run_error":
                            self.fail(message["error"])
                        if message["type"] == "run_end":
                            break

                self.assertEqual("A", (workspace / "a.txt").read_text(encoding="utf-8"))
                self.assertEqual("B", (workspace / "b.txt").read_text(encoding="utf-8"))
            finally:
                T.WORKSPACE = previous_workspace


if __name__ == "__main__":
    unittest.main()
