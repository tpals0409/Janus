"""오케스트레이터 스펙 검증 규칙."""

from __future__ import annotations

import os
import unittest

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from janus_server import spec as S


BASE = {"name": "T", "model": "qwen3.8-27b", "system_prompt": "hi",
        "tools": ["grep"], "approval": "auto", "max_steps": 10}


class SpecTests(unittest.TestCase):
    def assert_rejected(self, spec: dict, needle: str):
        with self.assertRaises(S.SpecError) as ctx:
            S.validate(spec)
        self.assertIn(needle, str(ctx.exception))

    def test_minimal_valid_spec_passes(self):
        S.validate(BASE)
        S.validate({**BASE, "tools": []})  # create_worker는 어차피 주입된다

    def test_unknown_tool_rejected(self):
        self.assert_rejected({**BASE, "tools": ["nope"]}, "알 수 없는 도구")

    def test_create_worker_in_tools_rejected(self):
        # 항상 주입되는 스킬 — YAML에 적으면 워커까지 스폰 권한이 새는 길이 열린다
        self.assert_rejected({**BASE, "tools": ["grep", "create_worker"]}, "항상 주입됩니다")

    def test_dangerous_tools_require_ask(self):
        self.assert_rejected({**BASE, "tools": ["run_bash"]}, "approval: auto 를 쓸 수 없습니다")
        self.assert_rejected({**BASE, "tools": ["http_get"]}, "approval: auto 를 쓸 수 없습니다")
        S.validate({**BASE, "tools": ["run_bash"], "approval": "ask"})

