"""구독형 CLI 실행기 — Claude Code(claude)와 Codex(codex)를 턴 실행기로 스폰한다.

구독 결제는 raw API를 주지 않으므로, 각사 CLI를 headless로 실행해 구조화
이벤트 스트림을 Janus 세션 이벤트로 매핑한다. Janus 오케스트레이터의 워커·예산·
컨텍스트 압축은 이 경로에 적용되지 않는다 — CLI가 자체 에이전트 루프를 돈다.

sessions.py가 기대하는 Orchestration 표면(turn/cancel_all/session.events/
snapshot_*)만 덕타이핑으로 구현한다.
"""

from __future__ import annotations

import json
import os
import subprocess
import threading
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from .workspace import WorkspaceContext

ORCH_ID = "orchestrator"
CLI_BINS = {
    "claude_code": lambda: os.environ.get("JANUS_CLAUDE_BIN", "claude"),
    "codex": lambda: os.environ.get("JANUS_CODEX_BIN", "codex"),
}


def check_cli_auth(provider: str, *, home: Path | None = None) -> str | None:
    """미로그인이 확실할 때만 안내 문자열을 돌려준다 — 판별 불가면 None(통과).

    실패 후 진단보다 사전 안내가 낫다. 단, 과차단은 더 나쁘므로 확신 없는
    상태(점검 오류·타임아웃·알 수 없는 저장 방식)에서는 막지 않는다.
    """
    home = home or Path.home()
    if provider == "codex":
        try:
            status = subprocess.run(
                [CLI_BINS["codex"](), "login", "status"],
                capture_output=True, text=True, timeout=5,
            )
        except (OSError, subprocess.TimeoutExpired):
            return None
        if status.returncode != 0:
            return (
                "Codex가 로그인되어 있지 않습니다. 터미널에서 `codex login`을 "
                "실행해 ChatGPT 계정으로 로그인한 뒤 다시 시도하세요."
            )
        return None
    if provider == "claude_code":
        if (home / ".claude" / ".credentials.json").is_file():
            return None
        if os.environ.get("JANUS_SKIP_KEYCHAIN") != "1":
            try:
                keychain = subprocess.run(
                    ["security", "find-generic-password",
                     "-s", "Claude Code-credentials"],
                    capture_output=True, timeout=5,
                )
                if keychain.returncode == 0:
                    return None
            except (OSError, subprocess.TimeoutExpired):
                return None  # 점검 불가 — 막지 않는다
        return (
            "Claude Code가 로그인되어 있지 않습니다. 터미널에서 `claude`를 "
            "실행하고 /login으로 Claude 구독 계정에 로그인한 뒤 다시 시도하세요."
        )
    return None


class _Transcript:
    """agent.Session과 같은 events 계약만 제공한다 — 재접속 복원에 쓰인다."""

    def __init__(self) -> None:
        self.events: list[dict] = []

    def append(self, kind: str, **data) -> None:
        self.events.append({"kind": kind, **data})


class CliOrchestration:
    def __init__(
        self, spec: dict, *, send: Callable[[dict], None],
        workspace_context: WorkspaceContext,
        task_id: str | None = None, session_id: str | None = None,
        **_ignored,
    ) -> None:
        self.spec = spec
        self.provider = str(spec.get("provider"))
        if self.provider not in CLI_BINS:
            raise ValueError(f"CLI 실행기가 아닌 프로바이더: {self.provider}")
        self.send = send
        self.workspace_context = workspace_context
        self.task_id = task_id
        self.session_id = session_id
        self.session = _Transcript()
        self.cancel = threading.Event()
        self.cancelled_turn = False
        self.turn_failed = False
        self.budget_exhausted_reason: str | None = None
        self.turn_outcome: dict | None = None
        self.last_text = ""
        # dispatch usage 계약 전체를 채운다 — 세션 마무리가 이 dict를 그대로 저장한다.
        self.usage = {
            "prompt_tokens": 0, "completion_tokens": 0, "steps": 0,
            "active_time_ms": 0, "workers_started": 0, "peak_concurrent_workers": 0,
        }
        self._process: subprocess.Popen[str] | None = None
        self._auth_checked = False
        self._seq = 0
        # claude는 --resume으로 대화가 이어진다. 재시작 복원을 위해 transcript의
        # cli_session 이벤트에서 마지막 id를 되살린다.
        self.cli_session_id: str | None = None

    # ── sessions.py가 쓰는 표면 ──

    def restore_transcript(self, events: list[dict]) -> None:
        self.session.events = [dict(item) for item in events]
        for event in reversed(self.session.events):
            if event.get("kind") == "cli_session" and event.get("id"):
                self.cli_session_id = str(event["id"])
                break

    def cancel_all(self) -> None:
        self.cancel.set()
        process = self._process
        if process is not None and process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass

    def stop_worker(self, _node_id: str) -> dict:
        return {"error": "CLI 실행기에는 Janus 워커가 없습니다"}

    def snapshot_budget(self) -> dict:
        # sessions의 턴 마무리가 exhausted_reason 키를 직접 읽는다 — 계약을 지킨다.
        return {
            "scope": "dispatch", "limits": {},
            "usage": dict(self.usage), "exhausted_reason": None,
        }

    def snapshot_turn_outcome(self) -> dict:
        return dict(self.turn_outcome or {
            "outcome": "partial", "summary": "CLI 실행이 결과 없이 종료됨",
            "evidence": [],
        })

    def turn(self, text: str, *, dispatch_id: str | None = None) -> None:
        self.cancel.clear()
        self.cancelled_turn = False
        self.turn_failed = False
        self.turn_outcome = None
        self.session.append("user", content=text)
        self._emit("user", content=text)

        if not self._auth_checked:
            guidance = check_cli_auth(self.provider)
            self._auth_checked = True
            if guidance:
                self._auth_checked = False  # 로그인 후 재시도를 다시 점검한다
                self.session.append("assistant", content=guidance)
                self._emit("assistant", content=guidance)
                self.turn_failed = True
                self.turn_outcome = {
                    "outcome": "failed", "summary": guidance, "evidence": [],
                }
                self._emit("done", reason="cli_auth")
                return

        command = self._command(text)
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(self.workspace_context.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as error:
            self.turn_failed = True
            self.turn_outcome = {
                "outcome": "failed",
                "summary": f"{self.provider} CLI를 찾을 수 없습니다: {error}",
                "evidence": [],
            }
            self._emit("done", reason="cli_missing")
            raise RuntimeError(
                f"{self.provider} CLI가 설치되어 있지 않습니다. "
                "구독 로그인까지 마친 뒤 다시 시도하세요."
            ) from error

        assert self._process.stdout is not None
        for line in self._process.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if self.provider == "claude_code":
                self._map_claude(event)
            else:
                self._map_codex(event)
        stderr = ""
        if self._process.stderr is not None:
            stderr = self._process.stderr.read()[-2000:]
        code = self._process.wait()
        self._process = None

        if self.cancel.is_set():
            self.cancelled_turn = True
            self._emit("done", reason="cancelled")
            return
        if self.turn_outcome is None:
            self.turn_failed = code != 0
            self.turn_outcome = {
                "outcome": "failed" if code != 0 else "partial",
                "summary": (
                    f"{self.provider} CLI 종료 코드 {code}"
                    + (f" · {stderr.strip()[:300]}" if code != 0 and stderr.strip() else "")
                ),
                "evidence": [],
            }
        if code != 0 and not self.turn_failed:
            self.turn_failed = True
        self._emit("done", reason="cli_result")

    # ── 내부 ──

    def _janus_context(self) -> str:
        """Janus 환경·Task 컨텍스트 — 로컬 경로의 preamble을 CLI에도 전달한다."""
        parts = [
            "You are running inside Janus, a local-first agent workbench. "
            "The current directory is the task's workspace; Janus tracks your "
            "file changes via git and shows them to the user. Answer in the "
            "user's language.",
        ]
        custom = str(self.spec.get("system_prompt") or "").strip()
        if custom:
            parts.append(custom)
        preamble = str(self.spec.get("context_preamble") or "").strip()
        if preamble:
            parts.append(preamble)
        return "\n\n".join(parts)

    def _command(self, text: str) -> list[str]:
        if self.provider == "claude_code":
            command = [
                CLI_BINS["claude_code"](), "-p", text,
                "--output-format", "stream-json", "--verbose",
                # headless에는 승인 UI가 없다 — 워크스페이스 안 편집과 셸만 허용.
                "--permission-mode", "acceptEdits",
                "--allowedTools", "Bash",
            ]
            if self.cli_session_id:
                # 대화가 이어지므로 컨텍스트는 첫 턴에만 주입한다.
                command += ["--resume", self.cli_session_id]
            else:
                command += ["--append-system-prompt", self._janus_context()]
            return command
        # codex: exec는 JSONL 이벤트를 낸다. 세션 연속이 없어 매 턴 컨텍스트를 앞에 싣는다.
        return [
            CLI_BINS["codex"](), "exec", "--json",
            "--sandbox", "workspace-write",
            f"[Janus environment context]\n{self._janus_context()}\n\n{text}",
        ]

    def _emit(self, kind: str, **data) -> None:
        self._seq += 1
        self.send({
            "type": "agent_event", "kind": kind, "node_id": ORCH_ID,
            "worker_id": None, "at_ms": float(self._seq),
            "task_id": self.task_id,
            "workspace_id": self.workspace_context.workspace_id,
            "session_id": self.session_id,
            "dispatch_id": self.workspace_context.dispatch_id,
            **data,
        })

    def _finish(self, *, ok: bool, summary: str) -> None:
        self.turn_outcome = {
            "outcome": "completed" if ok else "failed",
            "summary": summary[:500], "evidence": [],
        }
        self.turn_failed = not ok

    def _map_claude(self, event: dict) -> None:
        kind = event.get("type")
        if kind == "system" and event.get("subtype") == "init":
            cli_session = str(event.get("session_id") or "")
            if cli_session and cli_session != self.cli_session_id:
                self.cli_session_id = cli_session
                self.session.append("cli_session", id=cli_session)
                self._emit("cli_session", provider=self.provider, id=cli_session)
        elif kind == "assistant":
            content = (event.get("message") or {}).get("content") or []
            texts: list[str] = []
            calls: list[dict] = []
            for block in content:
                if block.get("type") == "text" and block.get("text"):
                    texts.append(str(block["text"]))
                elif block.get("type") == "tool_use":
                    calls.append({
                        "id": str(block.get("id") or ""),
                        "name": str(block.get("name") or "tool"),
                        "input": block.get("input") or {},
                    })
                    self._emit(
                        "tool_start", name=str(block.get("name") or "tool"),
                        args=block.get("input") or {},
                        call_id=str(block.get("id") or ""),
                    )
            text = "\n".join(texts)
            if text:
                self.last_text = text
                self._emit("text_delta", text=text)
            self.session.append(
                "assistant", content=text,
                tool_calls=[{
                    "id": call["id"], "type": "function",
                    "function": {"name": call["name"],
                                 "arguments": json.dumps(call["input"], ensure_ascii=False)},
                } for call in calls] or None,
            )
            if text:
                self._emit("assistant", content=text)
        elif kind == "user":
            content = (event.get("message") or {}).get("content") or []
            for block in content:
                if block.get("type") != "tool_result":
                    continue
                value = block.get("content")
                rendered = value if isinstance(value, str) else json.dumps(
                    value, ensure_ascii=False)[:4000]
                self.session.append(
                    "tool_result", tool_call_id=str(block.get("tool_use_id") or ""),
                    name="tool", value={"content": rendered[:4000]},
                )
                self._emit(
                    "tool_result", name="tool",
                    call_id=str(block.get("tool_use_id") or ""),
                    value={"content": rendered[:4000]},
                )
        elif kind == "result":
            usage = event.get("usage") or {}
            prompt = int(usage.get("input_tokens") or 0)
            completion = int(usage.get("output_tokens") or 0)
            cached = int(usage.get("cache_read_input_tokens") or 0)
            self.usage["prompt_tokens"] += prompt + cached
            self.usage["completion_tokens"] += completion
            self._emit(
                "usage", prompt_tokens=prompt + cached,
                completion_tokens=completion, cached_tokens=cached,
            )
            result_text = str(event.get("result") or "").strip()
            if result_text and result_text != self.last_text:
                self.last_text = result_text
                self.session.append("assistant", content=result_text)
                self._emit("assistant", content=result_text)
            self._finish(
                ok=event.get("subtype") == "success",
                summary=result_text or str(event.get("subtype") or "result"),
            )

    def _map_codex(self, event: dict) -> None:
        kind = event.get("type")
        item = event.get("item") or {}
        if kind == "item.completed":
            item_type = item.get("item_type") or item.get("type")
            if item_type == "agent_message" and item.get("text"):
                text = str(item["text"])
                self.last_text = text
                self.session.append("assistant", content=text)
                self._emit("text_delta", text=text)
                self._emit("assistant", content=text)
            elif item_type == "reasoning" and item.get("text"):
                self._emit("reasoning_delta", text=str(item["text"]))
            elif item_type == "command_execution":
                command = str(item.get("command") or "")
                self._emit("tool_start", name="command", args={"command": command})
                output = str(item.get("aggregated_output") or "")[:4000]
                self.session.append(
                    "tool_result", tool_call_id=str(item.get("id") or ""),
                    name="command",
                    value={"command": command, "content": output,
                           "exit_code": item.get("exit_code")},
                )
                self._emit(
                    "tool_result", name="command",
                    value={"command": command, "content": output,
                           "exit_code": item.get("exit_code")},
                )
        elif kind == "turn.completed":
            usage = event.get("usage") or {}
            prompt = int(usage.get("input_tokens") or 0)
            cached = int(usage.get("cached_input_tokens") or 0)
            completion = int(usage.get("output_tokens") or 0)
            self.usage["prompt_tokens"] += prompt
            self.usage["completion_tokens"] += completion
            self._emit(
                "usage", prompt_tokens=prompt,
                completion_tokens=completion, cached_tokens=cached,
            )
            self._finish(ok=True, summary=self.last_text or "turn completed")
        elif kind == "turn.failed":
            self._finish(
                ok=False,
                summary=str((event.get("error") or {}).get("message") or "turn failed"),
            )


def is_cli_provider(provider: object) -> bool:
    return str(provider) in CLI_BINS


def make_orchestration(spec: dict, **kwargs) -> CliOrchestration:
    return CliOrchestration(spec, **kwargs)


def _self_check() -> None:
    """assert 기반 자기 검증 — 스트림 매핑이 세션/이벤트 계약을 지키는지."""
    sent: list[dict] = []
    context = SimpleNamespace(
        root="/tmp", workspace_id="w", dispatch_id="d", task_id="t",
    )
    runner = CliOrchestration(
        {"provider": "claude_code"}, send=sent.append,
        workspace_context=context, task_id="t", session_id="s",
    )
    runner._map_claude({"type": "system", "subtype": "init", "session_id": "abc"})
    runner._map_claude({"type": "assistant", "message": {"content": [
        {"type": "text", "text": "hello"},
        {"type": "tool_use", "id": "c1", "name": "Bash", "input": {"command": "ls"}},
    ]}})
    runner._map_claude({"type": "user", "message": {"content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "file.txt"},
    ]}})
    runner._map_claude({"type": "result", "subtype": "success", "result": "done",
                        "usage": {"input_tokens": 10, "output_tokens": 2,
                                  "cache_read_input_tokens": 5}})
    assert runner.cli_session_id == "abc"
    assert runner.turn_outcome and runner.turn_outcome["outcome"] == "completed"
    assert runner.usage["prompt_tokens"] == 15 and runner.usage["completion_tokens"] == 2
    kinds = [event["kind"] for event in sent]
    assert kinds == ["cli_session", "tool_start", "text_delta", "assistant",
                     "tool_result", "usage", "assistant"], kinds
    assert [e["kind"] for e in runner.session.events] == [
        "cli_session", "assistant", "tool_result", "assistant"]
    print("cli_runner self-check ok")


if __name__ == "__main__":
    _self_check()
