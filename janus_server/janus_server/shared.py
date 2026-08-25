"""Janus 공유 런타임 상태·서비스 게터·도메인 직렬화 헬퍼.

server.py(앱 조립)와 routers/*(도메인 API)가 양쪽에서 쓰는 것들만 산다.
routers가 server를 import하지 않게 하는 것이 이 모듈의 존재 이유다 —
순환 참조가 없어야 어떤 import 순서로도 안전하게 기동한다.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import signal
import subprocess
import sys
import threading
from pathlib import Path

from . import agent as agent_mod
from . import domain as D
from . import event_bus as event_bus_mod
from . import github_service as github_mod
from . import runtime
from . import scheduler as scheduler_mod
from . import skills as skill_mod
from . import terminal_service as terminal_mod
from . import workspace_service as WS
from .workspace import WorkspaceContext

DOMAIN_DB_FILE = Path(
    os.environ.get("JANUS_DB_FILE", str(Path.home() / ".janus" / "janus.sqlite3"))
).expanduser()
WORKTREES_DIR = Path(
    os.environ.get("JANUS_WORKTREES_DIR", str(Path.home() / ".janus" / "workspaces"))
).expanduser()
BACKUPS_DIR = Path(
    os.environ.get("JANUS_BACKUPS_DIR", str(Path.home() / ".janus" / "backups"))
).expanduser()
DIAGNOSTICS_DIR = Path(
    os.environ.get("JANUS_DIAGNOSTICS_DIR", str(Path.home() / ".janus" / "diagnostics"))
).expanduser()
SKILLS_DIR = Path(
    os.environ.get("JANUS_SKILLS_DIR", str(Path.home() / ".janus" / "skills"))
).expanduser()
PACKAGED_SKILLS_DIR = Path(__file__).parent / "library_skills"
PACKAGED_SKILL_NAMESPACE = "janus"
_BACKUP_LOCK = threading.Lock()
_DOMAIN_LOCK = threading.Lock()
_DOMAIN_STORE: D.DomainStore | None = None
_DOMAIN_STORE_PATH: Path | None = None
_DOMAIN_RECOVERED_PATH: Path | None = None
_WORKSPACE_SERVICE_LOCK = threading.Lock()
_WORKSPACE_SERVICE: WS.WorkspaceService | None = None
_WORKSPACE_SERVICE_PATH: Path | None = None
_WORKSPACE_JOBS_LOCK = threading.Lock()
_WORKSPACE_JOBS: dict[str, threading.Thread] = {}
_TASK_RUNTIMES_LOCK = threading.Lock()
_TASK_RUNTIMES: dict[str, runtime.Orchestration] = {}
_VERIFICATION_JOBS_LOCK = threading.Lock()
_VERIFICATION_JOBS: dict[str, threading.Thread] = {}
_EVALUATION_JOBS_LOCK = threading.Lock()
# 승인 대기는 연결이 아니라 세션에 속한다. 연결 지역 변수로 두면 재연결한 클라이언트가
# 대기 중인 요청을 볼 수도, 답할 수도 없어 워커가 APPROVAL_TIMEOUT을 그대로 태운다.
_PENDING_APPROVALS_LOCK = threading.Lock()
_PENDING_APPROVALS: dict[str, dict[str, list]] = {}
_SESSION_APPROVAL_SCOPES = {
    "write_file": "workspace_write",
    "edit_file": "workspace_write",
    "run_bash": "workspace_shell",
}


def _session_approval_key(
    tool: str, context: WorkspaceContext,
) -> tuple[str, str] | None:
    """Return the narrow permission remembered for this websocket session."""
    scope = _SESSION_APPROVAL_SCOPES.get(tool)
    if scope is None:
        return None
    return (scope, context.workspace_id)
_EVALUATION_JOBS: dict[str, threading.Thread] = {}
_EVALUATION_PROCESSES: dict[str, subprocess.Popen[str]] = {}
_EVALUATION_CANCELLED: set[str] = set()
_GITHUB_SERVICE: github_mod.GitHubService | None = None
_TERMINAL_MANAGER_LOCK = threading.Lock()
_TERMINAL_MANAGER: terminal_mod.TerminalManager | None = None
_TERMINAL_MANAGER_PATH: Path | None = None
_EVENT_BUS = event_bus_mod.EventBus()


def _publish_change(topic: str, event: str = "changed", **payload: object) -> None:
    """Notify renderer subscribers without coupling domain writes to a socket."""
    _EVENT_BUS.publish(topic, event, **payload)
    if topic != "operations":
        operation_payload = {
            key: value for key, value in payload.items()
            if key in {"task_id", "workspace_id", "run_id", "experiment_id", "session_id"}
        }
        _EVENT_BUS.publish("operations", "changed", source=topic, **operation_payload)

# Electron main이 기동마다 만든 토큰을 서버/렌더러에만 나눠준다.
# 수동 기동도 무인증으로 열리지 않도록, env가 없으면 서버가 자체적으로
# 일회용 토큰을 만들고 콘솔에만 알린다.
AUTH_TOKEN = os.environ.get("JANUS_AUTH_TOKEN") or secrets.token_hex(32)
if "JANUS_AUTH_TOKEN" not in os.environ:
    print(f"[janus] generated auth token: {AUTH_TOKEN}", file=sys.stderr)

# supervisor의 SIGTERM grace(5s)보다 짧아야 SIGKILL 없이 정상 종료된다.
GRACEFUL_SHUTDOWN_SECONDS = 3

_DEFAULT_ORIGINS = "http://localhost:5173,http://127.0.0.1:5173,file://,null"
ALLOWED_ORIGINS = frozenset(
    origin.strip()
    for origin in os.environ.get("JANUS_ALLOWED_ORIGINS", _DEFAULT_ORIGINS).split(",")
    if origin.strip()
)


def _origin_allowed(origin: str | None) -> bool:
    """브라우저가 보낸 요청은 신뢰하는 Janus 렌더러에서만 받는다.

    Origin이 없는 curl/네이티브 클라이언트는 토큰 검증을 그대로 거친다.
    """
    return origin is None or origin in ALLOWED_ORIGINS


def _token_valid(candidate: str | None) -> bool:
    return candidate is not None and hmac.compare_digest(candidate, AUTH_TOKEN)


def _pin_library_skills(store: D.DomainStore, agent_profile_id: str) -> None:
    """Let one AgentProfile name the packaged skills. 사람이 정한 activation은 덮지 않는다."""
    held = {
        item["skill_id"]: item
        for item in store.list_agent_profile_skills(agent_profile_id)
    }
    for skill in store.list_skills():
        if skill["namespace"] != PACKAGED_SKILL_NAMESPACE:
            continue
        prior = held.get(skill["id"])
        store.set_agent_profile_skill(
            agent_profile_id=agent_profile_id,
            skill_id=skill["id"],
            skill_version_id=skill["latest_version_id"],
            activation_mode=prior["activation_mode"] if prior else "manual",
            priority=prior["priority"] if prior else 100,
        )


def _ensure_packaged_skills(store: D.DomainStore) -> None:
    """Install versioned Janus library skills and pin them on every AgentProfile."""
    if not PACKAGED_SKILLS_DIR.is_dir():
        return
    for directory in skill_mod.discover_skill_directories(PACKAGED_SKILLS_DIR):
        relative = directory.relative_to(PACKAGED_SKILLS_DIR).as_posix()
        store.import_skill_version(**skill_mod.compile_skill_directory(
            directory,
            source_kind="local",
            source_locator="janus://packaged-skills",
            source_subpath=relative,
            namespace=PACKAGED_SKILL_NAMESPACE,
        ))
    for profile in store.list_agent_profiles():
        _pin_library_skills(store, profile["id"])


def get_domain_store() -> D.DomainStore:
    """DB 경로는 호출 시점에 해석한다 — 테스트와 앱 data dir 전환을 격리한다."""
    global _DOMAIN_STORE, _DOMAIN_STORE_PATH, _DOMAIN_RECOVERED_PATH
    path = Path(os.environ.get("JANUS_DB_FILE", str(DOMAIN_DB_FILE))).expanduser().resolve()
    with _DOMAIN_LOCK:
        if _DOMAIN_STORE is None or _DOMAIN_STORE_PATH != path:
            _DOMAIN_STORE = D.DomainStore(path)
            _DOMAIN_STORE_PATH = path
            _ensure_packaged_skills(_DOMAIN_STORE)
        if _DOMAIN_RECOVERED_PATH != path:
            _DOMAIN_STORE.recover_interrupted_runtime()
            _DOMAIN_RECOVERED_PATH = path
        return _DOMAIN_STORE


def get_workspace_service() -> WS.WorkspaceService:
    global _WORKSPACE_SERVICE, _WORKSPACE_SERVICE_PATH
    path = Path(
        os.environ.get("JANUS_WORKTREES_DIR", str(WORKTREES_DIR))
    ).expanduser().resolve()
    with _WORKSPACE_SERVICE_LOCK:
        if _WORKSPACE_SERVICE is None or _WORKSPACE_SERVICE_PATH != path:
            _WORKSPACE_SERVICE = WS.WorkspaceService(path)
            _WORKSPACE_SERVICE_PATH = path
        return _WORKSPACE_SERVICE


def get_github_service() -> github_mod.GitHubService:
    global _GITHUB_SERVICE
    if _GITHUB_SERVICE is None:
        _GITHUB_SERVICE = github_mod.GitHubService()
    return _GITHUB_SERVICE


def get_terminal_manager() -> terminal_mod.TerminalManager:
    global _TERMINAL_MANAGER, _TERMINAL_MANAGER_PATH
    store = get_domain_store()
    path = store.path.resolve()
    with _TERMINAL_MANAGER_LOCK:
        if _TERMINAL_MANAGER is not None and _TERMINAL_MANAGER_PATH != path:
            _TERMINAL_MANAGER.stop_all()
            _TERMINAL_MANAGER = None
        if _TERMINAL_MANAGER is None:
            def on_output(terminal_id: str, output: str, offset: int) -> None:
                try:
                    item = get_domain_store().append_task_terminal_output(
                        terminal_id, text=output, output_offset=offset
                    )
                    _publish_change(
                        "terminal", "output", terminal_id=terminal_id,
                        task_id=item["task_id"], output=output, output_offset=offset,
                    )
                except D.DomainError:
                    pass

            def on_exit(terminal_id: str, exit_code: int | None) -> None:
                try:
                    item = get_domain_store().finish_task_terminal(
                        terminal_id, state="exited", exit_code=exit_code
                    )
                    _publish_change(
                        "terminal", "exit", terminal_id=terminal_id,
                        task_id=item["task_id"], state="exited", exit_code=exit_code,
                    )
                except D.DomainError:
                    pass

            _TERMINAL_MANAGER = terminal_mod.TerminalManager(
                on_output=on_output, on_exit=on_exit
            )
            _TERMINAL_MANAGER_PATH = path
        return _TERMINAL_MANAGER


async def shutdown_local_resources(
    scheduler: scheduler_mod.ResourceScheduler | None = None,
    *,
    timeout: float = 10.0,
) -> bool:
    """Cancel live Task runtimes and wait for acquired leases to be returned."""
    with _TASK_RUNTIMES_LOCK:
        runtimes = list(_TASK_RUNTIMES.values())
    for orchestration in runtimes:
        orchestration.cancel_all()
    with _TERMINAL_MANAGER_LOCK:
        terminal_manager = _TERMINAL_MANAGER
    if terminal_manager is not None:
        await asyncio.to_thread(terminal_manager.stop_all)
    with _EVALUATION_JOBS_LOCK:
        evaluation_processes = list(_EVALUATION_PROCESSES.values())
        evaluation_threads = list(_EVALUATION_JOBS.values())
    for process in evaluation_processes:
        if process.poll() is None:
            process.send_signal(signal.SIGINT)
    for thread in evaluation_threads:
        await asyncio.to_thread(thread.join, timeout)
    target = scheduler or scheduler_mod.default_scheduler()
    target.close()
    return await asyncio.to_thread(target.wait_for_idle, timeout)

# ─────────────────────────── P1 ADE domain API ───────────────────────────


# IDE성 파일 탐색기에서 제외할 디렉토리 이름.
_IGNORE = {".git", "node_modules", ".venv", "__pycache__", "out", "dist"}


def _verification_commands(value: object) -> list[dict]:
    if not isinstance(value, list) or len(value) > 20:
        raise D.Conflict("verification_commands는 최대 20개의 배열이어야 합니다")
    commands: list[dict] = []
    for item in value:
        if not isinstance(item, dict):
            raise D.Conflict("verification command 항목은 객체여야 합니다")
        kind = str(item.get("kind") or "custom")
        command = str(item.get("command") or "").strip()
        if kind not in {"acceptance", "test", "lint", "typecheck", "custom"} or not command:
            raise D.Conflict("각 command에는 유효한 kind와 command가 필요합니다")
        commands.append({"kind": kind, "command": command})
    return commands


def _delegation_base_ref(repo: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), "symbolic-ref", "--quiet", "--short", "HEAD"],
            capture_output=True, text=True, timeout=5, check=False,
        )
        branch = completed.stdout.strip()
        if completed.returncode == 0 and branch:
            return branch
    except (OSError, subprocess.SubprocessError):
        pass
    return "main"


def _workspace_job_active(workspace_id: str) -> bool:
    with _WORKSPACE_JOBS_LOCK:
        thread = _WORKSPACE_JOBS.get(workspace_id)
        return bool(thread and thread.is_alive())


def _verification_workspace(task_id: str) -> tuple[dict, dict, dict]:
    store = get_domain_store()
    task = store.get_task(task_id)
    workspace = store.get_task_workspace(task_id)
    if workspace is None or workspace["state"] != "ready" or not workspace.get("root_path"):
        raise D.Conflict("ready Workspace가 있어야 verification을 실행할 수 있습니다")
    changes = get_workspace_service().changeset(
        repo_path=workspace["repo_path"], root_path=workspace["root_path"],
        base_ref=task["base_ref"],
    )
    return task, workspace, changes


def _review_snapshot(task_id: str) -> tuple[dict, dict, dict]:
    task, workspace, changes = _verification_workspace(task_id)
    return task, workspace, changes


def _evaluation_comparison_json(item: dict) -> dict:
    value = dict(item)
    value["thresholds"] = json.loads(value.pop("thresholds_json"))
    value["result"] = json.loads(value.pop("result_json"))
    return value


def _agent_profile_json(profile: dict) -> dict:
    return {
        **profile,
        "base_system_prompt": runtime.persona_prompt("janus"),
        "coding_rules_prompt": agent_mod.CODING_RULES,
        "effective_system_prompt": runtime.persona_prompt(
            "janus", custom_prompt=str(profile.get("system_prompt") or "")
        ),
        "tools": json.loads(profile["tools_json"]),
        "budget": json.loads(profile["budget_json"]),
        "context_policy": D.normalize_context_policy(json.loads(
            profile.get("context_policy_json") or "{}"
        )),
    }


def _model_profile_json(profile: dict) -> dict:
    return {**profile, "config": json.loads(profile["config_json"])}


def _dispatch_json(dispatch: dict) -> dict:
    return {
        **dispatch,
        "budget": json.loads(dispatch["budget_json"]),
        "usage": json.loads(dispatch["usage_json"]),
        "adaptive_decision": json.loads(dispatch.get("adaptive_decision_json") or "{}"),
        "agent_profile_snapshot": json.loads(
            dispatch.get("agent_profile_snapshot_json") or "{}"
        ),
    }


def _skill_json(item: dict) -> dict:
    value = dict(item)
    for source, target in (
        ("original_json", "original"),
        ("compiled_json", "compiled"),
        ("report_json", "report"),
    ):
        raw = value.pop(source, None)
        if raw is not None:
            value[target] = json.loads(raw)
    return value


def _skill_summary(item: dict) -> dict:
    value = _skill_json(item)
    value.pop("original", None)
    compiled = value.get("compiled") or {}
    if isinstance(compiled, dict):
        value["compiled"] = {
            key: compiled.get(key)
            for key in ("format", "name", "description", "activation", "execution", "capabilities")
            if key in compiled
        }
    return value


# ─────────────────────────── Task AgentSession runtime ───────────────────────────


def _learning_json(item: dict) -> dict:
    value = dict(item)
    value["confidence"] = float(value.get("confidence") or 0)
    value["evidence"] = json.loads(value.pop("evidence_json", "[]") or "[]")
    return value


APPROVAL_TIMEOUT = 300  # 초. 무응답은 거부로 친다.
