"""에이전트 목록과 로드 실패가 앱 전체 오류로 번지지 않는지 검증한다."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import server


class AgentLoadTests(unittest.TestCase):
    client = TestClient(server.app)

    def test_broken_yaml_is_returned_with_diagnostics_instead_of_500(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp)
            source = "name: [unterminated\n"
            (agents / "broken.yaml").write_text(source, encoding="utf-8")

            with patch.object(server, "AGENTS_DIR", agents):
                listing = self.client.get(
                    "/agents", headers={"x-janus-token": server.AUTH_TOKEN}
                )
                detail = self.client.get(
                    "/agents/broken", headers={"x-janus-token": server.AUTH_TOKEN}
                )

        self.assertEqual(200, listing.status_code)
        self.assertIn("error", listing.json()[0])
        self.assertEqual(200, detail.status_code)
        self.assertIsNone(detail.json()["spec"])
        self.assertEqual(source, detail.json()["yaml"])
        self.assertTrue(detail.json()["errors"][0].startswith("YAML 파싱 실패:"))

    def test_non_mapping_yaml_is_not_exposed_as_a_canvas_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp)
            (agents / "scalar.yaml").write_text("just text\n", encoding="utf-8")

            with patch.object(server, "AGENTS_DIR", agents):
                detail = self.client.get(
                    "/agents/scalar", headers={"x-janus-token": server.AUTH_TOKEN}
                )

        self.assertEqual(200, detail.status_code)
        self.assertIsNone(detail.json()["spec"])
        self.assertEqual(["스펙 최상위는 매핑이어야 합니다"], detail.json()["errors"])

    def test_post_creates_valid_orchestrator_spec(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.object(server, "AGENTS_DIR", Path(tmp)):
                created = self.client.post(
                    "/agents", json={"name": "새 팀"},
                    headers={"x-janus-token": server.AUTH_TOKEN},
                )

        self.assertEqual(200, created.status_code)
        body = created.json()
        self.assertEqual([], body["errors"])
        self.assertEqual("새 팀", body["spec"]["name"])
        self.assertIn("model", body["spec"])

    def test_put_rejects_create_worker_in_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            agents = Path(tmp)
            spec = server._blank_spec("Orch")
            (agents / "orch.yaml").write_text("name: Orch\n", encoding="utf-8")
            with patch.object(server, "AGENTS_DIR", agents):
                saved = self.client.put(
                    "/agents/orch",
                    json={"spec": {**spec, "tools": ["create_worker"]}},
                    headers={"x-janus-token": server.AUTH_TOKEN},
                )

        self.assertEqual(200, saved.status_code)
        self.assertFalse(saved.json()["saved"])
        self.assertTrue(any("항상 주입됩니다" in e for e in saved.json()["errors"]))


if __name__ == "__main__":
    unittest.main()
