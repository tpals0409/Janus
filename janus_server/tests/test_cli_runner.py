"""구독형 CLI 실행기 — 스트림 매핑·턴 실행·재개 인자·프로바이더 배선 테스트."""

from __future__ import annotations

import json
import os
import stat
import tempfile
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import cli_runner, mcp_bridge
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
            # --restricted가 사용자·프로젝트·로컬 설정을 통째로 무시한다.
            self.assertIn("--restricted", command)
            self.assertIn("--strict-mcp-config", command)
            codex, _ = make_runner(tmp, provider="codex")
            self.assertIn("--ignore-user-config", codex._command("hello"))

    def test_only_the_profile_tools_reach_the_cli(self):
        """전에는 --allowedTools Bash 고정이라 프로필이 셸을 안 줘도 붙었다."""
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)
            runner.spec["tools"] = ["read_file", "glob", "grep"]
            command = runner._command("hello")
            tools = command[command.index("--tools") + 1].split(",")
            self.assertEqual(["Read", "Glob", "Grep"], tools)
            self.assertNotIn("Bash", tools)

            # 위험 도구는 내장 대응물을 주지 않는다. Bash/Edit/Write가 세션에 아예
            # 없어야 쓰기가 MCP로 Janus를 거치고, 승인 게이트에 우회로가 없다.
            runner.spec["tools"] = ["read_file", "run_bash", "edit_file"]
            command = runner._command("x")
            tools = command[command.index("--tools") + 1].split(",")
            self.assertEqual(
                ["Read", "mcp__janus__run_bash", "mcp__janus__edit_file"], tools)
            for builtin in ("Bash", "Edit", "Write"):
                self.assertNotIn(builtin, tools)

    def test_an_undeclared_profile_gets_read_only_not_everything(self):
        """도구 선언을 잊은 프로필이 조용히 최대 권한을 받으면 안 된다."""
        self.assertEqual(["Read", "Glob", "Grep"], cli_runner.claude_tools([]))
        self.assertEqual(["Read", "Glob", "Grep"], cli_runner.claude_tools(None))
        # 대응물이 없는 Janus 도구는 빠지고, 남는 게 없으면 읽기 전용으로 떨어진다.
        self.assertEqual(["Read", "Glob", "Grep"], cli_runner.claude_tools(["create_worker"]))

    def test_codex_sandbox_follows_the_profile_write_tools(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp, provider="codex")
            runner.spec["tools"] = ["read_file", "glob"]
            command = runner._command("hello")
            self.assertEqual("read-only", command[command.index("--sandbox") + 1])

            runner.spec["tools"] = ["read_file", "edit_file"]
            command = runner._command("hello")
            self.assertEqual("workspace-write", command[command.index("--sandbox") + 1])

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


class SelfCheckTests(unittest.TestCase):
    """모듈 self-check를 pytest가 부른다 — 아무도 안 돌려서 썩는 걸 막는다."""

    def test_tools_self_check_passes(self):
        from janus_server import tools

        tools.demo()

    def test_cli_runner_self_check_passes(self):
        cli_runner._self_check()


class McpApprovalTests(unittest.TestCase):
    """구독형 CLI의 건별 승인 — 위험 도구는 MCP로 Janus를 거쳐야만 실행된다.

    범위 제한(--restricted, --tools)은 무엇을 만질 수 있는지만 정했다. 이 계층이
    "이 파일을 고쳐도 됩니까?"를 묻는다.
    """

    def test_the_mcp_server_is_wired_only_when_a_dangerous_tool_is_granted(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)

            runner.spec["tools"] = ["read_file", "glob"]
            self.assertEqual([], runner.mcp_tool_names())
            self.assertNotIn("--mcp-config", runner._command("x"))
            self.assertNotIn("MCP_TOOL_TIMEOUT", runner._env())

            runner.spec["tools"] = ["read_file", "write_file"]
            self.assertEqual(["write_file"], runner.mcp_tool_names())
            command = runner._command("x")
            self.assertIn("--mcp-config", command)
            config = json.loads(command[command.index("--mcp-config") + 1])
            server = config["mcpServers"]["janus"]
            self.assertEqual("http", server["type"])
            # 세션마다 경로가 갈려야 다른 Task의 워크스페이스에 쓸 수 없다.
            self.assertTrue(server["url"].endswith("/mcp/session-cli"), server["url"])
            self.assertIn("x-janus-token", server["headers"])

    def test_the_tool_timeout_outlasts_the_approval_dialog(self):
        """CLI가 먼저 끊으면 사용자가 승인해도 이미 실패한 뒤다."""
        from janus_server.shared import APPROVAL_TIMEOUT

        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)
            runner.spec["tools"] = ["run_bash"]
            self.assertGreater(
                int(runner._env()["MCP_TOOL_TIMEOUT"]), APPROVAL_TIMEOUT * 1000)

    def test_a_denied_approval_stops_the_write_and_tells_the_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)
            runner.spec["tools"] = ["write_file"]
            runner.spec["approval"] = "ask"
            asked: list[tuple[str, dict]] = []

            def refuse(node_id, tool, args, context):
                asked.append((tool, args))
                return False

            runner.approver = refuse
            text = mcp_bridge.invoker(
                approve=runner.mcp_approve, context=runner.workspace_context,
            )("write_file", {"path": "secret.txt", "content": "x"})

            self.assertEqual([("write_file", {"path": "secret.txt", "content": "x"})], asked)
            self.assertTrue(text.startswith("ERROR: "), text)
            self.assertFalse((Path(tmp) / "secret.txt").exists(), "거부됐는데 파일이 생겼다")

    def test_an_approved_call_actually_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)
            runner.spec["tools"] = ["write_file"]
            runner.spec["approval"] = "ask"
            runner.approver = lambda *_: True
            mcp_bridge.invoker(
                approve=runner.mcp_approve, context=runner.workspace_context,
            )("write_file", {"path": "ok.txt", "content": "hello\n"})
            self.assertEqual("hello\n", (Path(tmp) / "ok.txt").read_text())

    def test_no_approver_denies_rather_than_falling_through(self):
        """브리지가 없는데 통과시키면 승인이 있다고 믿는 사용자를 배신한다."""
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp)
            runner.spec["tools"] = ["write_file"]
            runner.spec["approval"] = "ask"
            runner.approver = None
            self.assertFalse(runner.mcp_approve("write_file", {"path": "a", "content": "b"}))

            # approval=auto 프로필은 로컬 경로와 같이 그대로 통과한다.
            runner.spec["approval"] = "auto"
            self.assertTrue(runner.mcp_approve("write_file", {"path": "a", "content": "b"}))

    def test_model_and_effort_selection_reaches_both_clis(self):
        """구독형도 모델·사고 강도를 고를 수 있어야 한다 — 전에는 CLI 기본값에 갇혔다."""
        with tempfile.TemporaryDirectory() as tmp:
            claude, _ = make_runner(tmp)
            claude.spec["model_config"] = {"model": "opus", "effort": "high"}
            command = claude._command("hi")
            self.assertEqual(["--model", "opus"], command[3:5])
            self.assertIn("--effort", command)
            self.assertEqual("high", command[command.index("--effort") + 1])

            codex, _ = make_runner(tmp, provider="codex")
            codex.spec["model_config"] = {"model": "gpt-5.6-sol", "effort": "low"}
            command = codex._command("hi")
            self.assertIn("--model", command)
            self.assertEqual("gpt-5.6-sol", command[command.index("--model") + 1])
            self.assertIn('model_reasoning_effort="low"', command)
            # 프롬프트는 항상 마지막 위치를 지킨다 — 플래그가 인자 순서를 깨면 안 된다.
            # (첫 턴은 Janus 계약이 프롬프트 앞에 실리므로 끝으로 확인한다.)
            self.assertTrue(command[-1].endswith("hi"), command[-1][-40:])

            # resume 경로에서도 같은 선택이 유지되고, thread id·프롬프트가 끝에 남는다.
            codex.cli_session_id = "th-1"
            resumed = codex._command("again")
            self.assertIn("gpt-5.6-sol", resumed)
            self.assertEqual("th-1", resumed[-2])
            self.assertTrue(resumed[-1].endswith("again"), resumed[-1][-40:])

    def test_unknown_effort_is_dropped_rather_than_passed_through(self):
        """CLI가 모르는 값을 넘기면 인자 오류로 턴 전체가 죽는다 — 조용히 기본값으로."""
        self.assertEqual([], cli_runner.model_flags("claude_code", {"effort": "minimal"}))
        self.assertEqual([], cli_runner.model_flags("codex", {"effort": "xhigh"}))
        self.assertEqual([], cli_runner.model_flags("claude_code", None))
        self.assertEqual(
            ["--model", "sonnet"], cli_runner.model_flags("claude_code", {"model": "sonnet"}),
        )

    def test_codex_gets_no_mcp_bridge_yet(self):
        """codex는 streamable HTTP MCP를 지원하지만 헤더 인증이 아니라 bearer다.
        검증되지 않은 배선을 붙이는 대신, 범위 제한만 걸린 상태를 명시한다."""
        with tempfile.TemporaryDirectory() as tmp:
            runner, _ = make_runner(tmp, provider="codex")
            runner.spec["tools"] = ["write_file", "run_bash"]
            self.assertEqual([], runner.mcp_tool_names())
