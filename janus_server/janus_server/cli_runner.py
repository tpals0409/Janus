"""구독형 CLI 실행기 — Claude Code(claude)와 Codex(codex)를 턴 실행기로 스폰한다.

구독 결제는 raw API를 주지 않으므로, 각사 CLI를 headless로 실행해 구조화
이벤트 스트림을 Janus 세션 이벤트로 매핑한다. Janus 오케스트레이터의 워커·예산·
컨텍스트 압축은 이 경로에 적용되지 않는다 — CLI가 자체 에이전트 루프를 돈다.

다만 **에이전트 계약**은 로컬 경로와 같아야 한다. personas/janus.md와
policies/coding-rules.md를 그대로 주입하고, 도구가 없는 두 지점(create_worker,
finish_turn)만 CLI adapter 섹션에서 대체한다.

sessions.py가 기대하는 Orchestration 표면(turn/cancel_all/session.events/
snapshot_*)만 덕타이핑으로 구현한다.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace

from . import agent as agent_mod
from . import mcp_bridge
from . import runtime as runtime_mod
from .workspace import WorkspaceContext

ORCH_ID = "orchestrator"
CLI_BINS = {
    "claude_code": lambda: os.environ.get("JANUS_CLAUDE_BIN", "claude"),
    "codex": lambda: os.environ.get("JANUS_CODEX_BIN", "codex"),
}
OUTCOME_VALUES = ("completed", "partial", "input_required", "mockup_review")
# Janus 도구 → Claude Code 내장 도구. skills.TOOL_MAP의 역방향이다(그쪽은 스킬을
# 들여올 때 Claude 이름을 Janus 이름으로 접는다). 여기서는 프로필이 준 도구만
# CLI에 붙이려고 쓴다 — 매핑이 없는 Janus 도구는 CLI에 대응물이 없다는 뜻이라 뺀다.
CLAUDE_TOOLS = {
    "read_file": ("Read",),
    "glob": ("Glob",),
    "grep": ("Grep",),
    "write_file": ("Write",),
    "edit_file": ("Edit",),
    "run_bash": ("Bash",),
    "http_get": ("WebFetch",),
}
# 프로필이 도구를 선언하지 않았을 때의 바닥값. 읽기만 준다 — 쓰기를 기본으로 주면
# 선언을 잊은 프로필이 조용히 최대 권한을 받는다.
CLAUDE_READ_ONLY = ("Read", "Glob", "Grep")


def claude_tools(janus_tools: object, *, bridged: list[str] | None = None) -> list[str]:
    """프로필의 Janus 도구 목록을 Claude 도구 이름으로 옮긴다.

    `bridged`에 든 도구는 내장 대응물을 **붙이지 않는다**. 그래야 CLI 세션에
    Write/Edit/Bash가 아예 없고, 쓰기를 하려면 MCP로 Janus를 거칠 수밖에 없다 —
    승인 게이트가 우회 불가능해지는 지점이 여기다.
    """
    names = [str(item) for item in janus_tools] if isinstance(janus_tools, list) else []
    if not names:
        return list(CLAUDE_READ_ONLY)
    held = set(bridged or ())
    mapped: list[str] = []
    for name in names:
        if name in held:
            continue
        for claude_name in CLAUDE_TOOLS.get(name, ()):
            if claude_name not in mapped:
                mapped.append(claude_name)
    mapped += [mcp_tool_name(name) for name in (bridged or ())]
    return mapped or list(CLAUDE_READ_ONLY)


def mcp_tool_name(janus_name: str) -> str:
    """Claude Code가 MCP 도구를 부르는 이름."""
    return f"mcp__{mcp_bridge.SERVER_NAME}__{janus_name}"


# 구독형 CLI가 받는 사고 강도. 두 CLI가 서로 다른 어휘를 쓰므로 각자의 것만 넘긴다 —
# 모르는 값을 그대로 넘기면 CLI가 인자 오류로 죽어 턴 전체가 실패한다.
CLI_EFFORTS = {
    "claude_code": ("low", "medium", "high", "xhigh", "max"),
    "codex": ("minimal", "low", "medium", "high"),
}


def model_flags(provider: str, config: object) -> list[str]:
    """ModelProfile.config의 model/effort를 해당 CLI의 인자로 옮긴다.

    값이 없거나 그 CLI가 모르는 effort면 아무것도 넘기지 않는다 — 그러면 CLI가
    자기 기본값(구독 플랜의 기본 모델)으로 돈다. 조용한 폴백이 인자 오류보다 낫다.
    """
    if not isinstance(config, dict):
        return []
    model = str(config.get("model") or "").strip()
    effort = str(config.get("effort") or "").strip().lower()
    flags: list[str] = []
    if model:
        flags += ["--model", model]
    if effort in CLI_EFFORTS.get(provider, ()):
        if provider == "claude_code":
            flags += ["--effort", effort]
        else:  # codex는 전용 플래그가 없어 config override로 준다
            flags += ["-c", f'model_reasoning_effort="{effort}"']
    return flags
# 주입한 계약의 판. 이걸 올리면 기존 대화도 새 계약을 1회 다시 받는다.
# 1 = 3문장 환경 안내(v1.0.21~22). 2 = 페르소나·코딩 규칙·outcome 계약.
CONTEXT_VERSION = 2
_OUTCOME_BLOCK = re.compile(r"<janus-outcome>\s*(\{.*?\})\s*</janus-outcome>", re.DOTALL)

# janus.md를 그대로 실으면 존재하지 않는 도구를 호출하라고 지시하게 된다.
# 어긋나는 세 지점만 여기서 덮어쓴다 — 나머지 계약은 페르소나 원문이 소유한다.
CLI_ADAPTER = """## Janus CLI adapter

You are running inside Janus, a local-first agent workbench, as a subscription CLI rather
than on the Janus local runtime. Three parts of the persona above are adapted here.

1. **No `create_worker`.** This runner structurally forbids Janus workers — exactly the
   exception the persona names. Delegate with your own CLI subagent tool if you have one;
   otherwise do the work yourself in this session. "Never implement the work yourself to
   route around a failed worker" does not apply when there is no worker to route around.

2. **No `finish_turn` tool.** Instead, end your final answer with exactly one line:

   <janus-outcome>{"outcome":"completed","summary":"…","evidence":["…"]}</janus-outcome>

   `outcome` is one of completed | partial | input_required | mockup_review, with the same
   meanings the persona gives: `completed` only when fresh evidence proves the requested
   work is done, `partial` when useful work finished but the Task remains open,
   `input_required` only for a concrete user decision that blocks progress. `summary` is a
   concise factual result; `evidence` lists changed files, commands, or other fresh proof.
   Janus strips this line before showing your answer and uses it to move the Task forward.
   Omitting it settles the turn as `partial`.

3. **Ignore the `personas/…` paths listed above** — they are Janus package resources you
   cannot read.

The current directory is the Task's workspace. Janus tracks your file changes with git and
shows them to the user for review, so do not commit unless you are asked to. Answer in the
same language as the request."""


def extract_outcome(text: str) -> tuple[str, dict | None]:
    """최종 답변에서 outcome 블록을 떼어낸다 — (표시용 텍스트, outcome|None).

    사용자에게 raw JSON이 보이면 안 되므로 파싱 실패 여부와 무관하게 블록은 지운다.
    클램프는 runtime.finish_turn과 동일하게 맞춘다.
    """
    matches = list(_OUTCOME_BLOCK.finditer(text or ""))
    cleaned = _OUTCOME_BLOCK.sub("", text or "").strip()
    if not matches:
        return cleaned, None
    try:  # 여러 개면 마지막 것이 최종 판정이다.
        payload = json.loads(matches[-1].group(1))
    except json.JSONDecodeError:
        return cleaned, None
    if not isinstance(payload, dict):
        return cleaned, None
    outcome = str(payload.get("outcome") or "").strip().lower()
    evidence = payload.get("evidence")
    return cleaned, {
        "outcome": outcome if outcome in OUTCOME_VALUES else "partial",
        "summary": str(payload.get("summary") or "").strip()[:1000],
        "evidence": (
            [str(item)[:500] for item in evidence[:10]]
            if isinstance(evidence, list) else []
        ),
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
        approver: Callable[[str, str, dict, WorkspaceContext], bool] | None = None,
        **_ignored,
    ) -> None:
        self.spec = spec
        self.provider = str(spec.get("provider"))
        if self.provider not in CLI_BINS:
            raise ValueError(f"CLI 실행기가 아닌 프로바이더: {self.provider}")
        self.send = send
        self.approver = approver
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
        self._context_injected = False
        self._injecting_context = False  # _command가 컨텍스트를 실었는지 — turn()이 읽는다
        self._turn_started = time.monotonic()
        self.pending_outcome: dict | None = None
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
        # 현재 판의 주입 마커가 있어야 이 대화가 Janus 계약을 안다. 구판 마커
        # (version 없음 = 3문장 안내만 받은 대화)는 새 계약을 1회 다시 받는다.
        self._context_injected = any(
            event.get("kind") == "cli_context"
            and int(event.get("version") or 1) >= CONTEXT_VERSION
            for event in self.session.events
        )

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
        self.pending_outcome = None
        self._turn_started = time.monotonic()
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

        self._injecting_context = False
        command = self._command(text)
        if self._injecting_context:
            self._context_injected = True
            self.session.append("cli_context", injected=True, version=CONTEXT_VERSION)
        try:
            self._process = subprocess.Popen(
                command,
                cwd=str(self.workspace_context.root),
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env=self._env(),
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
        self.usage["active_time_ms"] += round(
            (time.monotonic() - self._turn_started) * 1000, 3
        )

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
        """로컬 경로와 같은 에이전트 계약을 CLI에 싣는다.

        persona_prompt이 personas/janus.md + builtin_skills/task-contract를 이미
        조립하므로 여기서 프롬프트를 새로 쓰지 않는다 — 계약의 단일 원천을 유지한다.
        """
        parts = [
            runtime_mod.persona_prompt(
                "janus", custom_prompt=str(self.spec.get("system_prompt") or ""),
            ),
            agent_mod.CODING_RULES,
            CLI_ADAPTER,
        ]
        preamble = str(self.spec.get("context_preamble") or "").strip()
        if preamble:
            parts.append(preamble)
        return "\n\n---\n\n".join(parts)

    def _command(self, text: str) -> list[str]:
        inject = not self._context_injected
        if inject:
            self._injecting_context = True
        selection = model_flags(self.provider, self.spec.get("model_config"))
        if self.provider == "claude_code":
            bridged = self.mcp_tool_names()
            command = [
                CLI_BINS["claude_code"](), "-p", text,
                *selection,
                "--output-format", "stream-json", "--verbose",
                # 위험 도구는 MCP로 Janus를 거치므로 승인은 Janus가 묻는다. 여기서
                # 또 물으면 답할 사람이 없는 headless에서 그냥 거부로 끝난다.
                "--permission-mode", "acceptEdits",
                # --restricted가 파일 도구를 작업 디렉터리에 가두고(실측: 감옥 밖 읽기
                # 거부), 사용자·프로젝트·로컬 설정 파일을 무시하고, bypassPermissions를
                # 거부한다. --strict-mcp-config는 개인 MCP를 빼고 우리 것만 남긴다.
                "--restricted", "--strict-mcp-config",
                # 프로필이 준 도구만 붙인다. 전에는 --allowedTools Bash 고정이라
                # 프로필이 run_bash를 안 줘도 셸이 붙었다.
                "--tools", ",".join(claude_tools(self.spec.get("tools"), bridged=bridged)),
            ]
            if bridged:
                command += [
                    "--mcp-config", self._mcp_config(),
                    # CLI 층에서는 통과시킨다 — 실제 게이트는 Janus의 dispatch다.
                    "--allowedTools", ",".join(mcp_tool_name(n) for n in bridged),
                ]
            if self.cli_session_id:
                command += ["--resume", self.cli_session_id]
            if inject:
                # 주입된 적 없는 대화(신규·구버전에서 만든 세션)에 1회 주입한다.
                command += ["--append-system-prompt", self._janus_context()]
            return command
        # codex: exec는 JSONL 이벤트를 낸다. thread_id가 있으면 resume으로 대화를 잇는다.
        prompt = text
        if inject:
            prompt = f"[Janus environment context]\n{self._janus_context()}\n\n{text}"
        binary = CLI_BINS["codex"]()
        # 프로필이 쓰기 도구를 주지 않았으면 샌드박스도 읽기 전용으로 내린다.
        sandbox = "workspace-write" if self._may_write() else "read-only"
        if self.cli_session_id:
            # exec resume에는 --sandbox 플래그가 없어 같은 정책을 config override로 준다.
            return [
                binary, "exec", "resume", "--json", "--ignore-user-config",
                "-c", f'sandbox_mode="{sandbox}"', *selection,
                self.cli_session_id, prompt,
            ]
        return [
            binary, "exec", "--json", "--sandbox", sandbox,
            "--ignore-user-config", *selection, prompt,
        ]

    # ── MCP 다리 (routers/mcp.py가 부른다) ──

    def mcp_tool_names(self) -> list[str]:
        """이 세션이 MCP로 내주는 Janus 도구. claude 경로에서만 채워진다."""
        if self.provider != "claude_code":
            return []
        return mcp_bridge.bridged_tools(self.spec.get("tools"))

    def mcp_approve(self, name: str, args: dict) -> bool:
        """MCP 도구 한 건의 승인. 로컬 경로의 approval 정책을 그대로 따른다."""
        if self.spec.get("approval", "auto") == "auto":
            return True
        if self.approver is None:
            return False  # 브리지 없음 = 거부. tools.dispatch의 기본과 같다.
        return bool(self.approver(ORCH_ID, name, args, self.workspace_context))

    def _mcp_config(self) -> str:
        """--mcp-config에 줄 JSON. 세션 경로 + 전역 인증 토큰.

        서버는 127.0.0.1에만 바인딩한다(server.py). 포트는 기동할 때 읽는 값과
        같은 환경변수에서 가져와야 JANUS_PORT를 바꾼 사용자에게서 어긋나지 않는다.
        """
        from .shared import AUTH_TOKEN

        port = os.environ.get("JANUS_PORT", "8765")
        return json.dumps({"mcpServers": {mcp_bridge.SERVER_NAME: {
            "type": "http",
            "url": f"http://127.0.0.1:{port}/mcp/{self.session_id}",
            "headers": {"x-janus-token": AUTH_TOKEN},
        }}})

    def _env(self) -> dict:
        """CLI에 줄 환경. MCP 도구 호출이 승인 대기보다 먼저 끊기면 안 된다.

        claude의 기본 MCP 도구 타임아웃은 사람이 승인 대화상자를 보는 시간보다
        짧다. APPROVAL_TIMEOUT(300초)이 먼저 만료돼 거부로 정착해야 사용자가 보는
        결과와 Janus가 기록하는 결과가 같아진다.
        """
        from .shared import APPROVAL_TIMEOUT

        env = dict(os.environ)
        if self.mcp_tool_names():
            env["MCP_TOOL_TIMEOUT"] = str(int((APPROVAL_TIMEOUT + 60) * 1000))
        return env

    def _may_write(self) -> bool:
        tools = self.spec.get("tools")
        if not tools:  # 미선언 프로필은 로컬 기본과 같은 도구 집합으로 본다
            return True
        return bool(set(tools) & {"write_file", "edit_file", "run_bash"})

    def _emit(self, kind: str, **data) -> None:
        self.send({
            "type": "agent_event", "kind": kind, "node_id": ORCH_ID,
            "worker_id": None,
            "at_ms": round((time.monotonic() - self._turn_started) * 1000, 3),
            "task_id": self.task_id,
            "workspace_id": self.workspace_context.workspace_id,
            "session_id": self.session_id,
            "dispatch_id": self.workspace_context.dispatch_id,
            **data,
        })

    def _finish(self, *, ok: bool, summary: str) -> None:
        """CLI 종료를 곧 완료로 보지 않는다 — outcome은 모델이 선언한 값만 인정한다.

        로컬 경로에서 finish_turn을 부르지 않은 턴이 partial로 남는 것과 같은 규칙이다.
        """
        if not ok:
            self.turn_outcome = {
                "outcome": "failed", "summary": summary[:500], "evidence": [],
            }
            self.turn_failed = True
            return
        self.turn_outcome = self.pending_outcome or {
            "outcome": "partial",
            "summary": (summary or "Janus outcome 계약이 지켜지지 않음")[:500],
            "evidence": [],
        }
        self.turn_failed = False

    def _take_outcome(self, text: str) -> str:
        """표시용 텍스트에서 outcome 블록을 떼고 판정을 보관한다."""
        cleaned, outcome = extract_outcome(text)
        if outcome is not None:
            self.pending_outcome = outcome
        return cleaned

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
            text = self._take_outcome("\n".join(texts))
            self.usage["steps"] += 1  # assistant 메시지 1건 = 모델 생성 1회
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
            result_text = self._take_outcome(str(event.get("result") or ""))
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
        if kind == "thread.started":
            # claude의 --resume과 같은 자리 — 이게 없으면 매 턴이 콜드 스타트다.
            thread_id = str(event.get("thread_id") or "")
            if thread_id and thread_id != self.cli_session_id:
                self.cli_session_id = thread_id
                self.session.append("cli_session", id=thread_id)
                self._emit("cli_session", provider=self.provider, id=thread_id)
        elif kind == "item.completed":
            item_type = item.get("item_type") or item.get("type")
            if item_type == "agent_message" and item.get("text"):
                text = self._take_outcome(str(item["text"]))
                self.usage["steps"] += 1
                self.last_text = text or self.last_text
                if text:
                    self.session.append("assistant", content=text)
                    self._emit("text_delta", text=text)
                    self._emit("assistant", content=text)
            elif item_type == "file_change":
                self._emit("tool_result", name="file_change", value={
                    "content": json.dumps(item, ensure_ascii=False)[:4000],
                })
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
            # claude 쪽과 같은 의미로 맞춘다 — 캐시 적중분도 프롬프트로 계산했다.
            self.usage["prompt_tokens"] += prompt + cached
            self.usage["completion_tokens"] += completion
            self._emit(
                "usage", prompt_tokens=prompt + cached,
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
    runner._map_claude({
        "type": "result", "subtype": "success",
        "result": 'done\n<janus-outcome>{"outcome":"completed","summary":"고쳤다",'
                  '"evidence":["cli_runner.py"]}</janus-outcome>',
        "usage": {"input_tokens": 10, "output_tokens": 2,
                  "cache_read_input_tokens": 5},
    })
    assert runner.cli_session_id == "abc"
    assert runner.turn_outcome == {
        "outcome": "completed", "summary": "고쳤다", "evidence": ["cli_runner.py"],
    }, runner.turn_outcome
    assert runner.usage["prompt_tokens"] == 15 and runner.usage["completion_tokens"] == 2
    assert runner.usage["steps"] == 1, runner.usage
    kinds = [event["kind"] for event in sent]
    assert kinds == ["cli_session", "tool_start", "text_delta", "assistant",
                     "tool_result", "usage", "assistant"], kinds
    assert [e["kind"] for e in runner.session.events] == [
        "cli_session", "assistant", "tool_result", "assistant"]
    # outcome 블록은 사용자에게 보이는 텍스트에서 지워진다.
    assert runner.session.events[-1]["content"] == "done", runner.session.events[-1]
    # 블록이 없으면 로컬의 "finish_turn was not called"와 같은 partial로 남는다.
    bare = CliOrchestration(
        {"provider": "codex"}, send=[].append,
        workspace_context=context, task_id="t", session_id="s",
    )
    bare._map_codex({"type": "thread.started", "thread_id": "th-1"})
    bare._map_codex({"type": "item.completed", "item": {
        "item_type": "agent_message", "text": "다 했습니다"}})
    bare._map_codex({"type": "turn.completed", "usage": {}})
    assert bare.turn_outcome["outcome"] == "partial", bare.turn_outcome
    assert bare._command("again")[:4] == [
        CLI_BINS["codex"](), "exec", "resume", "--json"], bare._command("again")
    print("cli_runner self-check ok")


if __name__ == "__main__":
    _self_check()
