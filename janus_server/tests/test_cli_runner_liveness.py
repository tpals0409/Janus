"""CLI 실행기가 멈추지 않는다 — stderr 파이프 포화·턴 상한·취소 승격.

셋 다 "턴이 영영 안 끝난다"로 끝나는 경로다. stdout EOF 뒤에 stderr를 읽던
구조에서는 자식이 stderr 버퍼(보통 64KB)를 채우면 서로를 기다리며 교착했다.
"""

from __future__ import annotations

import os
import stat
import tempfile
import threading
import time
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import cli_runner
from janus_server.workspace import WorkspaceContext

# stdout으로 결과를 내기 전에 stderr로 256KB를 뱉는다 — 파이프 버퍼를 훨씬 넘는다.
NOISY_STDERR = """#!/bin/sh
awk 'BEGIN { for (i = 0; i < 4096; i++) printf "%064d\\n", i }' >&2
cat <<'EOF'
{"type":"system","subtype":"init","session_id":"cli-noisy"}
{"type":"result","subtype":"success","result":"survived the noise","usage":{"input_tokens":1,"output_tokens":1}}
EOF
"""

HANGS_FOREVER = """#!/bin/sh
sleep 300
"""


def install_stub(tmp: str, script: str) -> None:
    stub = Path(tmp) / "fake-claude"
    stub.write_text(script)
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
    os.environ["JANUS_CLAUDE_BIN"] = str(stub)
    home = Path(tmp) / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    os.environ["HOME"] = str(home)


def make_runner(tmp: str) -> cli_runner.CliOrchestration:
    context = WorkspaceContext(Path(tmp), "task-cli", "workspace-cli", "dispatch-cli")
    return cli_runner.CliOrchestration(
        {"provider": "claude_code"}, send=lambda _event: None,
        workspace_context=context, task_id="task-cli", session_id="session-cli",
    )


class CliLivenessTests(unittest.TestCase):
    def setUp(self) -> None:
        self._saved = {
            key: os.environ.get(key)
            for key in ("JANUS_CLAUDE_BIN", "HOME", "JANUS_CLI_TURN_TIMEOUT_SECONDS")
        }

    def tearDown(self) -> None:
        for key, value in self._saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_large_stderr_does_not_deadlock_the_turn(self):
        with tempfile.TemporaryDirectory() as tmp:
            install_stub(tmp, NOISY_STDERR)
            runner = make_runner(tmp)
            finished = threading.Event()

            def run() -> None:
                runner.turn("do the thing")
                finished.set()

            threading.Thread(target=run, daemon=True).start()
            self.assertTrue(
                finished.wait(30), "stderr 파이프가 가득 차 턴이 교착했다"
            )
        self.assertFalse(runner.turn_failed)
        self.assertEqual("survived the noise", runner.last_text)

    def test_turn_timeout_kills_the_cli_and_reports_it(self):
        original = cli_runner.TURN_TIMEOUT_SECONDS
        cli_runner.TURN_TIMEOUT_SECONDS = 1.0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                install_stub(tmp, HANGS_FOREVER)
                runner = make_runner(tmp)
                started = time.monotonic()
                runner.turn("do the thing")
                elapsed = time.monotonic() - started
        finally:
            cli_runner.TURN_TIMEOUT_SECONDS = original

        self.assertLess(elapsed, 30, "상한을 넘겨도 턴이 안 끝났다")
        self.assertTrue(runner.turn_failed)
        self.assertEqual("failed", runner.turn_outcome["outcome"])
        self.assertIn("상한", runner.turn_outcome["summary"])

    def test_cancel_escalates_to_kill_when_sigterm_is_ignored(self):
        ignores_sigterm = """#!/bin/sh
trap '' TERM
sleep 300
"""
        original = cli_runner.CANCEL_KILL_GRACE_SECONDS
        cli_runner.CANCEL_KILL_GRACE_SECONDS = 1.0
        try:
            with tempfile.TemporaryDirectory() as tmp:
                install_stub(tmp, ignores_sigterm)
                runner = make_runner(tmp)
                done = threading.Event()

                def run() -> None:
                    runner.turn("do the thing")
                    done.set()

                threading.Thread(target=run, daemon=True).start()
                # 프로세스가 실제로 뜰 때까지 기다린 뒤 취소한다.
                for _ in range(100):
                    if runner._process is not None:
                        break
                    time.sleep(0.05)
                runner.cancel_all()
                self.assertTrue(
                    done.wait(20), "SIGTERM을 무시하는 CLI에서 취소가 안 먹었다"
                )
        finally:
            cli_runner.CANCEL_KILL_GRACE_SECONDS = original
        self.assertTrue(runner.cancelled_turn)


if __name__ == "__main__":
    unittest.main()
