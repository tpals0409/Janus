"""Janus의 로컬 RCE 방어선 회귀 테스트."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

os.environ["JANUS_AUTH_TOKEN"] = "test-token"
os.environ["JANUS_ALLOWED_ORIGINS"] = "http://localhost:5173"
os.environ["JANUS_DB_FILE"] = str(
    Path(tempfile.gettempdir()) / f"janus-test-domain-{os.getpid()}.sqlite3"
)


from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from janus_server import server
from janus_server import tools as T
from janus_server.workspace import WorkspaceContext


class DispatchApprovalTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.context = WorkspaceContext(
            root=Path(self.temp.name), task_id="task_security",
            workspace_id="workspace_security", dispatch_id="dispatch_security",
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_dangerous_dispatch_defaults_to_deny(self):
        marker = Path(self.temp.name) / "should-not-exist"

        result = T.dispatch(
            "run_bash", {"command": f"touch {marker.name}"}, context=self.context
        )

        self.assertIn("승인하지 않음", result["error"])
        self.assertFalse(marker.exists())

    def test_injected_registry_does_not_bypass_approval(self):
        # 실행별 레지스트리를 써도 위험 도구의 승인 게이트는 그대로다
        marker = Path(self.temp.name) / "registry-should-not-exist"
        reg = dict(T.REGISTRY)

        result = T.dispatch(
            "run_bash", {"command": f"touch {marker.name}"}, registry=reg,
            context=self.context,
        )

        self.assertIn("승인하지 않음", result["error"])
        self.assertFalse(marker.exists())


class ServerBoundaryTests(unittest.TestCase):
    def test_session_approval_scopes_keep_shell_and_file_access_separate(self):
        with tempfile.TemporaryDirectory() as tmp:
            context = WorkspaceContext(
                root=Path(tmp), task_id="task-1", workspace_id="workspace-1",
            )
            self.assertEqual(
                ("workspace_shell", "workspace-1"),
                server._session_approval_key("run_bash", context),
            )
            self.assertEqual(
                ("workspace_write", "workspace-1"),
                server._session_approval_key("edit_file", context),
            )
            self.assertIsNone(server._session_approval_key("http_get", context))

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
        patch_preflight = self.client.options(
            "/tasks/task-example",
            headers={
                "origin": self.origin,
                "access-control-request-method": "PATCH",
                "access-control-request-headers": "x-janus-token,content-type",
            },
        )
        self.assertEqual(200, patch_preflight.status_code)

    def test_websocket_accepts_only_authenticated_renderer(self):
        # 인증 실패는 accept 전에 끊긴다 — 더미 ID라도 저장소 접근 전에 거부된다.
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect(
                "/tasks/not-a-task/sessions/not-a-session",
                headers={"origin": self.origin},
                subprotocols=["janus", "wrong-token"],
            ):
                pass

        self.assertFalse(server._origin_allowed("https://attacker.example"))
        self.assertFalse(server._token_valid("wrong-token"))

        # 올바른 토큰·오리진이면 핸드셰이크는 수락된다(서브프로토콜 janus).
        # 이후 도메인 검증에서 닫히는 것까지가 정상 — 여기선 인증 게이트만 본다.
        accepted = False
        try:
            with self.client.websocket_connect(
                "/tasks/not-a-task/sessions/not-a-session",
                headers={"origin": self.origin},
                subprotocols=["janus", "test-token"],
            ) as ws:
                accepted = True
                self.assertEqual("janus", ws.accepted_subprotocol)
        except Exception:
            pass
        self.assertTrue(accepted)

