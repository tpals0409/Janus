"""Janus 서버 — Task·Session 중심의 로컬 ADE 백엔드.

렌더러는 이 서버하고만 통신한다. Electron main은 창과 다이얼로그만 담당한다.
Project/Task/Workspace/Dispatch/AgentSession 도메인과 Janus Local runtime,
검증·리뷰·출하·평가 API를 제공한다.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import threading
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import agent as agent_mod
from . import diagnostics, recovery, runtime
from . import domain as D
from . import event_bus as event_bus_mod
from . import github_service as github_mod
from . import scheduler as scheduler_mod
from . import skills as skill_mod
from . import terminal_service as terminal_mod
from . import workspace_service as WS
from .version import __version__
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


@asynccontextmanager
async def app_lifespan(_app: FastAPI):
    yield
    idle = await shutdown_local_resources()
    if not idle:
        print("[janus] scheduler shutdown timed out with active leases", file=sys.stderr)


app = FastAPI(title="Janus", version=__version__, lifespan=app_lifespan)


@app.websocket("/events")
async def stream_domain_events(ws: WebSocket):
    """Authenticated, app-wide domain change stream for the desktop renderer."""
    origin = ws.headers.get("origin")
    protocols = {
        value.strip()
        for value in ws.headers.get("sec-websocket-protocol", "").split(",")
        if value.strip()
    }
    if not _origin_allowed(origin) or "janus" not in protocols or not any(
        _token_valid(value) for value in protocols if value != "janus"
    ):
        await ws.close(code=1008)
        return

    await ws.accept(subprotocol="janus")
    subscription = _EVENT_BUS.subscribe()
    try:
        await ws.send_json({"topic": "system", "event": "ready", "sequence": 0})
        while True:
            try:
                message = await asyncio.wait_for(subscription.queue.get(), timeout=20)
            except TimeoutError:
                message = {"topic": "system", "event": "heartbeat"}
            await ws.send_json(message)
    except (WebSocketDisconnect, asyncio.CancelledError):
        # 종료 시 uvicorn이 이 연결을 끊는다 — 정상 경로이므로 traceback을 남기지 않는다.
        pass
    finally:
        _EVENT_BUS.unsubscribe(subscription.id)


@app.exception_handler(D.NotFound)
async def domain_not_found(_request: Request, error: D.NotFound):
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(D.Conflict)
async def domain_conflict(_request: Request, error: D.Conflict):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.exception_handler(D.InvalidTransition)
async def domain_transition(_request: Request, error: D.InvalidTransition):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.exception_handler(WS.InvalidRepository)
async def invalid_repository(_request: Request, error: WS.InvalidRepository):
    return JSONResponse({"detail": str(error)}, status_code=400)


@app.exception_handler(WS.WorkspaceServiceError)
async def workspace_conflict(_request: Request, error: WS.WorkspaceServiceError):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.middleware("http")
async def authenticate_http(request: Request, call_next):
    # CORS preflight는 CORSMiddleware가 origin/메서드/헤더를 검증한다.
    if request.method == "OPTIONS":
        return await call_next(request)
    if not _origin_allowed(request.headers.get("origin")):
        return JSONResponse({"detail": "허용되지 않은 Origin"}, status_code=403)
    if not _token_valid(request.headers.get("x-janus-token")):
        return JSONResponse({"detail": "Janus 인증 토큰이 필요합니다"}, status_code=401)
    return await call_next(request)


# CORS를 **인증 뒤에** 등록한다 = 스택의 가장 바깥.
# add_middleware는 앞에 끼워 넣으므로 나중에 등록한 쪽이 바깥이고, 바깥이어야
# 401/403 응답에도 Access-Control-Allow-Origin이 붙는다. 안 그러면 브라우저가
# 인증 실패를 CORS 오류로만 보고, UI가 "백엔드가 죽었다"로 오해한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_ORIGINS),
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Janus-Token"],
)


@app.get("/health")
def health():
    # 렌더러는 이 서버하고만 통신한다 — 모델 서버 상태도 여기서 대신 확인해준다.
    # 모델 로드에 수십 초가 걸리므로 UI가 "모델 로딩 중"을 구분해 보여줄 수 있어야 한다.
    import socket

    mlx = False
    try:
        with socket.create_connection(("127.0.0.1", 8080), timeout=0.3):
            mlx = True
    except OSError:
        pass
    try:
        schema_version = get_domain_store().schema_version()
    except D.DomainError as error:
        # DB가 앱보다 새 버전인 경우는 예상된 거절이다 — 500 + 트레이스백으로 흘리면
        # 폴링마다 로그를 채우고 화면에는 "백엔드 시작 중"이라는 거짓말이 남는다.
        return {
            "ok": False, "version": app.version, "mlx": mlx,
            "schema_version": None, "fault": str(error),
        }
    return {
        "ok": True, "version": app.version, "mlx": mlx,
        "schema_version": schema_version,
    }


@app.get("/maintenance/recovery")
def recovery_status():
    database = get_domain_store().path
    backups = Path(os.environ.get("JANUS_BACKUPS_DIR", str(BACKUPS_DIR))).expanduser()
    integrity = recovery.database_integrity(database)
    return {
        "database": integrity,
        "backups": recovery.list_database_backups(backups),
        "policy": {
            "automatic_reset": False,
            "reset_requires_backup": True,
            "default_retention": 5,
            "restore": "앱을 종료한 뒤 검증된 backup을 janus.sqlite3로 교체하고 재시작",
        },
    }


@app.post("/maintenance/backups", status_code=201)
def create_backup(body: dict | None = None):
    try:
        retain = int((body or {}).get("retain", 5))
        database = get_domain_store().path
        backups = Path(os.environ.get("JANUS_BACKUPS_DIR", str(BACKUPS_DIR))).expanduser()
        with _BACKUP_LOCK:
            return recovery.create_database_backup(
                database, backups, retain=retain,
            )
    except (OSError, sqlite3.Error, ValueError) as error:
        payload = recovery.classify_failure(error)
        raise D.Conflict(f"database backup 실패 [{payload['kind']}]: {payload['detail']}") from error


@app.post("/maintenance/diagnostics", status_code=201)
def create_diagnostics():
    database = get_domain_store().path
    log_dir = Path(
        os.environ.get("JANUS_LOG_DIR", str(Path.home() / ".janus" / "logs"))
    ).expanduser()
    output_dir = Path(
        os.environ.get("JANUS_DIAGNOSTICS_DIR", str(DIAGNOSTICS_DIR))
    ).expanduser()
    try:
        return diagnostics.create_diagnostic_bundle(
            database=database, log_dir=log_dir, output_dir=output_dir,
        )
    except (OSError, sqlite3.Error) as error:
        payload = recovery.classify_failure(error)
        raise D.Conflict(
            f"diagnostics 생성 실패 [{payload['kind']}]: {payload['detail']}"
        ) from error


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


def main():
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ.get("JANUS_PORT", "8765")),
        log_level="info",
        # SIGTERM 뒤 스트리밍 연결을 기다리다 supervisor의 5초 grace를 넘기면
        # SIGKILL을 맞아 SQLite 쓰기가 끊긴다. 남은 연결은 강제로 닫고 정상 종료한다.
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )

# ── 도메인 라우터 장착 ────────────────────────────────────────────
# 하단에서 import해야 위의 상태·헬퍼 정의가 먼저 초기화된다 (순환 참조 회피).
from .routers import (  # noqa: E402
    development,
    evaluations,
    operations,
    profiles,
    projects,
    reviews,
    sessions,
    shipping,
    skills,
    tasks,
    terminals,
    verifications,
    workspaces,
)

app.include_router(projects.router)
app.include_router(tasks.router)
app.include_router(workspaces.router)
app.include_router(verifications.router)
app.include_router(reviews.router)
app.include_router(shipping.router)
app.include_router(evaluations.router)
app.include_router(operations.router)
app.include_router(terminals.router)
app.include_router(development.router)
app.include_router(skills.router)
app.include_router(profiles.router)
app.include_router(sessions.router)


if __name__ == "__main__":
    main()
