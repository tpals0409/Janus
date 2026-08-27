"""구독형 CLI 실행기 — 스트림 매핑·턴 실행·재개 인자·프로바이더 배선 테스트."""

from __future__ import annotations

import os
import stat
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import cli_runner
from janus_server.workspace import WorkspaceContext

OUTCOME = (
    '<janus-outcome>{\\"outcome\\":\\"completed\\",\\"summary\\":\\"done\\",'
    '\\"evidence\\":[\\"a.py\\"]}</janus-outcome>'
)
FAKE_CLAUDE = """#!/bin/sh
cat <<'EOF'
{"type":"system","subtype":"init","session_id":"cli-abc"}
{"type":"assistant","message":{"content":[{"type":"text","text":"working"}]}}
{"type":"result","subtype":"success","result":"finished the task\\n%s","usage":{"input_tokens":10,"output_tokens":3,"cache_read_input_tokens":4}}
EOF
""" % OUTCOME


def fake_claude_login(tmp: str) -> dict:
    """HOME을 임시로 바꿔 인증 게이트를 결정적으로 통과시킨다."""
    home = Path(tmp) / "home"
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / ".credentials.json").write_text("{}")
    previous = os.environ.get("HOME")
    os.environ["HOME"] = str(home)
    return {"HOME": previous}


def restore_env(saved: dict) -> None:
    for key, value in saved.items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def make_runner(tmp: str, provider: str = "claude_code") -> tuple[cli_runner.CliOrchestration, list[dict]]:
    sent: list[dict] = []
    context = WorkspaceContext(Path(tmp), "task-cli", "workspace-cli", "dispatch-cli")
    runner = cli_runner.CliOrchestration(
        {"provider": provider}, send=sent.append,
        workspace_context=context, task_id="task-cli", session_id="session-cli",
    )
    return runner, sent


class CliRunnerTests(unittest.TestCase):
    def test_turn_runs_the_cli_and_maps_events_to_the_session_contract(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "fake-claude"
            stub.write_text(FAKE_CLAUDE)
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            os.environ["JANUS_CLAUDE_BIN"] = str(stub)
            saved = fake_claude_login(tmp)
            try:
                runner, sent = make_runner(tmp)
                runner.turn("do the thing")
            finally:
                os.environ.pop("JANUS_CLAUDE_BIN", None)
                restore_env(saved)

        self.assertFalse(runner.turn_failed)
        self.assertEqual(
            {"outcome": "completed", "summary": "done", "evidence": ["a.py"]},
            runner.turn_outcome,
        )
        # outcome 블록은 사용자에게 보이는 답변에서 지워진다.
        self.assertEqual("finished the task", runner.session.events[-1]["content"])
        self.assertEqual("cli-abc", runner.cli_session_id)
        budget = runner.snapshot_budget()
        self.assertIsNone(budget["exhausted_reason"])  # 세션 마무리가 이 키를 읽는다
        self.assertEqual(14, budget["usage"]["prompt_tokens"])
        self.assertEqual(3, budget["usage"]["completion_tokens"])
        self.assertEqual(1, budget["usage"]["steps"])
        self.assertGreater(budget["usage"]["active_time_ms"], 0)
        kinds = [event["kind"] for event in sent]
        self.assertEqual(
            ["user", "cli_session", "text_delta", "assistant", "usage",
             "assistant", "done"],
            kinds,
        )
        usage = next(e for e in sent if e["kind"] == "usage")
        self.assertEqual(4, usage["cached_tokens"])
        # 다음 턴은 --resume으로 이어진다.
        self.assertIn("--resume", runner._command("again"))
        # 주입 마커가 남아 같은 대화에는 다시 주입하지 않는다.
        self.assertIn("cli_context", [e["kind"] for e in runner.session.events])
        self.assertNotIn("--append-system-prompt", runner._command("again"))
        self.assertEqual(
            ["user", "cli_context", "cli_session", "assistant", "assistant"],
            [event["kind"] for event in runner.session.events],
        )
        self.assertEqual(
            cli_runner.CONTEXT_VERSION, runner.session.events[1]["version"],
        )

    def test_missing_cli_fails_the_turn_with_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ["JANUS_CLAUDE_BIN"] = str(Path(tmp) / "nope")
            saved = fake_claude_login(tmp)
            try:
                runner, _sent = make_runner(tmp)
                with self.assertRaises(RuntimeError) as caught:
                    runner.turn("hello")
            finally:
                os.environ.pop("JANUS_CLAUDE_BIN", None)
                restore_env(saved)
        self.assertIn("설치되어 있지 않습니다", str(caught.exception))
        self.assertTrue(runner.turn_failed)

    def test_codex_events_map_to_commands_and_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, sent = make_runner(tmp, provider="codex")
        runner._map_codex({"type": "item.completed", "item": {
            "item_type": "command_execution", "id": "i1",
            "command": "ls", "aggregated_output": "file.txt", "exit_code": 0,
        }})
        runner._map_codex({"type": "item.completed", "item": {
            "item_type": "agent_message",
            "text": 'all done\n<janus-outcome>{"outcome":"partial",'
                    '"summary":"절반"}</janus-outcome>',
        }})
        runner._map_codex({"type": "turn.completed", "usage": {
            "input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 1,
        }})
        self.assertEqual(
            {"outcome": "partial", "summary": "절반", "evidence": []},
            runner.turn_outcome,
        )
        self.assertEqual("all done", runner.last_text)
        # 캐시 적중분을 prompt_tokens에 더하는 의미를 claude 쪽과 맞춘다.
        self.assertEqual(9, runner.usage["prompt_tokens"])
        self.assertEqual(
            ["tool_start", "tool_result", "text_delta", "assistant", "usage"],
            [event["kind"] for event in sent],
        )

    def test_codex_thread_id_resumes_the_conversation(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, sent = make_runner(tmp, provider="codex")
        runner._map_codex({"type": "thread.started", "thread_id": "th-42"})
        self.assertEqual("th-42", runner.cli_session_id)
        self.assertEqual("cli_session", sent[0]["kind"])
        self.assertEqual(
            ["cli_session"], [event["kind"] for event in runner.session.events],
        )
        command = runner._command("이어서")
        self.assertEqual(["exec", "resume"], command[1:3])
        self.assertIn("th-42", command)
        self.assertIn("--ignore-user-config", command)
        self.assertTrue(command[-1].endswith("이어서"))
        # 재개 경로에는 --sandbox 플래그가 없어 config override로 같은 정책을 준다.
        self.assertIn('sandbox_mode="workspace-write"', command)

    def test_missing_outcome_block_settles_the_turn_as_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)
        runner._map_claude({"type": "result", "subtype": "success",
                            "result": "그냥 설명만 했습니다"})
        self.assertEqual("partial", runner.turn_outcome["outcome"])
        self.assertEqual("그냥 설명만 했습니다", runner.turn_outcome["summary"])

    def test_unknown_outcome_value_falls_back_to_partial(self):
        cleaned, outcome = cli_runner.extract_outcome(
            'ok\n<janus-outcome>{"outcome":"shipped","summary":"x"}</janus-outcome>'
        )
        self.assertEqual("ok", cleaned)
        self.assertEqual("partial", outcome["outcome"])
        # 파싱 실패해도 블록은 사용자에게 보이지 않아야 한다.
        cleaned, outcome = cli_runner.extract_outcome(
            "ok\n<janus-outcome>{not json}</janus-outcome>"
        )
        self.assertEqual("ok", cleaned)
        self.assertIsNone(outcome)

    def test_unauthenticated_codex_short_circuits_with_guidance(self):
        with tempfile.TemporaryDirectory() as tmp:
            stub = Path(tmp) / "fake-codex"
            stub.write_text("#!/bin/sh\nexit 1\n")
            stub.chmod(stub.stat().st_mode | stat.S_IEXEC)
            os.environ["JANUS_CODEX_BIN"] = str(stub)
            try:
                runner, sent = make_runner(tmp, provider="codex")
                runner.turn("hello")
            finally:
                os.environ.pop("JANUS_CODEX_BIN", None)
        self.assertTrue(runner.turn_failed)
        self.assertIn("codex login", runner.turn_outcome["summary"])
        kinds = [event["kind"] for event in sent]
        self.assertEqual(["user", "assistant", "done"], kinds)
        self.assertEqual("cli_auth", sent[-1]["reason"])
        # 로그인 후 재시도를 위해 점검 상태가 리셋된다.
        self.assertFalse(runner._auth_checked)

    def test_claude_auth_check_uses_credentials_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            os.environ["JANUS_SKIP_KEYCHAIN"] = "1"
            try:
                missing = cli_runner.check_cli_auth("claude_code", home=home)
                self.assertIn("claude", missing)
                (home / ".claude").mkdir()
                (home / ".claude" / ".credentials.json").write_text("{}")
                self.assertIsNone(cli_runner.check_cli_auth("claude_code", home=home))
            finally:
                os.environ.pop("JANUS_SKIP_KEYCHAIN", None)

    def test_first_turn_injects_the_janus_agent_contract(self):
        """로컬 경로와 같은 페르소나·코딩 규칙이 CLI에도 들어가야 한다."""
        with tempfile.TemporaryDirectory() as tmp:
            claude, _ = make_runner(tmp)
            claude.spec["system_prompt"] = "팀 규칙을 지켜라"
            claude.spec["context_preamble"] = "Task 목표: 할일 앱"
            command = claude._command("hello")
            self.assertIn("--append-system-prompt", command)
            context = command[command.index("--append-system-prompt") + 1]
            self.assertIn("You are Janus", context)  # personas/janus.md
            self.assertIn("Worker contract", context)
            self.assertIn("Task Contract", context)  # builtin_skills/task-contract
            self.assertIn("Coding Rules", context)  # policies/coding-rules.md
            self.assertIn("Janus CLI adapter", context)
            self.assertIn("<janus-outcome>", context)
            self.assertIn("팀 규칙을 지켜라", context)  # Delegated emphasis
            self.assertIn("Task 목표: 할일 앱", context)

            codex, _ = make_runner(tmp, provider="codex")
            codex.spec["context_preamble"] = "Task 목표: 할일 앱"
            prompt = codex._command("hello")[-1]
            self.assertIn("[Janus environment context]", prompt)
            self.assertIn("You are Janus", prompt)
            self.assertIn("Task 목표: 할일 앱", prompt)
            self.assertTrue(prompt.endswith("hello"))

    def test_personal_global_cli_config_is_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            claude, _ = make_runner(tmp)
            command = claude._command("hello")
            self.assertEqual(
                "project,local", command[command.index("--setting-sources") + 1],
            )
            codex, _ = make_runner(tmp, provider="codex")
            self.assertIn("--ignore-user-config", codex._command("hello"))

    def test_transcript_restore_recovers_the_cli_session_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)
        runner.restore_transcript([
            {"kind": "user", "content": "hi"},
            {"kind": "cli_session", "id": "old-1"},
            {"kind": "assistant", "content": "ok"},
        ])
        self.assertEqual("old-1", runner.cli_session_id)
        # 주입된 적 없는 기존 대화 — 재개여도 컨텍스트를 1회 주입한다.
        command = runner._command("continue")
        self.assertIn("--resume", command)
        self.assertIn("--append-system-prompt", command)

        with tempfile.TemporaryDirectory() as tmp:
            injected, _ = make_runner(tmp)
        injected.restore_transcript([
            {"kind": "cli_session", "id": "old-2"},
            {"kind": "cli_context", "injected": True,
             "version": cli_runner.CONTEXT_VERSION},
        ])
        self.assertNotIn("--append-system-prompt", injected._command("continue"))

        # 구판 마커(3문장 안내만 받은 대화)는 새 계약을 1회 다시 받는다.
        with tempfile.TemporaryDirectory() as tmp:
            stale, _ = make_runner(tmp)
        stale.restore_transcript([
            {"kind": "cli_session", "id": "old-3"},
            {"kind": "cli_context", "injected": True},
        ])
        self.assertIn("--append-system-prompt", stale._command("continue"))


if __name__ == "__main__":
    unittest.main()
