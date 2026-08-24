"""Janus 서버 — 에이전트 CRUD + 오케스트레이터 대화 스트리밍.

렌더러는 이 서버하고만 통신한다. Electron main은 창과 다이얼로그만 담당한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shlex
import signal
import sqlite3
import subprocess
import sys
import tempfile
import threading
import urllib.error
import uuid
from contextlib import asynccontextmanager, suppress
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import Response

from . import adaptive
from . import diagnostics
from . import event_bus as event_bus_mod
from . import runtime
from . import scheduler as scheduler_mod
from . import telemetry as telemetry_mod
from . import terminal_service as terminal_mod
from . import domain as D
from . import evaluation
from . import github_service as github_mod
from . import recovery
from . import skills as skill_mod
from . import spec as S
from . import tools as T
from . import verification
from . import workspace_service as WS
from .workspace import WorkspaceContext
from .version import __version__

AGENTS_DIR = Path(__file__).parent / "agents"
RUNS_DIR = Path(__file__).parent / "runs"    # 실행 기록. 앱을 닫아도 남는다.
STATE_FILE = Path(
    os.environ.get("JANUS_STATE_FILE", str(Path.home() / ".janus" / "state.json"))
).expanduser()
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
_STATE_LOCK = threading.Lock()
_BACKUP_LOCK = threading.Lock()
_LEGACY_WORKSPACE_LOCK = threading.Lock()
_LEGACY_WORKSPACE_ROOT = (Path(__file__).parent / "workspace").resolve()
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


def _read_state() -> dict:
    if not STATE_FILE.is_file():
        return {}
    try:
        value = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        print(f"[janus] state read failed: {type(e).__name__}: {e}", file=sys.stderr)
        return {}
    return value if isinstance(value, dict) else {}


def _persist_workspace(path: Path) -> None:
    """워크스페이스를 원자적으로 저장한다.

    동시 인스턴스가 써도 완성된 JSON 하나가 replace되므로 반쪽 파일이
    남지 않는다. 마지막 성공 쓰기가 승리한다.
    """
    with _STATE_LOCK:
        state = _read_state()
        state["workspace"] = str(path)
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            prefix=f".{STATE_FILE.name}.", suffix=".tmp", dir=STATE_FILE.parent
        )
        tmp = Path(tmp_name)
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, STATE_FILE)
        finally:
            tmp.unlink(missing_ok=True)


def _set_legacy_workspace(path: str | Path) -> Path:
    """Compatibility UI selection; tool execution still receives a value copy."""
    global _LEGACY_WORKSPACE_ROOT
    root = Path(path).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"디렉토리가 아닙니다: {path}")
    with _LEGACY_WORKSPACE_LOCK:
        _LEGACY_WORKSPACE_ROOT = root
    return root


def _get_legacy_workspace() -> Path:
    with _LEGACY_WORKSPACE_LOCK:
        root = _LEGACY_WORKSPACE_ROOT
    root.mkdir(parents=True, exist_ok=True)
    return root


def _legacy_workspace_context(task_id: str) -> WorkspaceContext:
    root = _get_legacy_workspace()
    workspace_id = "workspace_legacy_" + uuid.uuid5(
        uuid.NAMESPACE_URL, str(root)
    ).hex[:16]
    return WorkspaceContext(root=root, task_id=task_id, workspace_id=workspace_id)


def _restore_workspace() -> bool:
    saved = _read_state().get("workspace")
    if not isinstance(saved, str) or not saved:
        return False
    try:
        _set_legacy_workspace(saved)
    except ValueError as e:
        # 삭제된 폴더나 접근 불가 경로는 기본 workspace로 안전하게 돌아간다.
        print(f"[janus] saved workspace ignored: {e}", file=sys.stderr)
        return False
    return True


_restore_workspace()


def get_domain_store() -> D.DomainStore:
    """DB 경로는 호출 시점에 해석한다 — 테스트와 앱 data dir 전환을 격리한다."""
    global _DOMAIN_STORE, _DOMAIN_STORE_PATH, _DOMAIN_RECOVERED_PATH
    path = Path(os.environ.get("JANUS_DB_FILE", str(DOMAIN_DB_FILE))).expanduser().resolve()
    with _DOMAIN_LOCK:
        if _DOMAIN_STORE is None or _DOMAIN_STORE_PATH != path:
            _DOMAIN_STORE = D.DomainStore(path)
            _DOMAIN_STORE_PATH = path
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


def _save_run(
    agent_id: str, run_id: str, inputs: dict, spans: list,
    cancelled: bool, telemetry: dict | None = None,
    owner_id: str | None = None,
) -> None:
    """실행 하나를 단일 JSON 파일로 남긴다. run_id가 고정이라 대화가 이어질 때마다
    같은 파일을 덮어쓴다 — 한 대화 = 한 기록."""
    if not spans:
        return
    owner_id = owner_id or agent_id
    d = RUNS_DIR / owner_id
    d.mkdir(parents=True, exist_ok=True)
    total = max((s.get("started_ms", 0) + (s.get("duration_ms") or 0)) for s in spans)
    first = spans[0].get("output") or {}
    summary = next((str(v) for v in first.values() if v), "")
    (d / f"{run_id}.json").write_text(json.dumps({
        "id": run_id, "agent_id": agent_id, "owner_id": owner_id,
        "at": run_id.rsplit("-", 1)[0],
        "inputs": inputs, "cancelled": cancelled,
        "duration_ms": total, "node_count": len(spans),
        "summary": summary[:120], "spans": spans,
        "telemetry": telemetry,
    }, ensure_ascii=False), encoding="utf-8")

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


def _path(agent_id: str) -> Path:
    # 경로 조작 차단 — id는 파일명 한 조각이어야 한다
    if "/" in agent_id or "\\" in agent_id or agent_id.startswith("."):
        raise HTTPException(400, f"잘못된 agent id: {agent_id!r}")
    return AGENTS_DIR / f"{agent_id}.yaml"


def _run_owner_id(agent_id: str) -> str:
    path = _path(agent_id)
    if not path.is_file():
        return agent_id
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return agent_id
    owner = raw.get("_instance_id") if isinstance(raw, dict) else None
    if isinstance(owner, str) and re.fullmatch(r"agent_instance_[a-f0-9]{24}", owner):
        return owner
    return agent_id


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
    return {
        "ok": True, "version": app.version, "mlx": mlx,
        "schema_version": get_domain_store().schema_version(),
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


def _project_json(project: dict) -> dict:
    value = dict(project)
    raw = value.pop("verification_commands_json", "[]")
    value["verification_commands"] = json.loads(raw)
    return value


@app.get("/projects")
def list_projects(include_archived: bool = False):
    return [
        _project_json(item)
        for item in get_domain_store().list_projects(include_archived=include_archived)
    ]


@app.post("/projects")
def create_project(body: dict):
    return _project_json(get_domain_store().create_project(
        name=str(body.get("name") or ""), repo_path=str(body.get("repo_path") or "")
    ))


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    return _project_json(get_domain_store().get_project(project_id))


def _project_workspace_context(project_id: str) -> WorkspaceContext:
    project = get_domain_store().get_project(project_id)
    try:
        return WorkspaceContext(
            root=Path(project["repo_path"]),
            task_id=f"project_{project_id}",
            workspace_id=f"project_{project_id}",
        )
    except ValueError as error:
        raise HTTPException(404, str(error)) from error


@app.get("/projects/{project_id}/tree")
def project_tree(project_id: str, path: str = ""):
    """선택한 프로젝트 디렉토리의 한 계층을 IDE 탐색기용으로 반환한다."""
    context = _project_workspace_context(project_id)
    try:
        root = T._resolve(path or ".", context)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not root.is_dir():
        raise HTTPException(404, f"디렉토리가 아님: {path}")
    entries = []
    for item in sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if item.name in _IGNORE or item.name.startswith("."):
            continue
        entries.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
        if len(entries) >= 500:
            break
    return {"path": path, "entries": entries}


@app.get("/projects/{project_id}/file")
def project_file(project_id: str, path: str):
    context = _project_workspace_context(project_id)
    try:
        item = T._resolve(path, context)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not item.is_file():
        raise HTTPException(404, f"파일 없음: {path}")
    if item.stat().st_size > 1_000_000:
        return {"path": path, "content": None, "error": "1MB 초과 — 뷰어로 열기엔 너무 큼"}
    raw = item.read_bytes()
    if b"\x00" in raw[:8192]:
        return {"path": path, "content": None, "error": "바이너리 파일"}
    return {
        "path": path,
        "content": raw.decode("utf-8", errors="replace"),
        "error": None,
    }


@app.post("/projects/{project_id}/agent-profile/promote")
def promote_project_agent_profile(project_id: str, body: dict):
    store = get_domain_store()
    comparison = _evaluation_comparison_json(
        store.get_evaluation_comparison(str(body.get("comparison_id") or ""))
    )
    project = store.promote_project_agent_profile(
        project_id, comparison_id=comparison["id"]
    )
    profile_id = project["default_agent_profile_id"]
    return {
        "project": _project_json(project),
        "agent_profile": _agent_profile_json(store.get_agent_profile(profile_id)),
        "comparison_id": comparison["id"],
        "verdict": comparison["result"].get("verdict"),
    }


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


@app.put("/projects/{project_id}/verification-commands")
def set_project_verification_commands(project_id: str, body: dict):
    commands = _verification_commands(body.get("commands"))
    return _project_json(
        get_domain_store().set_project_verification_commands(project_id, commands)
    )


@app.delete("/projects/{project_id}")
def archive_project(project_id: str):
    return get_domain_store().archive_project(project_id)


@app.get("/projects/{project_id}/tasks")
def list_project_tasks(project_id: str, include_archived: bool = False):
    get_domain_store().get_project(project_id)
    return get_domain_store().list_tasks(project_id, include_archived=include_archived)


@app.post("/projects/{project_id}/tasks")
def create_task(project_id: str, body: dict):
    return get_domain_store().create_task(
        project_id=project_id,
        title=str(body.get("title") or ""),
        objective=str(body.get("objective") or ""),
        acceptance_command=str(body.get("acceptance_command") or ""),
        base_ref=str(body.get("base_ref") or "main"),
    )


def _delegation_title(objective: str) -> str:
    first_line = " ".join(objective.strip().splitlines()[0].split())
    return first_line if len(first_line) <= 72 else f"{first_line[:71]}…"


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


def _delegation_acceptance(project: dict, repo: Path) -> str:
    configured = json.loads(project.get("verification_commands_json") or "[]")
    for preferred in ("acceptance", "test", "typecheck", "lint", "custom"):
        match = next((item for item in configured if item.get("kind") == preferred), None)
        if match and str(match.get("command") or "").strip():
            return str(match["command"]).strip()

    package = repo / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if scripts.get("test"):
            if (repo / "pnpm-lock.yaml").is_file():
                return "pnpm test"
            if (repo / "yarn.lock").is_file():
                return "yarn test"
            return "npm test"
        if scripts.get("typecheck"):
            manager = "pnpm" if (repo / "pnpm-lock.yaml").is_file() else "npm run"
            return f"{manager} typecheck"
    if (repo / "pyproject.toml").is_file():
        return "uv run pytest -q" if (repo / "uv.lock").is_file() else "python -m pytest -q"
    if (repo / "Cargo.toml").is_file():
        return "cargo test"
    if (repo / "go.mod").is_file():
        return "go test ./..."
    return "git diff --check"


@app.post("/projects/{project_id}/delegations")
def delegate_project_work(project_id: str, body: dict):
    objective = str(body.get("objective") or "").strip()
    if not objective:
        raise D.Conflict("위임할 목표를 입력하세요")
    if len(objective) > 12_000:
        raise D.Conflict("위임 목표는 12,000자 이하여야 합니다")
    store = get_domain_store()
    project = store.get_project(project_id)
    repo = Path(project["repo_path"]).resolve()
    return store.create_task(
        project_id=project_id,
        title=_delegation_title(objective),
        objective=objective,
        acceptance_command=_delegation_acceptance(project, repo),
        base_ref=_delegation_base_ref(repo),
    )


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = get_domain_store().get_task(task_id)
    task["workspace"] = get_domain_store().get_task_workspace(task_id)
    task["dispatches"] = [
        _dispatch_json(item) for item in get_domain_store().list_dispatches(task_id)
    ]
    return task


@app.patch("/tasks/{task_id}")
def update_task(task_id: str, body: dict):
    fields = {
        key: str(body[key])
        for key in ("title", "objective", "acceptance_command", "base_ref")
        if key in body
    }
    return get_domain_store().update_task(task_id, **fields)


@app.post("/tasks/{task_id}/transition")
def transition_task(task_id: str, body: dict):
    return get_domain_store().transition_task(
        task_id, str(body.get("status") or ""),
        expected=str(body["expected"]) if body.get("expected") is not None else None,
    )


@app.post("/tasks/{task_id}/mockup/approve")
def approve_task_mockup(task_id: str):
    return get_domain_store().approve_task_mockup(task_id)


@app.delete("/tasks/{task_id}")
def archive_task(task_id: str):
    """Task를 목록에서 감춘다. 대화 기록과 Git 브랜치는 남는다.

    감춘 Task의 worktree는 UI로 다시 닿을 수 없으므로 같이 놓아준다.
    브랜치는 보존되므로 작업 내용이 사라지지는 않는다.
    """
    store = get_domain_store()
    workspace = store.get_task_workspace(task_id)
    if workspace is not None and workspace["state"] != "archived" and workspace["owned"]:
        latest = store.latest_dispatch(task_id)
        if latest is not None and latest["status"] in {"queued", "running", "needs_you"}:
            raise D.Conflict("활성 AgentSession을 먼저 중지해야 작업을 삭제할 수 있습니다")
        if _workspace_job_active(workspace["id"]):
            raise D.Conflict("작업 공간을 준비하는 중에는 삭제할 수 없습니다")
        try:
            if workspace.get("root_path"):
                get_workspace_service().archive(
                    repo_path=workspace["repo_path"], root_path=workspace["root_path"]
                )
            store.transition_workspace(workspace["id"], "archived", progress="archived")
        except WS.UnsafeWorkspace:
            # 커밋되지 않은 변경은 목록 정리보다 무겁다 — worktree는 두고 Task만 감춘다.
            pass
    return store.archive_task(task_id)


def _workspace_job_active(workspace_id: str) -> bool:
    with _WORKSPACE_JOBS_LOCK:
        thread = _WORKSPACE_JOBS.get(workspace_id)
        return bool(thread and thread.is_alive())


def _run_workspace_preparation(workspace_id: str) -> None:
    store = get_domain_store()
    try:
        workspace = store.get_workspace(workspace_id)
        task = store.get_task(workspace["task_id"])

        def progress(stage: str, details: dict) -> None:
            store.update_workspace_preparation(
                workspace_id,
                progress=stage,
                root_path=details.get("root_path"),
                branch_name=details.get("branch_name"),
            )
            _publish_change(
                "workspace", "progress", workspace_id=workspace_id,
                task_id=task["id"], progress=stage,
            )

        prepared = get_workspace_service().prepare(
            workspace_id=workspace_id,
            task_id=task["id"],
            title=task["title"],
            repo_path=workspace["repo_path"],
            base_ref=workspace["base_ref"],
            existing_root=workspace.get("root_path"),
            existing_branch=workspace.get("branch_name"),
            progress=progress,
        )
        store.transition_workspace(
            workspace_id, "ready",
            root_path=prepared["root_path"],
            branch_name=prepared["branch_name"],
            progress="ready",
        )
        current_task = store.get_task(task["id"])
        if current_task["status"] == "preparing":
            store.transition_task(task["id"], "todo", expected="preparing")
        _publish_change(
            "workspace", "ready", workspace_id=workspace_id,
            task_id=task["id"], state="ready", progress="ready",
        )
    except Exception as error:
        message = f"{type(error).__name__}: {error}"
        try:
            current = store.get_workspace(workspace_id)
            if current["state"] == "preparing":
                store.transition_workspace(
                    workspace_id, "failed", error=message, progress="failed"
                )
            task = store.get_task(current["task_id"])
            if task["status"] == "preparing":
                store.transition_task(task["id"], "failed", expected="preparing")
            _publish_change(
                "workspace", "failed", workspace_id=workspace_id,
                task_id=task["id"], state="failed", error=message,
            )
        except D.DomainError:
            pass
    finally:
        with _WORKSPACE_JOBS_LOCK:
            if _WORKSPACE_JOBS.get(workspace_id) is threading.current_thread():
                _WORKSPACE_JOBS.pop(workspace_id, None)


def _start_workspace_preparation(workspace_id: str) -> None:
    with _WORKSPACE_JOBS_LOCK:
        existing = _WORKSPACE_JOBS.get(workspace_id)
        if existing is not None and existing.is_alive():
            raise D.Conflict(f"Workspace 준비가 이미 진행 중입니다: {workspace_id}")
        thread = threading.Thread(
            target=_run_workspace_preparation,
            args=(workspace_id,),
            name=f"janus-workspace-{workspace_id}",
            daemon=True,
        )
        _WORKSPACE_JOBS[workspace_id] = thread
        thread.start()


@app.get("/tasks/{task_id}/workspace")
def get_task_workspace(task_id: str):
    get_domain_store().get_task(task_id)
    workspace = get_domain_store().get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    return {**workspace, "job_active": _workspace_job_active(workspace["id"])}


@app.post("/tasks/{task_id}/workspace/prepare", status_code=202)
def prepare_task_workspace(task_id: str):
    store = get_domain_store()
    task = store.get_task(task_id)
    if store.get_task_workspace(task_id) is not None:
        raise D.Conflict("Workspace가 이미 있습니다. failed면 retry를 사용하세요.")
    project = store.get_project(task["project_id"])
    workspace = store.create_workspace(
        task_id=task_id, repo_path=project["repo_path"], base_ref=task["base_ref"]
    )
    store.transition_task(task_id, "preparing", expected="todo")
    _start_workspace_preparation(workspace["id"])
    return {
        **store.get_workspace(workspace["id"]),
        "job_active": _workspace_job_active(workspace["id"]),
    }


@app.post("/tasks/{task_id}/workspace/retry", status_code=202)
def retry_task_workspace(task_id: str):
    store = get_domain_store()
    task = store.get_task(task_id)
    workspace = store.get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    if _workspace_job_active(workspace["id"]):
        raise D.Conflict("Workspace 준비가 이미 진행 중입니다")
    if workspace["state"] not in {"failed", "archived"}:
        raise D.Conflict(f"retry할 수 없는 Workspace 상태: {workspace['state']}")
    if task["status"] not in {"failed", "todo"}:
        raise D.Conflict(f"retry할 수 없는 Task 상태: {task['status']}")
    store.transition_workspace(
        workspace["id"], "preparing", progress="queued", error=None,
        base_ref=task["base_ref"],
    )
    store.transition_task(task_id, "preparing", expected=task["status"])
    _start_workspace_preparation(workspace["id"])
    return {
        **store.get_workspace(workspace["id"]),
        "job_active": _workspace_job_active(workspace["id"]),
    }


@app.get("/tasks/{task_id}/workspace/status")
def inspect_task_workspace(task_id: str):
    workspace = get_domain_store().get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    if not workspace.get("root_path") or workspace["state"] != "ready":
        return {**workspace, "job_active": _workspace_job_active(workspace["id"])}
    status = get_workspace_service().inspect(
        workspace["repo_path"], workspace["root_path"]
    )
    return {
        **workspace, "job_active": _workspace_job_active(workspace["id"]),
        "git_status": status,
    }


@app.get("/tasks/{task_id}/changeset")
def get_task_changeset(task_id: str):
    store = get_domain_store()
    task = store.get_task(task_id)
    workspace = store.get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    if workspace["state"] != "ready" or not workspace.get("root_path"):
        raise D.Conflict("ready Workspace가 있어야 ChangeSet을 읽을 수 있습니다")
    if not workspace["owned"]:
        raise D.Conflict("Janus가 소유한 Workspace만 ChangeSet을 읽을 수 있습니다")
    return get_workspace_service().changeset(
        repo_path=workspace["repo_path"],
        root_path=workspace["root_path"],
        base_ref=task["base_ref"],
    )


def _verification_workspace(task_id: str) -> tuple[dict, dict, dict]:
    store = get_domain_store()
    task = store.get_task(task_id)
    workspace = store.get_task_workspace(task_id)
    if workspace is None or workspace["state"] != "ready" or not workspace.get("root_path"):
        raise D.Conflict("ready Workspace가 있어야 verification을 실행할 수 있습니다")
    if not workspace["owned"]:
        raise D.Conflict("Janus가 소유한 Workspace만 verification할 수 있습니다")
    changes = get_workspace_service().changeset(
        repo_path=workspace["repo_path"], root_path=workspace["root_path"],
        base_ref=task["base_ref"],
    )
    return task, workspace, changes


def _run_verification_job(run_id: str) -> None:
    store = get_domain_store()
    try:
        item = store.start_verification_run(run_id)
        _publish_change(
            "verification", "running", run_id=run_id, task_id=item["task_id"],
            status="running",
        )
        _task, workspace, _changes = _verification_workspace(item["task_id"])
        context = WorkspaceContext(
            root=Path(workspace["root_path"]), task_id=item["task_id"],
            workspace_id=workspace["id"], dispatch_id=item.get("dispatch_id"),
        )
        result = verification.run(
            item["command"], context, scheduler=scheduler_mod.default_scheduler()
        )
        finished = store.finish_verification_run(run_id, result)
        _publish_change(
            "verification", "finished", run_id=run_id,
            task_id=finished["task_id"], status=finished["status"],
        )
    except Exception as error:
        try:
            current = store.get_verification_run(run_id)
            if current["status"] == "running":
                store.finish_verification_run(run_id, {
                    "exit_code": None, "stdout": "", "stderr": "",
                    "duration_ms": 0.0, "error": f"{type(error).__name__}: {error}",
                })
                _publish_change(
                    "verification", "finished", run_id=run_id,
                    task_id=current["task_id"], status="failed",
                )
        except D.DomainError:
            pass
    finally:
        with _VERIFICATION_JOBS_LOCK:
            if _VERIFICATION_JOBS.get(run_id) is threading.current_thread():
                _VERIFICATION_JOBS.pop(run_id, None)


def _start_verification_job(run_id: str) -> None:
    with _VERIFICATION_JOBS_LOCK:
        thread = threading.Thread(
            target=_run_verification_job, args=(run_id,),
            name=f"janus-verification-{run_id}", daemon=True,
        )
        _VERIFICATION_JOBS[run_id] = thread
        thread.start()


def _create_verification_runs(task_id: str, body: dict) -> list[dict]:
    store = get_domain_store()
    task, _workspace, changes = _verification_workspace(task_id)
    configured = body.get("commands")
    commands = (
        _verification_commands(configured)
        if configured is not None
        else [{"kind": "acceptance", "command": task["acceptance_command"]}] +
        _verification_commands(json.loads(
            store.get_project(task["project_id"])["verification_commands_json"]
        ))
    )
    trigger = str(body.get("trigger") or "manual")
    agent_claim = body.get("agent_claim")
    if trigger not in {"manual", "agent"}:
        raise D.Conflict("모르는 verification trigger입니다")
    if agent_claim is not None and agent_claim not in {"passed", "failed", "unknown"}:
        raise D.Conflict("모르는 agent_claim입니다")
    latest = store.latest_dispatch(task_id)
    runs = [
        store.create_verification_run(
            task_id=task_id, kind=item["kind"], command=item["command"],
            trigger=trigger, agent_claim=agent_claim,
            dispatch_id=latest["id"] if latest else None,
            head_commit=changes["head_commit"], revision=changes["revision"],
        )
        for item in commands
    ]
    for item in runs:
        _start_verification_job(item["id"])
    return runs


@app.get("/tasks/{task_id}/verifications")
def list_task_verifications(task_id: str):
    get_domain_store().get_task(task_id)
    return get_domain_store().list_verification_runs(task_id)


@app.post("/tasks/{task_id}/verifications", status_code=202)
def run_task_verifications(task_id: str, body: dict):
    return _create_verification_runs(task_id, body)


@app.post("/verifications/{run_id}/rerun", status_code=202)
def rerun_verification(run_id: str):
    previous = get_domain_store().get_verification_run(run_id)
    return _create_verification_runs(previous["task_id"], {
        "commands": [{"kind": previous["kind"], "command": previous["command"]}],
        "trigger": "manual",
    })[0]


def _review_snapshot(task_id: str) -> tuple[dict, dict, dict]:
    task, workspace, changes = _verification_workspace(task_id)
    return task, workspace, changes


@app.get("/tasks/{task_id}/review")
def get_task_review(task_id: str):
    task, _workspace, changes = _review_snapshot(task_id)
    store = get_domain_store()
    return {
        "task_status": task["status"], "revision": changes["revision"],
        "unmerged": changes["unmerged"],
        "comments": store.list_review_comments(task_id),
        "decisions": store.list_review_decisions(task_id),
    }


@app.post("/tasks/{task_id}/review/comments")
def create_task_review_comment(task_id: str, body: dict):
    _task, _workspace, changes = _review_snapshot(task_id)
    revision = str(body.get("revision") or "")
    layer = str(body.get("layer") or "")
    file_path = str(body.get("file_path") or "")
    if revision != changes["revision"]:
        raise D.Conflict("diff가 바뀌었습니다. refresh 후 comment하세요")
    if layer not in changes["sections"] or file_path not in {
        item["path"] for item in changes["sections"][layer]
    }:
        raise D.Conflict("현재 ChangeSet에 없는 파일입니다")
    old_line = body.get("old_line")
    new_line = body.get("new_line")
    if old_line is not None and int(old_line) < 1:
        raise D.Conflict("old_line은 1 이상이어야 합니다")
    if new_line is not None and int(new_line) < 1:
        raise D.Conflict("new_line은 1 이상이어야 합니다")
    return get_domain_store().create_review_comment(
        task_id=task_id, revision=revision, layer=layer, file_path=file_path,
        old_line=int(old_line) if old_line is not None else None,
        new_line=int(new_line) if new_line is not None else None,
        hunk_header=str(body.get("hunk_header") or "") or None,
        body=str(body.get("body") or ""),
    )


@app.patch("/review/comments/{comment_id}")
def resolve_task_review_comment(comment_id: str, body: dict):
    return get_domain_store().resolve_review_comment(
        comment_id, resolved=bool(body.get("resolved", True))
    )


@app.post("/tasks/{task_id}/review/decision")
def decide_task_review(task_id: str, body: dict):
    task, workspace, changes = _review_snapshot(task_id)
    store = get_domain_store()
    decision = str(body.get("decision") or "")
    if decision not in {"accept", "request_changes", "discard"}:
        raise D.Conflict("모르는 review decision입니다")
    if str(body.get("revision") or "") != changes["revision"]:
        raise D.Conflict("diff가 바뀌었습니다. refresh 후 다시 판정하세요")
    if changes["unmerged"]:
        raise D.Conflict("unmerged 변경은 accept/discard할 수 없습니다")
    comments = store.list_review_comments(task_id)
    unresolved = [item for item in comments if item["resolved_at"] is None]
    requested_ids = body.get("comment_ids")
    comment_ids = (
        [str(item) for item in requested_ids]
        if isinstance(requested_ids, list)
        else [item["id"] for item in unresolved]
    )

    if decision == "accept":
        if unresolved:
            raise D.Conflict("unresolved review comment를 먼저 해결하세요")
        current_runs = [
            item for item in store.list_verification_runs(task_id)
            if item["revision"] == changes["revision"]
        ]
        if not current_runs or any(item["status"] != "passed" for item in current_runs):
            raise D.Conflict("현재 revision의 Janus verification이 모두 pass해야 accept할 수 있습니다")
        if task["status"] != "review":
            task = store.transition_task(task_id, "review", expected=task["status"])
    elif decision == "request_changes":
        if not comment_ids:
            raise D.Conflict("일괄 수정 요청에는 comment가 하나 이상 필요합니다")
        if task["status"] != "working":
            task = store.transition_task(task_id, "working", expected=task["status"])
    else:
        if str(body.get("confirm_workspace_id") or "") != workspace["id"]:
            raise D.Conflict("정확한 confirm_workspace_id가 필요합니다")
        if str(body.get("confirm_discard") or "") != task_id:
            raise D.Conflict("confirm_discard에 Task ID를 정확히 입력하세요")
        get_workspace_service().discard_changes(
            repo_path=workspace["repo_path"], root_path=workspace["root_path"]
        )
        if task["status"] != "todo":
            task = store.transition_task(task_id, "todo", expected=task["status"])

    recorded = store.create_review_decision(
        task_id=task_id, revision=changes["revision"], decision=decision,
        comment_ids=comment_ids, message=str(body.get("message") or ""),
    )
    return {"decision": recorded, "task": task}


def _shipping_gate(task_id: str, revision: str) -> tuple[dict, dict, dict]:
    task, workspace, changes = _review_snapshot(task_id)
    if revision != changes["revision"]:
        raise D.Conflict("ChangeSet revision이 바뀌었습니다. review를 다시 완료하세요")
    if changes["unmerged"]:
        raise D.Conflict("unmerged 변경은 shipping할 수 없습니다")
    decisions = get_domain_store().list_review_decisions(task_id)
    latest = decisions[-1] if decisions else None
    if latest is None or latest["decision"] != "accept" or latest["revision"] != revision:
        raise D.Conflict("현재 revision에 대한 accept review가 필요합니다")
    return task, workspace, changes


@app.get("/tasks/{task_id}/shipments")
def list_task_shipments(task_id: str):
    get_domain_store().get_task(task_id)
    return get_domain_store().list_task_shipments(task_id)


def _committed_shipment(task_id: str, workspace: dict) -> tuple[dict, dict]:
    head = get_workspace_service().current_head(
        repo_path=workspace["repo_path"], root_path=workspace["root_path"]
    )
    commits = [
        item for item in get_domain_store().list_task_shipments(task_id)
        if item["action"] == "commit" and item["status"] == "completed"
    ]
    latest = commits[-1] if commits else None
    if latest is None or latest["commit_sha"] != head["commit_sha"]:
        raise D.Conflict("Janus가 기록한 현재 commit이 있어야 handoff/push할 수 있습니다")
    return latest, head


@app.post("/tasks/{task_id}/ship/commit")
def commit_task_changes(task_id: str, body: dict):
    _task, workspace, changes = _shipping_gate(
        task_id, str(body.get("revision") or "")
    )
    service = get_workspace_service()
    before = service.current_head(
        repo_path=workspace["repo_path"], root_path=workspace["root_path"]
    )
    try:
        result = service.commit_changes(
            repo_path=workspace["repo_path"], root_path=workspace["root_path"],
            message=str(body.get("message") or ""),
        )
    except WS.WorkspaceServiceError as error:
        get_domain_store().record_task_shipment(
            task_id=task_id, action="commit", commit_sha=before["commit_sha"],
            branch_name=str(before["branch_name"] or workspace["branch_name"]),
            status="failed", error=str(error),
        )
        raise
    shipment = get_domain_store().record_task_shipment(
        task_id=task_id, action="commit", commit_sha=result["commit_sha"],
        branch_name=result["branch_name"],
    )
    return {"result": result, "shipment": shipment}


@app.post("/tasks/{task_id}/ship/push")
def push_task_branch(task_id: str, body: dict):
    _task, workspace, _changes = _review_snapshot(task_id)
    _shipment, head = _committed_shipment(task_id, workspace)
    current_sha = head["commit_sha"]
    if str(body.get("confirm_commit_sha") or "") != current_sha:
        raise D.Conflict("정확한 confirm_commit_sha가 필요합니다")
    remote = str(body.get("remote") or "origin")
    try:
        result = get_workspace_service().push_branch(
            repo_path=workspace["repo_path"], root_path=workspace["root_path"],
            remote=remote,
        )
    except WS.WorkspaceServiceError as error:
        get_domain_store().record_task_shipment(
            task_id=task_id, action="push", commit_sha=current_sha,
            branch_name=str(head["branch_name"] or workspace["branch_name"]),
            remote=remote, status="failed", error=str(error),
        )
        raise
    shipment = get_domain_store().record_task_shipment(
        task_id=task_id, action="push", commit_sha=result["commit_sha"],
        branch_name=result["branch_name"], remote=result["remote"],
    )
    return {"result": result, "shipment": shipment}


def _pull_request_json(item: dict) -> dict:
    value = dict(item)
    value["draft"] = bool(value["draft"])
    for source, target in (
        ("checks_json", "checks"), ("runs_json", "runs"),
        ("failed_logs_json", "failed_logs"),
    ):
        value[target] = json.loads(value.pop(source))
    return value


def _pull_request_payload(task_id: str, item: dict | None) -> dict:
    store = get_domain_store()
    workspace = store.get_task_workspace(task_id)
    archive_recommended = False
    archive_reason = None
    if item is not None and item["state"] == "merged":
        if workspace is not None and workspace["state"] == "ready" and workspace["root_path"]:
            try:
                head = get_workspace_service().current_head(
                    repo_path=workspace["repo_path"], root_path=workspace["root_path"]
                )
                archive_recommended = not head["dirty"]
                archive_reason = (
                    "PR merged; clean Task workspace can be archived safely"
                    if archive_recommended else
                    "PR merged, but uncommitted workspace changes block safe archive"
                )
            except WS.WorkspaceServiceError as error:
                archive_reason = str(error)
        else:
            archive_reason = "PR merged; workspace is already archived or unavailable"
    return {
        "pull_request": _pull_request_json(item) if item is not None else None,
        "archive_recommended": archive_recommended,
        "archive_reason": archive_reason,
        "branch_preserved": True,
    }


@app.get("/tasks/{task_id}/pull-request")
def get_task_pull_request(task_id: str):
    store = get_domain_store()
    store.get_task(task_id)
    return _pull_request_payload(task_id, store.get_task_pull_request(task_id))


def _pushed_task_head(task_id: str) -> tuple[dict, dict, dict]:
    store = get_domain_store()
    task = store.get_task(task_id)
    workspace = store.get_task_workspace(task_id)
    if workspace is None or workspace["state"] != "ready" or not workspace["root_path"]:
        raise D.Conflict("ready Task workspace가 필요합니다")
    _commit, head = _committed_shipment(task_id, workspace)
    pushed = [
        item for item in store.list_task_shipments(task_id)
        if item["action"] == "push" and item["status"] == "completed"
        and item["commit_sha"] == head["commit_sha"]
        and item["branch_name"] == head["branch_name"]
    ]
    if not pushed:
        raise D.Conflict("현재 Task commit을 remote에 push한 기록이 필요합니다")
    return task, workspace, head


@app.post("/tasks/{task_id}/pull-request")
def create_task_pull_request(task_id: str, body: dict):
    store = get_domain_store()
    task, workspace, head = _pushed_task_head(task_id)
    existing = store.get_task_pull_request(task_id)
    if existing is not None and existing["state"] not in {"error", "closed"}:
        raise D.Conflict("Task에 이미 활성 PullRequest가 연결되어 있습니다")
    base = str(body.get("base") or task["base_ref"]).removeprefix("origin/")
    title = str(body.get("title") or task["title"]).strip()
    draft = bool(body.get("draft", False))
    store.record_task_pull_request(
        task_id=task_id, title=title, head_branch=str(head["branch_name"]),
        base_branch=base, state="creating", details={"draft": draft},
    )
    try:
        created = get_github_service().create_pull_request(
            root_path=workspace["root_path"], head=str(head["branch_name"]), base=base,
            title=title, body=str(body.get("body") or task["objective"]), draft=draft,
        )
        status = get_github_service().checks(
            root_path=workspace["root_path"], branch=str(head["branch_name"])
        )
        item = store.record_task_pull_request(
            task_id=task_id, title=created["title"],
            head_branch=created["head_branch"], base_branch=created["base_branch"],
            state=created["state"], details={**created, **status},
        )
    except github_mod.GitHubServiceError as error:
        store.record_task_pull_request(
            task_id=task_id, title=title, head_branch=str(head["branch_name"]),
            base_branch=base, state="error", details={"draft": draft}, error=str(error),
        )
        raise D.Conflict(str(error)) from error
    return _pull_request_payload(task_id, item)


@app.post("/tasks/{task_id}/pull-request/refresh")
def refresh_task_pull_request(task_id: str):
    store = get_domain_store()
    item = store.get_task_pull_request(task_id)
    if item is None or item["number"] is None:
        raise D.Conflict("연결된 PullRequest가 없습니다")
    workspace = store.get_task_workspace(task_id)
    if workspace is None or not workspace["root_path"]:
        raise D.Conflict("PullRequest를 갱신할 Task workspace가 없습니다")
    try:
        refreshed = get_github_service().refresh(
            root_path=workspace["root_path"], branch=item["head_branch"]
        )
        remote = refreshed["pull_request"]
        item = store.record_task_pull_request(
            task_id=task_id, title=remote["title"],
            head_branch=remote["head_branch"], base_branch=remote["base_branch"],
            state=remote["state"], details={**remote, **refreshed},
        )
    except github_mod.GitHubServiceError as error:
        current = _pull_request_json(item)
        store.record_task_pull_request(
            task_id=task_id, title=item["title"], head_branch=item["head_branch"],
            base_branch=item["base_branch"], state="error", details=current,
            error=str(error),
        )
        raise D.Conflict(str(error)) from error
    return _pull_request_payload(task_id, item)


@app.get("/tasks/{task_id}/ship/handoff")
def task_ship_handoff(task_id: str):
    _task, workspace, _changes = _review_snapshot(task_id)
    _shipment, current = _committed_shipment(task_id, workspace)
    head = current["commit_sha"]
    repo = shlex.quote(str(workspace["repo_path"]))
    branch = shlex.quote(str(workspace["branch_name"]))
    sha = shlex.quote(head)
    return {
        "executed": False,
        "commit_sha": head,
        "branch_name": workspace["branch_name"],
        "local_apply_command": f"git -C {repo} cherry-pick {sha}",
        "push_command": f"git -C {repo} push origin {branch}",
        "notice": "Janus did not modify the main checkout; run a handoff command explicitly.",
    }


def _evaluation_experiment_json(item: dict) -> dict:
    value = dict(item)
    for source, target in (
        ("profile_snapshot_json", "profile_snapshot"),
        ("config_json", "config"), ("conditions_json", "conditions"),
        ("report_json", "report"),
    ):
        raw = value.pop(source, None)
        value[target] = json.loads(raw) if raw else None
    return value


def _evaluation_comparison_json(item: dict) -> dict:
    value = dict(item)
    value["thresholds"] = json.loads(value.pop("thresholds_json"))
    value["result"] = json.loads(value.pop("result_json"))
    return value


def _evaluation_profile_snapshot(profile_id: str) -> dict:
    store = get_domain_store()
    profile = store.get_agent_profile(profile_id)
    model = store.get_model_profile(profile["model_profile_id"])
    if model["provider"] != "local":
        raise D.Conflict("P4 Evaluation Lab은 현재 local model profile만 실행합니다")
    return {
        "id": profile["id"], "name": profile["name"],
        "system_prompt": profile["system_prompt"],
        "tools": json.loads(profile["tools_json"]), "approval": profile["approval"],
        "worker_policy": profile["worker_policy"], "max_steps": profile["max_steps"],
        "budget": json.loads(profile["budget_json"]),
        "model_profile_id": model["id"], "model_key": model["model_key"],
        "quantization": model["quantization"],
        "model_config": json.loads(model["config_json"]),
    }


def _evaluation_run_config(body: dict) -> dict:
    manifest_path = Path(__file__).resolve().parents[1] / "tasksuite" / "v0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    available = {item["id"] for item in manifest["tasks"]}
    tasks = body.get("tasks") or sorted(available)
    if not isinstance(tasks, list) or not tasks or len(tasks) != len(set(tasks)):
        raise D.Conflict("tasks는 중복 없는 하나 이상의 배열이어야 합니다")
    tasks = [str(item) for item in tasks]
    unknown = sorted(set(tasks) - available)
    if unknown:
        raise D.Conflict(f"모르는 TaskSuite task: {unknown}")
    try:
        repeats = int(body.get("repeats", manifest["repeats"]))
        turn_timeout = float(body.get("turn_timeout_seconds", 180))
        startup_timeout = float(body.get("model_startup_timeout_seconds", 240))
    except (TypeError, ValueError) as error:
        raise D.Conflict("repeats/timeout은 숫자여야 합니다") from error
    if not 1 <= repeats <= 20:
        raise D.Conflict("repeats는 1~20 사이여야 합니다")
    if not 30 <= turn_timeout <= 900 or not 30 <= startup_timeout <= 900:
        raise D.Conflict("timeout은 30~900초 사이여야 합니다")
    return {
        "tasks": tasks, "repeats": repeats,
        "turn_timeout_seconds": turn_timeout,
        "model_startup_timeout_seconds": startup_timeout,
    }


def _evaluation_root() -> Path:
    return Path(
        os.environ.get(
            "JANUS_EVALUATIONS_DIR", str(Path.home() / ".janus" / "evaluations")
        )
    ).expanduser().resolve()


def _run_evaluation_job(experiment_id: str) -> None:
    store = get_domain_store()
    process: subprocess.Popen[str] | None = None
    output_dir = _evaluation_root() / experiment_id
    try:
        item = store.start_evaluation_experiment(experiment_id)
        _publish_change(
            "evaluation", "running", experiment_id=experiment_id, status="running",
        )
        with _EVALUATION_JOBS_LOCK:
            cancelled_before_start = experiment_id in _EVALUATION_CANCELLED
        if cancelled_before_start:
            store.finish_evaluation_experiment(
                experiment_id, status="cancelled", error="cancelled by user"
            )
            return
        profile = json.loads(item["profile_snapshot_json"])
        config = json.loads(item["config_json"])
        root = _evaluation_root()
        root.mkdir(parents=True, exist_ok=True)
        profile_path = root / f".{experiment_id}-profile.json"
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        project_dir = Path(__file__).resolve().parents[1]
        command = [
            sys.executable, str(project_dir / "scripts" / "run_tasksuite_v0.py"),
            "--label", item["label"], "--profile-json", str(profile_path),
            "--output-dir", str(output_dir), "--repeats", str(config["repeats"]),
            "--turn-timeout", str(config["turn_timeout_seconds"]),
            "--model-startup-timeout", str(config["model_startup_timeout_seconds"]),
            "--tasks", *config["tasks"],
        ]
        process = subprocess.Popen(
            command, cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        with _EVALUATION_JOBS_LOCK:
            _EVALUATION_PROCESSES[experiment_id] = process
        output, _ = process.communicate()
        (root / f"{experiment_id}.log").write_text(output[-200_000:], encoding="utf-8")
        result_path = output_dir / "result.json"
        report = (
            json.loads(result_path.read_text(encoding="utf-8"))
            if result_path.is_file() else None
        )
        with _EVALUATION_JOBS_LOCK:
            cancelled = experiment_id in _EVALUATION_CANCELLED
        if cancelled:
            store.finish_evaluation_experiment(
                experiment_id, status="cancelled", report=report,
                conditions=report.get("conditions") if report else None,
                result_path=str(result_path) if report else None,
                error="cancelled by user",
            )
        elif process.returncode == 0 and report is not None:
            validated = evaluation.validate_report(report)
            store.finish_evaluation_experiment(
                experiment_id, status="completed", report=validated,
                conditions=validated["conditions"], result_path=str(result_path),
            )
        else:
            store.finish_evaluation_experiment(
                experiment_id, status="failed", report=report,
                conditions=report.get("conditions") if report else None,
                result_path=str(result_path) if report else None,
                error=f"TaskSuite runner exit {process.returncode}",
            )
    except Exception as error:
        try:
            current = store.get_evaluation_experiment(experiment_id)
            if current["status"] in {"queued", "running"}:
                store.finish_evaluation_experiment(
                    experiment_id, status="failed",
                    error=f"{type(error).__name__}: {error}",
                )
        except D.DomainError:
            pass
    finally:
        try:
            final_status = store.get_evaluation_experiment(experiment_id)["status"]
        except D.DomainError:
            final_status = "unknown"
        _publish_change(
            "evaluation", "finished", experiment_id=experiment_id,
            status=final_status,
        )
        profile_path = _evaluation_root() / f".{experiment_id}-profile.json"
        profile_path.unlink(missing_ok=True)
        with _EVALUATION_JOBS_LOCK:
            _EVALUATION_PROCESSES.pop(experiment_id, None)
            _EVALUATION_CANCELLED.discard(experiment_id)
            if _EVALUATION_JOBS.get(experiment_id) is threading.current_thread():
                _EVALUATION_JOBS.pop(experiment_id, None)


def _start_evaluation_job(experiment_id: str) -> None:
    with _EVALUATION_JOBS_LOCK:
        thread = threading.Thread(
            target=_run_evaluation_job, args=(experiment_id,),
            name=f"janus-evaluation-{experiment_id}", daemon=True,
        )
        _EVALUATION_JOBS[experiment_id] = thread
        thread.start()


@app.get("/evaluations/experiments")
def list_evaluation_experiments():
    return [
        _evaluation_experiment_json(item)
        for item in get_domain_store().list_evaluation_experiments()
    ]


@app.get("/evaluations/experiments/{experiment_id}")
def get_evaluation_experiment(experiment_id: str):
    return _evaluation_experiment_json(
        get_domain_store().get_evaluation_experiment(experiment_id)
    )


@app.post("/evaluations/experiments/import")
def import_evaluation_experiment(body: dict):
    role = str(body.get("role") or "")
    if role not in {"baseline", "candidate"}:
        raise D.Conflict("Evaluation role은 baseline 또는 candidate여야 합니다")
    try:
        report = evaluation.validate_report(body.get("report"))
    except evaluation.EvaluationError as error:
        raise HTTPException(400, str(error)) from error
    item = get_domain_store().create_evaluation_experiment(
        role=role, label=str(report["label"]), source="import", status="completed",
        conditions=report["conditions"], report=report,
        profile_snapshot=report["conditions"].get("agent_profile") or {},
        config={"imported": True},
    )
    return _evaluation_experiment_json(item)


@app.post("/evaluations/experiments/run", status_code=202)
def run_evaluation_experiment(body: dict):
    role = str(body.get("role") or "")
    if role not in {"baseline", "candidate"}:
        raise D.Conflict("Evaluation role은 baseline 또는 candidate여야 합니다")
    label = str(body.get("label") or "").strip()
    if not label:
        raise D.Conflict("Evaluation label이 필요합니다")
    profile_id = str(body.get("agent_profile_id") or "")
    profile = _evaluation_profile_snapshot(profile_id)
    if profile["model_key"] != "qwen3.8-27b":
        raise D.Conflict("현재 TaskSuite model server는 qwen3.8-27b profile만 실행합니다")
    config = _evaluation_run_config(body)
    item = get_domain_store().create_evaluation_experiment(
        role=role, label=label, source="runner", status="queued",
        agent_profile_id=profile_id, profile_snapshot=profile, config=config,
        conditions={
            "model": profile["model_key"], "quantization": profile["quantization"],
            "agent_profile": profile,
        },
    )
    _start_evaluation_job(item["id"])
    return _evaluation_experiment_json(item)


@app.post("/evaluations/experiments/{experiment_id}/cancel")
def cancel_evaluation_experiment(experiment_id: str):
    item = get_domain_store().get_evaluation_experiment(experiment_id)
    if item["status"] not in {"queued", "running"}:
        raise D.Conflict(f"취소할 수 없는 Evaluation 상태: {item['status']}")
    with _EVALUATION_JOBS_LOCK:
        _EVALUATION_CANCELLED.add(experiment_id)
        process = _EVALUATION_PROCESSES.get(experiment_id)
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
    return {"id": experiment_id, "cancellation_requested": True}


@app.get("/evaluations/comparisons")
def list_evaluation_comparisons():
    return [
        _evaluation_comparison_json(item)
        for item in get_domain_store().list_evaluation_comparisons()
    ]


@app.post("/evaluations/comparisons")
def create_evaluation_comparison(body: dict):
    store = get_domain_store()
    baseline = store.get_evaluation_experiment(str(body.get("baseline_id") or ""))
    candidate = store.get_evaluation_experiment(str(body.get("candidate_id") or ""))
    if baseline["role"] != "baseline" or candidate["role"] != "candidate":
        raise D.Conflict("baseline/candidate role이 올바른 experiment를 선택하세요")
    if baseline["status"] != "completed" or candidate["status"] != "completed":
        raise D.Conflict("완료된 experiment만 비교할 수 있습니다")
    thresholds = body.get("thresholds") or {}
    try:
        result = evaluation.compare(
            json.loads(baseline["report_json"]), json.loads(candidate["report_json"]),
            thresholds,
        )
    except evaluation.EvaluationError as error:
        raise HTTPException(400, str(error)) from error
    comparison = store.create_evaluation_comparison(
        baseline_experiment_id=baseline["id"],
        candidate_experiment_id=candidate["id"],
        thresholds=result["thresholds"], result=result,
    )
    return _evaluation_comparison_json(comparison)


@app.get("/evaluations/comparisons/{comparison_id}/export")
def export_evaluation_comparison(comparison_id: str, format: str = "json"):
    item = _evaluation_comparison_json(
        get_domain_store().get_evaluation_comparison(comparison_id)
    )
    exporters = {
        "json": (evaluation.export_json, "application/json", "json"),
        "csv": (evaluation.export_csv, "text/csv; charset=utf-8", "csv"),
        "markdown": (evaluation.export_markdown, "text/markdown; charset=utf-8", "md"),
    }
    if format not in exporters:
        raise HTTPException(400, "format은 json/csv/markdown 중 하나여야 합니다")
    exporter, media_type, suffix = exporters[format]
    return Response(
        exporter(item["result"]), media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="evaluation-{comparison_id}.{suffix}"'
        },
    )


def _workspace_for_removal(task_id: str, body: dict) -> dict:
    store = get_domain_store()
    workspace = store.get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    if str(body.get("confirm_workspace_id") or "") != workspace["id"]:
        raise D.Conflict("정확한 confirm_workspace_id가 필요합니다")
    if not workspace["owned"]:
        raise D.Conflict("Janus가 소유하지 않은 Workspace는 제거할 수 없습니다")
    if _workspace_job_active(workspace["id"]):
        raise D.Conflict("준비 중인 Workspace는 제거할 수 없습니다")
    latest = store.latest_dispatch(task_id)
    if latest is not None and latest["status"] in {"queued", "running", "needs_you"}:
        raise D.Conflict("활성 AgentSession을 먼저 중지해야 Workspace를 제거할 수 있습니다")
    return workspace


@app.post("/tasks/{task_id}/workspace/archive")
def archive_task_workspace(task_id: str, body: dict):
    workspace = _workspace_for_removal(task_id, body)
    result = {"removed": False, "branch_preserved": True}
    if workspace.get("root_path"):
        result = get_workspace_service().archive(
            repo_path=workspace["repo_path"], root_path=workspace["root_path"]
        )
    updated = get_domain_store().transition_workspace(
        workspace["id"], "archived", progress="archived"
    )
    return {"workspace": updated, "result": result}


@app.delete("/tasks/{task_id}/workspace/force")
def force_remove_task_workspace(task_id: str, body: dict):
    workspace = _workspace_for_removal(task_id, body)
    result = {"removed": False, "branch_preserved": True}
    if workspace.get("root_path"):
        result = get_workspace_service().force_remove(
            repo_path=workspace["repo_path"], root_path=workspace["root_path"]
        )
    updated = get_domain_store().transition_workspace(
        workspace["id"], "archived", progress="force_removed"
    )
    return {"workspace": updated, "result": result}


@app.delete("/tasks/{task_id}/workspace/branch")
def delete_task_workspace_branch(task_id: str, body: dict):
    workspace = _workspace_for_removal(task_id, body)
    if workspace["state"] != "archived":
        raise D.Conflict("Workspace를 먼저 archive해야 branch를 삭제할 수 있습니다")
    branch = str(workspace.get("branch_name") or "")
    if not branch:
        return {"deleted": False, "branch_name": None}
    return get_workspace_service().delete_branch(
        repo_path=workspace["repo_path"], branch_name=branch
    )


def _agent_profile_json(profile: dict) -> dict:
    return {
        **profile,
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


def _budget_progress(dispatch: dict | None) -> dict:
    if dispatch is None:
        return {"tokens": 0.0, "steps": 0.0, "time": 0.0, "workers": 0.0, "peak": 0.0}
    budget = json.loads(dispatch["budget_json"])
    usage = json.loads(dispatch["usage_json"])
    values = {
        "tokens": (
            int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))
        ) / budget["dispatch"]["token_limit"],
        "steps": int(usage.get("steps", 0)) / budget["dispatch"]["step_limit"],
        "time": float(usage.get("active_time_ms", 0)) / budget["dispatch"]["time_limit_ms"],
        "workers": int(usage.get("workers_started", 0)) / budget["workers"]["total_limit"],
    }
    values = {key: round(max(0.0, value) * 100, 1) for key, value in values.items()}
    return {**values, "peak": max(values.values())}


def _operations_lane(task: dict, dispatch: dict | None) -> str:
    if task["status"] == "review":
        return "review"
    if task["status"] == "needs_you":
        return "needs_you"
    if task["status"] == "failed" or (dispatch and dispatch["status"] == "failed"):
        return "failed"
    if dispatch and dispatch["status"] in {"running", "needs_you"}:
        return "working" if dispatch["status"] == "running" else "needs_you"
    if task["status"] == "working" and dispatch and dispatch["status"] != "queued":
        return "working"
    return "queue"


def _operations_timeline(
    store: D.DomainStore, task_id: str, session: dict | None,
) -> list[dict]:
    timeline: list[dict] = []
    if session is not None:
        for event in store.list_session_events(session["id"])[-60:]:
            payload = event["payload"]
            kind = str(payload.get("kind") or payload.get("type") or event["kind"])
            if kind.startswith("model_generation"):
                category = "generation"
            elif kind.startswith("tool_"):
                category = "tool"
            elif kind.startswith("resource_queue") or kind == "resource_lease_acquired":
                category = "queue"
            elif kind.startswith("worker_") or kind.startswith("span_"):
                category = "worker"
            else:
                continue
            timeline.append({
                "category": category, "kind": kind, "at": event["created_at"],
                "status": payload.get("status"), "label": payload.get("name"),
            })
    for run in store.list_verification_runs(task_id)[:12]:
        timeline.append({
            "category": "verification", "kind": run["kind"],
            "at": run["started_at"] or run["created_at"], "status": run["status"],
            "label": run["command"],
        })
    return sorted(timeline, key=lambda item: item["at"] or "")[-24:]


@app.get("/operations/dashboard")
def operations_dashboard(project_id: str | None = None):
    store = get_domain_store()
    projects = (
        [store.get_project(project_id)] if project_id
        else store.list_projects()
    )
    project_names = {project["id"]: project["name"] for project in projects}
    tasks = [
        task for project in projects
        for task in store.list_tasks(project["id"])
    ]
    rows = []
    for task in tasks:
        dispatch = store.latest_dispatch(task["id"])
        sessions = store.list_sessions(task["id"])
        session = sessions[0] if sessions else None
        lane = _operations_lane(task, dispatch)
        rows.append({
            "id": task["id"], "project_id": task["project_id"],
            "project_name": project_names[task["project_id"]],
            "title": task["title"], "status": task["status"], "lane": lane,
            "updated_at": task["updated_at"],
            "dispatch": _dispatch_json(dispatch) if dispatch else None,
            "session": ({
                "id": session["id"], "status": session["status"],
                "updated_at": session["updated_at"],
            } if session else None),
            "budget_progress": _budget_progress(dispatch),
            "timeline": _operations_timeline(store, task["id"], session),
            "attention": lane in {"needs_you", "review", "failed"},
        })
    lane_order = {"needs_you": 0, "failed": 1, "review": 2, "working": 3, "queue": 4}
    rows.sort(key=lambda item: (lane_order[item["lane"]], item["updated_at"]), reverse=False)
    scheduler = scheduler_mod.default_scheduler().snapshot()
    lane_counts = {
        lane: sum(row["lane"] == lane for row in rows)
        for lane in ("queue", "working", "needs_you", "review", "failed")
    }
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "total": len(rows), "attention": sum(row["attention"] for row in rows),
            "lanes": lane_counts,
        },
        "scheduler": scheduler,
        "memory": {
            "janus_process_peak_rss_bytes": telemetry_mod.process_peak_rss_bytes(),
        },
        "tasks": rows,
    }


def _terminal_json(item: dict, *, after_offset: int | None = None) -> dict:
    value = dict(item)
    buffer = str(value.pop("buffer"))
    end = int(value["output_offset"])
    start = max(0, end - len(buffer))
    requested = start if after_offset is None else max(0, int(after_offset))
    reset = requested < start or requested > end
    if reset:
        requested = start
    value["output"] = buffer[max(0, requested - start):]
    value["output_start"] = requested
    value["buffer_start"] = start
    value["reset"] = reset
    return value


@app.get("/tasks/{task_id}/terminals")
def list_task_terminals(task_id: str):
    store = get_domain_store()
    store.get_task(task_id)
    return [_terminal_json(item) for item in store.list_task_terminals(task_id)]


@app.post("/tasks/{task_id}/terminals")
def create_task_terminal(task_id: str, body: dict):
    store = get_domain_store()
    workspace = store.get_task_workspace(task_id)
    if workspace is None or workspace["state"] != "ready" or not workspace["root_path"]:
        raise D.Conflict("ready Task workspace가 있어야 terminal을 열 수 있습니다")
    pane_id = str(body.get("pane_id") or "primary")
    if pane_id not in {"primary", "secondary"}:
        raise D.Conflict("terminal pane_id는 primary/secondary만 허용합니다")
    manager = get_terminal_manager()
    try:
        session = manager.create(
            task_id=task_id, pane_id=pane_id, cwd=workspace["root_path"]
        )
    except (OSError, terminal_mod.TerminalServiceError) as error:
        raise D.Conflict(f"terminal 시작 실패: {error}") from error
    snapshot = session.snapshot()
    item = store.start_task_terminal(
        terminal_id=session.id, task_id=task_id, pane_id=pane_id,
        cwd=str(snapshot["cwd"]), shell=str(snapshot["shell"]), pid=int(snapshot["pid"]),
    )
    if snapshot["buffer"]:
        item = store.append_task_terminal_output(
            session.id, text=snapshot["buffer"], output_offset=int(snapshot["offset"])
        )
    if snapshot["state"] != "running":
        item = store.finish_task_terminal(
            session.id, state="exited", exit_code=snapshot["exit_code"]
        )
    return _terminal_json(item)


def _owned_live_terminal(task_id: str, terminal_id: str) -> terminal_mod.TerminalSession:
    item = get_domain_store().get_task_terminal(terminal_id)
    if item["task_id"] != task_id:
        raise D.Conflict("terminal이 다른 Task에 속합니다")
    try:
        return get_terminal_manager().get(terminal_id)
    except terminal_mod.TerminalServiceError as error:
        raise D.Conflict(str(error)) from error


@app.get("/tasks/{task_id}/terminals/{terminal_id}")
def get_task_terminal(task_id: str, terminal_id: str, after_offset: int = 0):
    item = get_domain_store().get_task_terminal(terminal_id)
    if item["task_id"] != task_id:
        raise D.Conflict("terminal이 다른 Task에 속합니다")
    return _terminal_json(item, after_offset=after_offset)


@app.post("/tasks/{task_id}/terminals/{terminal_id}/input")
def input_task_terminal(task_id: str, terminal_id: str, body: dict):
    value = str(body.get("data") or "")
    if not value or len(value) > 65_536:
        raise D.Conflict("terminal input은 1~65536자여야 합니다")
    session = _owned_live_terminal(task_id, terminal_id)
    try:
        session.write(value)
    except terminal_mod.TerminalServiceError as error:
        raise D.Conflict(str(error)) from error
    return {"written": len(value), "terminal_id": terminal_id}


@app.post("/tasks/{task_id}/terminals/{terminal_id}/resize")
def resize_task_terminal(task_id: str, terminal_id: str, body: dict):
    session = _owned_live_terminal(task_id, terminal_id)
    try:
        session.resize(int(body.get("columns") or 80), int(body.get("rows") or 24))
    except (TypeError, ValueError, OSError) as error:
        raise D.Conflict(f"terminal resize 실패: {error}") from error
    return {"terminal_id": terminal_id, "resized": True}


@app.delete("/tasks/{task_id}/terminals/{terminal_id}")
def stop_task_terminal(task_id: str, terminal_id: str):
    session = _owned_live_terminal(task_id, terminal_id)
    session.stop()
    item = get_domain_store().finish_task_terminal(
        terminal_id, state="stopped", exit_code=session.process.poll()
    )
    return _terminal_json(item)


def _task_development_root(task_id: str) -> tuple[dict, Path]:
    workspace = get_domain_store().get_task_workspace(task_id)
    if workspace is None or workspace["state"] != "ready" or not workspace["root_path"]:
        raise D.Conflict("ready Task workspace가 필요합니다")
    root = Path(workspace["root_path"]).resolve()
    if not root.is_dir():
        raise D.Conflict("Task workspace 경로가 없습니다")
    return workspace, root


def _task_development_path(task_id: str, value: str) -> tuple[dict, Path, Path]:
    workspace, root = _task_development_root(task_id)
    relative = Path(str(value or "."))
    if relative.is_absolute():
        raise D.Conflict("Task file path는 상대경로여야 합니다")
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise D.Conflict("Task workspace 밖의 file path입니다")
    return workspace, root, target


@app.get("/tasks/{task_id}/development/files")
def list_task_development_files(task_id: str, path: str = "."):
    _workspace, root, target = _task_development_path(task_id, path)
    if not target.is_dir():
        raise D.NotFound(f"디렉토리가 없습니다: {path}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if child.name == ".git":
            continue
        relative = str(child.relative_to(root))
        entries.append({
            "name": child.name, "path": relative,
            "type": "directory" if child.is_dir() else "file",
            "size": child.stat().st_size if child.is_file() else None,
        })
        if len(entries) >= 500:
            break
    return {"path": "" if path == "." else path, "entries": entries, "truncated": len(entries) >= 500}


@app.get("/tasks/{task_id}/development/file")
def read_task_development_file(task_id: str, path: str):
    _workspace, root, target = _task_development_path(task_id, path)
    if not target.is_file():
        raise D.NotFound(f"파일이 없습니다: {path}")
    size = target.stat().st_size
    if size > 2_000_000:
        raise D.Conflict("editor는 2MB 이하 text file만 엽니다")
    raw = target.read_bytes()
    if b"\0" in raw[:8192]:
        raise D.Conflict("binary file은 editor에서 열 수 없습니다")
    return {
        "path": str(target.relative_to(root)), "content": raw.decode("utf-8", errors="replace"),
        "size": size, "mtime_ns": target.stat().st_mtime_ns,
    }


@app.put("/tasks/{task_id}/development/file")
def write_task_development_file(task_id: str, body: dict):
    path = str(body.get("path") or "")
    content = body.get("content")
    if not path or not isinstance(content, str):
        raise D.Conflict("path와 text content가 필요합니다")
    encoded = content.encode("utf-8")
    if len(encoded) > 2_000_000:
        raise D.Conflict("editor는 2MB 이하 text file만 저장합니다")
    _workspace, root, target = _task_development_path(task_id, path)
    expected_mtime = body.get("expected_mtime_ns")
    if expected_mtime is not None and target.exists() and target.stat().st_mtime_ns != int(expected_mtime):
        raise D.Conflict("파일이 외부에서 변경됐습니다. 다시 열고 수정하세요")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.janus-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
    except OSError as error:
        with suppress(OSError):
            temporary.unlink()
        raise D.Conflict(f"파일 저장 실패: {error}") from error
    return {
        "path": str(target.relative_to(root)), "saved": True,
        "size": len(encoded), "mtime_ns": target.stat().st_mtime_ns,
    }


@app.get("/tasks/{task_id}/development/search")
def search_task_development_files(task_id: str, q: str):
    _workspace, root = _task_development_root(task_id)
    query = str(q).strip()
    if not query or len(query) > 200:
        raise D.Conflict("검색어는 1~200자여야 합니다")
    try:
        completed = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--color=never", "--fixed-strings",
             "--glob", "!.git/**", "--max-count", "50", query, "."],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise D.Conflict(f"file search 실패: {error}") from error
    if completed.returncode not in {0, 1}:
        raise D.Conflict(completed.stderr.strip() or "file search 실패")
    matches = []
    for line in completed.stdout.splitlines()[:500]:
        file_path, separator, rest = line.removeprefix("./").partition(":")
        line_number, separator2, text_value = rest.partition(":")
        if separator and separator2:
            matches.append({
                "path": file_path, "line": int(line_number), "text": text_value[:1000],
            })
    return {"query": query, "matches": matches, "truncated": len(completed.stdout.splitlines()) > 500}


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


def _store_skill_artifact(artifact: dict) -> dict:
    return _skill_json(get_domain_store().import_skill_version(**artifact))


def _local_source_kind(path: Path, requested: object) -> str:
    kind = str(requested or "").strip().lower()
    if kind in {"codex", "claude", "local", "project"}:
        return kind
    lowered = {part.lower() for part in path.parts}
    if ".claude" in lowered:
        return "claude"
    if ".codex" in lowered or ".agents" in lowered:
        return "codex"
    return "local"


def _local_skill_namespace(root: Path, kind: str, requested: object) -> str:
    if requested is not None and str(requested).strip():
        return str(requested).strip()
    generic = {"skills", ".skills", ".codex", ".claude", ".agents"}
    label = root.name if root.name.lower() not in generic else root.parent.name
    slug = re.sub(r"[^a-z0-9._-]+", "-", label.lower()).strip("-._") or "source"
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{slug[:70]}-{digest}"


@app.get("/skills")
def list_skills(include_archived: bool = False):
    return [
        _skill_summary(item)
        for item in get_domain_store().list_skills(include_archived=include_archived)
    ]


@app.get("/skills/{skill_id}")
def get_skill(skill_id: str):
    return _skill_json(get_domain_store().get_skill(skill_id))


@app.post("/skills/import/local")
def import_local_skills(body: dict):
    raw_path = str(body.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(400, "path가 필요합니다")
    root = Path(raw_path).expanduser().resolve()
    try:
        directories = skill_mod.discover_skill_directories(root)
        if not directories:
            raise skill_mod.SkillImportError("선택한 폴더에서 SKILL.md를 찾지 못했습니다")
        kind = _local_source_kind(root, body.get("source_kind"))
        namespace = _local_skill_namespace(root, kind, body.get("namespace"))
        imported = []
        for directory in directories:
            relative = directory.relative_to(root).as_posix()
            artifact = skill_mod.compile_skill_directory(
                directory,
                source_kind=kind,
                source_locator=str(root),
                source_subpath="" if relative == "." else relative,
                namespace=namespace,
            )
            imported.append(_store_skill_artifact(artifact))
        return {"source": str(root), "skills": imported}
    except skill_mod.SkillImportError as error:
        raise HTTPException(400, str(error)) from error


def _github_skill_artifacts(url: str) -> tuple[dict, list[dict]]:
    with tempfile.TemporaryDirectory(prefix="janus-skill-github-") as temporary:
        source = skill_mod.download_github_skills(url, temporary)
        raw_namespace = f"github-{source['owner']}-{source['repository']}".lower()
        namespace = (
            raw_namespace if len(raw_namespace) <= 100
            else f"{raw_namespace[:87]}-{hashlib.sha256(raw_namespace.encode()).hexdigest()[:12]}"
        )
        artifacts = []
        for directory in source["skill_directories"]:
            relative = directory.relative_to(source["root"]).as_posix()
            prefix = str(source.get("subpath") or "").strip("/")
            subpath = "/".join(item for item in (prefix, relative) if item and item != ".")
            artifact = skill_mod.compile_skill_directory(
                directory,
                source_kind="github",
                source_locator=source["canonical_url"],
                source_subpath=subpath,
                source_revision=source["revision"],
                namespace=namespace,
            )
            artifact["original"]["github"] = {
                "owner": source["owner"],
                "repository": source["repository"],
                "revision": source["revision"],
                "requested_ref": source["requested_ref"],
                "license": source["license"],
            }
            artifacts.append(artifact)
        return {
            "source": source["canonical_url"],
            "revision": source["revision"],
            "license": source["license"],
        }, artifacts


def _skill_artifact_preview(artifact: dict) -> dict:
    compiled = artifact["compiled"]
    return {
        key: artifact[key]
        for key in (
            "namespace", "name", "description", "source_kind", "source_locator",
            "source_subpath", "source_revision", "content_hash", "compatibility",
        )
    } | {
        "compiled": {
            key: compiled.get(key)
            for key in ("format", "activation", "execution", "capabilities")
        },
        "report": artifact["report"],
    }


@app.post("/skills/preview/github")
def preview_github_skills(body: dict):
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url이 필요합니다")
    if len(url) > 2_048:
        raise HTTPException(400, "GitHub URL이 너무 깁니다")
    try:
        metadata, artifacts = _github_skill_artifacts(url)
        return {**metadata, "url": url, "skills": [
            _skill_artifact_preview(artifact) for artifact in artifacts
        ]}
    except skill_mod.SkillImportError as error:
        raise HTTPException(400, str(error)) from error
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise HTTPException(502, f"GitHub 스킬을 가져올 수 없습니다: {error}") from error


@app.post("/skills/import/github")
def import_github_skills(body: dict):
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url이 필요합니다")
    if len(url) > 2_048:
        raise HTTPException(400, "GitHub URL이 너무 깁니다")
    selected = body.get("selected_subpaths")
    if selected is not None and (
        not isinstance(selected, list)
        or not selected
        or len(selected) > skill_mod.MAX_SKILL_FILES
        or not all(isinstance(item, str) for item in selected)
        or any(not item or len(item) > 1_024 for item in selected)
        or len(selected) != len(set(selected))
    ):
        raise HTTPException(400, "selected_subpaths는 중복 없는 하나 이상의 문자열 배열이어야 합니다")
    try:
        metadata, artifacts = _github_skill_artifacts(url)
        expected_revision = str(body.get("expected_revision") or "")
        if expected_revision and expected_revision != metadata["revision"]:
            raise D.Conflict(
                f"미리보기 이후 GitHub revision이 변경됐습니다: "
                f"expected={expected_revision}, actual={metadata['revision']}"
            )
        if selected is not None:
            requested = set(selected)
            available = {artifact["source_subpath"] for artifact in artifacts}
            unknown = sorted(requested - available)
            if unknown:
                raise D.Conflict(f"미리보기에 없던 스킬 경로입니다: {unknown}")
            artifacts = [
                artifact for artifact in artifacts if artifact["source_subpath"] in requested
            ]
        return {
            **metadata,
            "skills": [_store_skill_artifact(artifact) for artifact in artifacts],
        }
    except skill_mod.SkillImportError as error:
        raise HTTPException(400, str(error)) from error
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise HTTPException(502, f"GitHub 스킬을 가져올 수 없습니다: {error}") from error


@app.get("/profiles/agents/{profile_id}/skills")
def list_profile_skills(profile_id: str):
    return [
        _skill_summary(item)
        for item in get_domain_store().list_agent_profile_skills(profile_id)
    ]


@app.put("/profiles/agents/{profile_id}/skills/{skill_id}")
def set_profile_skill(profile_id: str, skill_id: str, body: dict):
    try:
        return _skill_summary(get_domain_store().set_agent_profile_skill(
            agent_profile_id=profile_id,
            skill_id=skill_id,
            skill_version_id=(str(body["skill_version_id"]) if body.get("skill_version_id") else None),
            activation_mode=str(body.get("activation_mode") or "off"),
            priority=int(body.get("priority") or 0),
        ))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error


@app.get("/profiles/models")
def list_model_profiles():
    return [_model_profile_json(item) for item in get_domain_store().list_model_profiles()]


@app.get("/profiles/agents")
def list_agent_profiles():
    return [_agent_profile_json(item) for item in get_domain_store().list_agent_profiles()]


@app.post("/profiles/agents")
def create_agent_profile(body: dict):
    try:
        profile = get_domain_store().create_agent_profile(
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
            system_prompt=str(body.get("system_prompt") or ""),
            tools=[str(item) for item in body.get("tools") or []],
            approval=str(body.get("approval") or "ask"),
            worker_policy=str(body.get("worker_policy") or "autonomous"),
            max_steps=int(body.get("max_steps") or 15),
            model_profile_id=str(body.get("model_profile_id") or "model_qwen38_27b_4bit"),
            budget=body.get("budget") if isinstance(body.get("budget"), dict) else None,
            context_policy=(
                body.get("context_policy") if isinstance(body.get("context_policy"), dict) else None
            ),
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    return _agent_profile_json(profile)


@app.put("/profiles/agents/{profile_id}")
def update_agent_profile(profile_id: str, body: dict):
    try:
        profile = get_domain_store().update_agent_profile(profile_id, **body)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    return _agent_profile_json(profile)


# ─────────────────────────── Task AgentSession runtime ───────────────────────────


def _task_runtime_spec(
    store: D.DomainStore, agent_profile_id: str, *, budget: dict | None = None,
    adaptive_decision: dict | None = None, profile_snapshot: dict | None = None,
) -> dict:
    profile = profile_snapshot or _agent_profile_json(store.get_agent_profile(agent_profile_id))
    if not profile:
        profile = _agent_profile_json(store.get_agent_profile(agent_profile_id))
    model = _model_profile_json(store.get_model_profile(profile["model_profile_id"]))
    decision = adaptive_decision or {}
    effective = decision.get("effective") or {}
    return {
        "name": profile["name"],
        "description": profile["description"],
        "model": model["model_key"],
        "system_prompt": profile["system_prompt"],
        "tools": profile["tools"],
        "approval": profile["approval"],
        "worker_policy": effective.get("worker_policy", profile["worker_policy"]),
        "worker_roles": effective.get("worker_roles", ["implementer", "researcher", "verifier"]),
        "worker_role_sequence": effective.get("worker_role_sequence", []),
        "allow_autonomous_workers": bool(effective.get("allow_autonomous_workers", False)),
        "max_steps": profile["max_steps"],
        "budget": budget or profile["budget"],
        "context_policy": profile["context_policy"],
    }


def _context_item(
    item_id: str, label: str, source: str, content: str, *, status: str = "included",
    detail: dict | None = None,
) -> dict:
    return {
        "id": item_id,
        "label": label,
        "source": source,
        "status": status,
        "content": content,
        "chars": len(content),
        "estimated_tokens": max(1, (len(content) + 3) // 4) if content else 0,
        "detail": detail or {},
    }


def _task_context_snapshot(
    spec: dict, dispatch: dict, workspace: dict, skills: list[dict],
    events: list[dict] | None = None, task: dict | None = None,
) -> dict:
    policy = D.normalize_context_policy(spec.get("context_policy"))
    items = [_context_item(
        "agent_prompt", "시스템 프롬프트", "AgentProfile",
        str(spec.get("system_prompt") or "You are an orchestrator."),
    )]
    preamble: list[str] = []
    objective = str(dispatch.get("objective_snapshot") or "")
    acceptance = str(dispatch.get("acceptance_snapshot") or "")
    workspace_root = str(workspace.get("root_path") or "")
    task_parts = (
        ("task_objective", "작업 목표", "Task", objective, "include_task_objective", "Task objective"),
        ("acceptance", "수용 검증", "Dispatch", acceptance, "include_acceptance", "Acceptance command"),
        ("workspace_root", "작업 공간", "Workspace", workspace_root, "include_workspace_root", "Workspace root"),
    )
    for item_id, label, source, content, policy_key, prompt_label in task_parts:
        included = bool(policy[policy_key]) and bool(content)
        items.append(_context_item(
            item_id, label, source, content,
            status="included" if included else "excluded",
            detail={"policy_key": policy_key},
        ))
        if included:
            preamble.append(f"{prompt_label}:\n{content}")

    workflow_stage = str((task or {}).get("workflow_stage") or "direct")
    workflow_prompt = ""
    if workflow_stage == "mockup":
        workflow_prompt = (
            "WORKFLOW STAGE: FRONTEND MOCKUP ONLY\n"
            "Create the smallest usable visual mockup for the requested feature using "
            "dummy or local fixture data. Reuse the existing UI patterns. Do not modify "
            "backend code, database schemas, APIs, model runtime, packaging, or infrastructure. "
            "Do not add speculative abstractions or dependencies. Once the mockup can be "
            "previewed, stop and ask the user to approve it before implementation."
        )
    elif workflow_stage == "implementation":
        workflow_prompt = (
            "WORKFLOW STAGE: APPROVED MOCKUP IMPLEMENTATION\n"
            "The user approved the frontend mockup. Treat its visible states and interactions "
            "as the scope. Derive only the minimal data/action contract it requires, connect the "
            "real implementation, verify it, and stop. Do not add features absent from the mockup."
        )
    if workflow_prompt:
        items.append(_context_item(
            "workflow_stage", "목업 우선 개발 단계", "Task", workflow_prompt,
            detail={"stage": workflow_stage},
        ))
        preamble.append(workflow_prompt)

    for skill in skills:
        description = str(skill.get("description") or "")
        qualified = f"{skill.get('namespace')}:{skill.get('name')}"
        items.append(_context_item(
            f"skill_catalog:{skill.get('skill_version_id')}",
            f"스킬 카탈로그 · {qualified}", "SkillVersion", description,
            detail={
                "activation_mode": skill.get("activation_mode"),
                "version": skill.get("version"),
                "loaded_at": skill.get("loaded_at"),
            },
        ))
        if skill.get("loaded_at"):
            compiled = skill.get("compiled") or {}
            instructions = str(compiled.get("instructions") or "")
            items.append(_context_item(
                f"skill_body:{skill.get('skill_version_id')}",
                f"로드된 스킬 · {qualified}", "load_skill", instructions,
                detail={"loaded_at": skill.get("loaded_at")},
            ))

    latest_window = None
    for event in reversed(events or []):
        payload = event.get("payload") or {}
        if event.get("kind") == "agent_event" and payload.get("kind") == "context_window":
            latest_window = payload
            break
    return {
        "policy": policy,
        "items": items,
        "estimated_static_tokens": sum(
            int(item["estimated_tokens"]) for item in items if item["status"] == "included"
        ),
        "latest_window": latest_window,
        "preamble": "\n\n".join(preamble),
    }


def _task_session_detail(store: D.DomainStore, session_id: str) -> dict:
    session = store.get_session(session_id)
    dispatch = _dispatch_json(store.get_dispatch(session["dispatch_id"]))
    workspace = store.get_workspace(dispatch["workspace_id"])
    skills = [_skill_json(item) for item in store.snapshot_session_skills(session_id)]
    # session_ready는 접속 handshake이지 대화/실행 기록이 아니다. 이전 빌드에서
    # 잘못 영속화된 기록도 세션 상세와 컨텍스트에서 제외한다.
    events = [
        event for event in store.list_session_events(session_id)
        if event["kind"] != "session_ready"
    ]
    spec = _task_runtime_spec(
        store, session["agent_profile_id"], budget=dispatch["budget"],
        adaptive_decision=dispatch["adaptive_decision"],
        profile_snapshot=dispatch["agent_profile_snapshot"],
    )
    task = store.get_task(session["task_id"])
    context = _task_context_snapshot(spec, dispatch, workspace, skills, events, task)
    context.pop("preamble", None)
    return {
        **session,
        "dispatch": dispatch,
        "workspace_id": workspace["id"],
        "workspace_root": workspace["root_path"],
        "skills": [_skill_summary(item) for item in skills],
        "context": context,
        "events": events,
    }


def _cancel_live_task_runtimes(task_id: str, *, except_session_id: str | None = None) -> None:
    with _TASK_RUNTIMES_LOCK:
        live = [
            orch for session_id, orch in _TASK_RUNTIMES.items()
            if session_id != except_session_id and orch.workspace_context.task_id == task_id
        ]
    for orch in live:
        orch.cancel_all()


@app.post("/tasks/{task_id}/sessions")
def start_task_session(task_id: str, body: dict):
    store = get_domain_store()
    workspace = store.get_task_workspace(task_id)
    if workspace is None:
        raise D.Conflict("Task Workspace를 먼저 준비하세요")
    task = store.get_task(task_id)
    project = store.get_project(task["project_id"])
    profile_id = str(
        body.get("agent_profile_id")
        or project.get("default_agent_profile_id")
        or "agent_default"
    )
    try:
        profile = _agent_profile_json(store.get_agent_profile(profile_id))
        previous = store.latest_dispatch(task_id)
        decision = adaptive.decide(
            task=task,
            base_profile=profile,
            scheduler_snapshot=scheduler_mod.default_scheduler().snapshot(),
            previous_dispatch=previous,
            verification_runs=store.list_verification_runs(task_id),
        )
        queue_override = {
            key: int(body[source])
            for key, source in (("priority", "priority"), ("timeout_ms", "queue_timeout_ms"))
            if source in body
        }
        effective_budget = decision["effective"]["budget"]
        if queue_override:
            effective_budget = D.merge_budget(
                effective_budget, {"queue": queue_override}
            )
            decision["effective"]["budget"] = effective_budget
        execution = store.create_execution(
            task_id=task_id,
            workspace_id=workspace["id"],
            agent_profile_id=profile_id,
            budget_override=effective_budget,
            adaptive_decision=decision,
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    _cancel_live_task_runtimes(task_id)
    return _task_session_detail(store, execution["session"]["id"])


@app.get("/tasks/{task_id}/sessions")
def list_task_sessions(task_id: str):
    store = get_domain_store()
    store.get_task(task_id)
    return [
        _task_session_detail(store, item["id"])
        for item in store.list_sessions(task_id)
    ]


@app.get("/tasks/{task_id}/sessions/latest")
def latest_task_session(task_id: str):
    store = get_domain_store()
    store.get_task(task_id)
    sessions = store.list_sessions(task_id)
    if not sessions:
        raise D.NotFound(f"Task의 AgentSession이 없습니다: {task_id}")
    return _task_session_detail(store, sessions[0]["id"])


@app.get("/sessions/{session_id}")
def get_agent_session(session_id: str):
    return _task_session_detail(get_domain_store(), session_id)


@app.post("/sessions/{session_id}/resume")
def resume_agent_session(session_id: str):
    store = get_domain_store()
    detail = _task_session_detail(store, session_id)
    latest = store.latest_dispatch(detail["task_id"])
    if latest is None or latest["id"] != detail["dispatch_id"]:
        raise D.StaleDispatch(f"오래된 Dispatch의 Session입니다: {detail['dispatch_id']}")
    if detail["status"] not in {"created", "idle"}:
        raise D.Conflict(f"resume할 수 없는 AgentSession 상태: {detail['status']}")
    if detail["dispatch"]["status"] not in {"queued", "needs_you"}:
        raise D.Conflict(f"resume할 수 없는 Dispatch 상태: {detail['dispatch']['status']}")
    return detail


@app.post("/sessions/{session_id}/stop")
def stop_agent_session(session_id: str):
    session = get_domain_store().get_session(session_id)
    _cancel_live_task_runtimes(session["task_id"])
    return _task_session_detail(
        get_domain_store(),
        get_domain_store().stop_execution(session_id)["id"],
    )


@app.get("/agents")
def list_agents():
    out = []
    for p in sorted(AGENTS_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            out.append({"id": p.stem, "name": p.stem, "error": str(e)[:200]})
            continue
        if not isinstance(raw, dict):
            out.append({
                "id": p.stem,
                "name": p.stem,
                "error": "스펙 최상위는 매핑이어야 합니다",
            })
            continue
        agent = {
            "id": p.stem,
            "instance_id": raw.get("_instance_id"),
            "name": raw.get("name", p.stem),
            "description": raw.get("description", ""),
            "model": raw.get("model", ""),
        }
        try:
            S.validate(raw)
        except (S.SpecError, yaml.YAMLError, TypeError, AttributeError) as e:
            agent["error"] = str(e)[:200]
        out.append(agent)
    return out


def _blank_spec(name: str) -> dict:
    """새 오케스트레이터의 기본 설정 — 이 상태로도 검증을 통과해야 한다."""
    return {
        "name": name,
        "description": "",
        "model": sorted(runtime.LOCAL_MODELS)[0],
        "system_prompt": (
            "You are an orchestrator. For separable subtasks, spawn workers with "
            "create_worker and integrate their results."),
        "tools": [],
        "worker_policy": "autonomous",
        "approval": "auto",
        "max_steps": 15,
    }


@app.post("/agents")
def create_agent(body: dict):
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "name이 필요합니다")
    # 파일명으로 쓸 수 있는 id를 만든다
    agent_id = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_") or "agent"
    p = _path(agent_id)
    n = 2
    while p.exists():
        p = _path(f"{agent_id}_{n}")
        n += 1
    spec = _blank_spec(name)
    spec["_instance_id"] = f"agent_instance_{uuid.uuid4().hex[:24]}"
    S.validate(spec)  # 기본 설정도 유효해야 한다 — 아니면 버그다
    p.write_text(S.dumps(spec), encoding="utf-8")
    return {"id": p.stem, "spec": spec, "yaml": S.dumps(spec), "errors": []}


@app.delete("/agents/{agent_id}")
def delete_agent(agent_id: str):
    p = _path(agent_id)
    if not p.is_file():
        raise HTTPException(404, f"없는 에이전트: {agent_id}")
    p.unlink()
    return {"deleted": agent_id}


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    p = _path(agent_id)
    if not p.is_file():
        raise HTTPException(404, f"없는 에이전트: {agent_id}")
    source = p.read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(source)
    except yaml.YAMLError as e:
        return {
            "id": agent_id,
            "spec": None,
            "yaml": source,
            "errors": [f"YAML 파싱 실패: {e}"],
        }
    # 구조가 UI에 안전하면 검증 오류와 함께 돌려줘 설정 폼에서 고칠 수 있게 한다.
    try:
        S.validate(raw)
        errors = []
    except (S.SpecError, yaml.YAMLError, TypeError, AttributeError) as e:
        errors = str(e).splitlines()
    # 매핑이 아니면 YAML 원문과 오류만 보여준다.
    spec = raw if isinstance(raw, dict) else None
    return {"id": agent_id, "spec": spec, "yaml": source, "errors": errors}


@app.put("/agents/{agent_id}")
def put_agent(agent_id: str, body: dict):
    new_spec = body.get("spec")
    if not isinstance(new_spec, dict):
        raise HTTPException(400, "body.spec 이 필요합니다")
    path = _path(agent_id)
    if not path.is_file():
        raise HTTPException(404, f"없는 에이전트: {agent_id}")
    current = yaml.safe_load(path.read_text(encoding="utf-8"))
    current_owner = current.get("_instance_id") if isinstance(current, dict) else None
    new_spec = dict(new_spec)
    if isinstance(current_owner, str):
        new_spec["_instance_id"] = current_owner
    else:
        new_spec.pop("_instance_id", None)
    try:
        S.validate(new_spec)
    except S.SpecError as e:
        # 저장은 하지 않고 문제를 돌려준다
        return {"saved": False, "errors": str(e).splitlines()}
    path.write_text(S.dumps(new_spec), encoding="utf-8")
    return {"saved": True, "errors": [], "yaml": S.dumps(new_spec)}


@app.get("/runs/{agent_id}")
def list_runs(agent_id: str):
    d = RUNS_DIR / _run_owner_id(agent_id)
    if not d.is_dir():
        return []
    out = []
    for f in sorted(d.glob("*.json"), reverse=True)[:50]:
        try:
            r = json.loads(f.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        out.append({k: r.get(k) for k in
                    ("id", "at", "cancelled", "duration_ms", "node_count", "summary", "inputs")})
    return out


@app.get("/runs/{agent_id}/{run_id}")
def get_run(agent_id: str, run_id: str):
    if "/" in run_id or ".." in run_id:
        raise HTTPException(400, "잘못된 run id")
    f = RUNS_DIR / _run_owner_id(agent_id) / f"{run_id}.json"
    if not f.is_file():
        raise HTTPException(404, f"없는 실행: {run_id}")
    return json.loads(f.read_text(encoding="utf-8"))


@app.get("/workspace")
def get_workspace():
    return {"path": str(_get_legacy_workspace())}


@app.post("/workspace")
def set_workspace(body: dict):
    path = body.get("path")
    if not path:
        raise HTTPException(400, "path가 필요합니다")
    previous = _get_legacy_workspace()
    try:
        p = _set_legacy_workspace(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    try:
        _persist_workspace(p)
    except OSError as e:
        # UI는 저장 실패를 받고 이전 경로를 계속 표시해야 하므로 메모리도 롤백한다.
        _set_legacy_workspace(previous)
        raise HTTPException(500, f"워크스페이스 설정 저장 실패: {e}")
    return {"path": str(p)}


# IDE성 파일 접근 — 전부 tools._resolve의 jail을 통과한다(워크스페이스 밖 거부).
_IGNORE = {".git", "node_modules", ".venv", "__pycache__", "out", "dist"}


@app.get("/workspace/tree")
def workspace_tree(path: str = ""):
    """디렉토리 한 층. 재귀 아님 — 큰 프로젝트에서 폭발하지 않게 lazy로 편다."""
    try:
        root = T._resolve(path or ".", _legacy_workspace_context("task_legacy_ide"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not root.is_dir():
        raise HTTPException(404, f"디렉토리가 아님: {path}")
    entries = []
    for p in sorted(root.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if p.name in _IGNORE or p.name.startswith("."):
            continue
        entries.append({"name": p.name, "type": "dir" if p.is_dir() else "file",
                        "size": p.stat().st_size if p.is_file() else None})
        if len(entries) >= 500:
            break
    return {"path": path, "entries": entries}


@app.get("/workspace/file")
def workspace_file(path: str):
    try:
        p = T._resolve(path, _legacy_workspace_context("task_legacy_ide"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    if not p.is_file():
        raise HTTPException(404, f"파일 없음: {path}")
    if p.stat().st_size > 1_000_000:
        return {"path": path, "content": None, "error": "1MB 초과 — 뷰어로 열기엔 너무 큼"}
    raw = p.read_bytes()
    if b"\x00" in raw[:8192]:
        return {"path": path, "content": None, "error": "바이너리 파일"}
    return {"path": path, "content": raw.decode("utf-8", errors="replace"), "error": None}


@app.get("/tools")
def list_tools():
    return T.listing()


@app.get("/models")
def list_models():
    return [{"name": n, "provider": "local"} for n in sorted(runtime.LOCAL_MODELS)]


APPROVAL_TIMEOUT = 300  # 초. 무응답은 거부로 친다.


@app.websocket("/tasks/{task_id}/sessions/{session_id}")
async def run_task_session(ws: WebSocket, task_id: str, session_id: str):
    """Stream one persisted AgentSession inside its Task Workspace.

    The Dispatch row is the ownership fence. Every runtime event is appended to
    SQLite only while that Dispatch is the latest non-terminal attempt for the Task.
    Reconnecting reconstructs the model transcript from persisted `transcript` events.
    """
    origin = ws.headers.get("origin")
    protocols = {
        p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()
    }
    if not _origin_allowed(origin) or "janus" not in protocols or not any(
        _token_valid(p) for p in protocols if p != "janus"
    ):
        await ws.close(code=1008)
        return

    store = get_domain_store()
    try:
        session = store.get_session(session_id)
        if session["task_id"] != task_id:
            raise D.Conflict("AgentSession이 다른 Task에 속합니다")
        dispatch = _dispatch_json(store.get_dispatch(session["dispatch_id"]))
        latest = store.latest_dispatch(task_id)
        if latest is None or latest["id"] != dispatch["id"]:
            raise D.StaleDispatch(f"오래된 Dispatch의 Session입니다: {dispatch['id']}")
        if session["status"] not in {"created", "idle"}:
            raise D.Conflict(f"연결할 수 없는 AgentSession 상태: {session['status']}")
        workspace = store.get_workspace(dispatch["workspace_id"])
        if workspace["state"] != "ready" or not workspace["root_path"]:
            raise D.Conflict("ready Workspace가 있어야 Session을 실행할 수 있습니다")
        spec = _task_runtime_spec(
            store, session["agent_profile_id"], budget=dispatch["budget"],
            adaptive_decision=dispatch["adaptive_decision"],
            profile_snapshot=dispatch["agent_profile_snapshot"],
        )
        spec["skills"] = [
            _skill_json(item) for item in store.snapshot_session_skills(session_id)
        ]
        context_snapshot = _task_context_snapshot(
            spec, dispatch, workspace, spec["skills"], store.list_session_events(session_id),
            store.get_task(task_id),
        )
        spec["context_preamble"] = context_snapshot["preamble"]
    except D.DomainError:
        await ws.close(code=1008)
        return

    await ws.accept(subprotocol="janus")
    loop = asyncio.get_running_loop()
    pending: dict[str, list] = {}
    pending_lock = threading.Lock()
    turn_task: asyncio.Task | None = None
    orch: runtime.Orchestration | None = None
    stop_requested = False
    stale_notified = threading.Event()
    transcript_events = [
        item["payload"] for item in store.list_session_events(session_id)
        if item["kind"] == "transcript"
    ]
    transcript_count = len(transcript_events)
    context = WorkspaceContext(
        root=Path(workspace["root_path"]),
        task_id=task_id,
        workspace_id=workspace["id"],
        dispatch_id=dispatch["id"],
    )

    async def _safe_send(payload: dict) -> None:
        try:
            await ws.send_json(payload)
        except Exception:
            pass

    def _direct_send(payload: dict) -> None:
        with suppress(RuntimeError):
            asyncio.run_coroutine_threadsafe(_safe_send(payload), loop)

    def _payload_with_ids(event: dict) -> dict:
        return {
            **event,
            "task_id": task_id,
            "workspace_id": workspace["id"],
            "dispatch_id": dispatch["id"],
            "session_id": session_id,
        }

    def send(event: dict) -> None:
        """Persist before delivery and reject any event that lost Dispatch ownership."""
        payload = _payload_with_ids(event)
        try:
            store.append_session_event(
                session_id,
                kind=str(payload.get("type") or "runtime"),
                payload=payload,
                task_id=task_id,
                dispatch_id=dispatch["id"],
                workspace_id=workspace["id"],
                require_latest=True,
            )
        except D.StaleDispatch:
            if not stale_notified.is_set():
                stale_notified.set()
                if orch is not None:
                    orch.cancel_all()
                _direct_send(_payload_with_ids({
                    "type": "stale_dispatch",
                    "error": "더 최신 Dispatch가 이 Task의 실행 권한을 소유합니다",
                }))
            return
        _direct_send(payload)

    def persist_final(event: dict) -> dict | None:
        """Persist a terminal event only if this is still the latest Dispatch."""
        payload = _payload_with_ids(event)
        try:
            store.append_session_event(
                session_id,
                kind=str(payload.get("type") or "runtime"),
                payload=payload,
                task_id=task_id,
                dispatch_id=dispatch["id"],
                workspace_id=workspace["id"],
                require_latest=True,
                require_active=False,
            )
        except D.StaleDispatch:
            return None
        return payload

    async def send_final(event: dict) -> None:
        """Flush earlier worker sends, then deliver the persisted terminal event."""
        payload = persist_final(event)
        if payload is None:
            return
        await asyncio.sleep(0)
        await _safe_send(payload)

    def approver(
        node_id: str, tool: str, args: dict, approval_context: WorkspaceContext
    ) -> bool:
        req_id = uuid.uuid4().hex[:12]
        event = threading.Event()
        with pending_lock:
            pending[req_id] = [event, False]
        send({
            "type": "approval_request",
            "id": req_id,
            "node_id": node_id,
            "tool": tool,
            "args": args,
            **approval_context.identifiers(),
        })
        if not event.wait(timeout=APPROVAL_TIMEOUT):
            with pending_lock:
                pending.pop(req_id, None)
            return False
        with pending_lock:
            slot = pending.pop(req_id, None)
        return bool(slot and slot[1])

    def skill_loaded(skill_version_id: str, reason: str, prompt_tokens: int) -> None:
        store.mark_session_skill_loaded(
            session_id, skill_version_id, reason=reason, prompt_tokens=prompt_tokens,
        )

    def ensure_orchestration() -> runtime.Orchestration:
        nonlocal orch
        if orch is None:
            orch = runtime.Orchestration(
                spec,
                send=send,
                approver=approver,
                workspace_context=context,
                task_id=task_id,
                session_id=session_id,
                budget=spec["budget"],
                budget_usage=dispatch["usage"],
                on_skill_loaded=skill_loaded,
            )
            orch.session.events = [dict(item) for item in transcript_events]
            with _TASK_RUNTIMES_LOCK:
                existing = _TASK_RUNTIMES.get(session_id)
                if existing is not None and existing is not orch:
                    raise D.Conflict("AgentSession이 이미 다른 연결에서 실행 중입니다")
                _TASK_RUNTIMES[session_id] = orch
        return orch

    def persist_transcript(current: runtime.Orchestration) -> None:
        nonlocal transcript_count
        new_events = current.session.events[transcript_count:]
        for event in new_events:
            store.append_session_event(
                session_id,
                kind="transcript",
                payload=dict(event),
                task_id=task_id,
                dispatch_id=dispatch["id"],
                workspace_id=workspace["id"],
                require_latest=True,
            )
        transcript_count += len(new_events)

    async def do_turn(text: str) -> None:
        nonlocal stop_requested
        current: runtime.Orchestration | None = None
        failure: str | None = None
        activated = False
        try:
            store.activate_session_turn(session_id)
            activated = True
            current = ensure_orchestration()
            send({"type": "run_start", "agent_profile_id": session["agent_profile_id"]})
            runtime_done = threading.Event()
            runtime_errors: list[BaseException] = []

            def run_persisted_turn() -> None:
                try:
                    current.turn(text, dispatch_id=dispatch["id"])
                except BaseException as error:
                    runtime_errors.append(error)
                finally:
                    runtime_done.set()

            threading.Thread(target=run_persisted_turn, daemon=True).start()
            while not runtime_done.is_set():
                try:
                    await asyncio.sleep(0.02)
                except asyncio.CancelledError:
                    # The renderer socket owns delivery only. ASGI cancellation
                    # of its connection scope must not settle the persistent
                    # Dispatch before the model thread has finished.
                    task = asyncio.current_task()
                    if task is not None:
                        task.uncancel()
            if runtime_errors:
                raise runtime_errors[0]
            persist_transcript(current)
            if current.budget_exhausted_reason:
                failure = f"budget exhausted: {current.budget_exhausted_reason}"
                send({"type": "run_error", "error": failure})
        except D.StaleDispatch:
            stale_notified.set()
            if current is not None:
                current.cancel_all()
            _direct_send(_payload_with_ids({
                "type": "stale_dispatch",
                "error": "더 최신 Dispatch가 이 Task의 실행 권한을 소유합니다",
            }))
            return
        except Exception as error:
            failure = f"{type(error).__name__}: {error}"
            recovery_hint = recovery.classify_failure(error)
            if current is not None:
                current.turn_failed = True
            if activated:
                send({"type": "run_error", "error": failure, "recovery": recovery_hint})
            else:
                _direct_send(_payload_with_ids({
                    "type": "run_error", "error": failure, "recovery": recovery_hint,
                }))
        finally:
            if stale_notified.is_set() or not activated:
                return
            try:
                if current is not None and len(current.session.events) > transcript_count:
                    persist_transcript(current)
                if current is not None:
                    budget_snapshot = current.snapshot_budget()
                    store.record_dispatch_budget(
                        dispatch["id"],
                        usage=budget_snapshot["usage"],
                        exhausted_reason=budget_snapshot["exhausted_reason"],
                    )
                persisted = store.get_session(session_id)
                if stop_requested or persisted["status"] == "stopped":
                    if persisted["status"] != "stopped":
                        store.stop_execution(session_id)
                    await send_final({"type": "session_stopped"})
                else:
                    store.settle_session_turn(
                        session_id, failed=failure is not None, error=failure
                    )
                await send_final({
                    "type": "turn_end",
                    "cancelled": bool(current and current.cancelled_turn),
                    "session_status": store.get_session(session_id)["status"],
                })
                if stop_requested:
                    await asyncio.sleep(0)
                    await ws.close(code=1000)
            except D.StaleDispatch:
                stale_notified.set()
                _direct_send(_payload_with_ids({
                    "type": "stale_dispatch",
                    "error": "더 최신 Dispatch가 이 Task의 실행 권한을 소유합니다",
                }))

    await _safe_send(_payload_with_ids({
        "type": "session_ready",
        "agent_profile_id": session["agent_profile_id"],
        "resumed_events": transcript_count,
        "session_status": session["status"],
    }))

    try:
        while True:
            message = await ws.receive_json()
            kind = message.get("type")
            if kind in {"message", "resume"}:
                text = str(message.get("text") or "").strip()
                if not text or (turn_task and not turn_task.done()) or stop_requested:
                    continue
                turn_task = asyncio.create_task(do_turn(text))
            elif kind == "approval_response":
                with pending_lock:
                    slot = pending.get(message.get("id"))
                if slot:
                    slot[1] = bool(message.get("approved"))
                    slot[0].set()
            elif kind == "cancel":
                if orch is not None:
                    orch.cancel_all()
                with pending_lock:
                    slots = list(pending.values())
                for slot in slots:
                    slot[1] = False
                    slot[0].set()
            elif kind == "stop_worker" and orch is not None:
                orch.stop_worker(str(message.get("node_id") or ""))
            elif kind == "stop":
                stop_requested = True
                if orch is not None:
                    orch.cancel_all()
                with pending_lock:
                    slots = list(pending.values())
                for slot in slots:
                    slot[1] = False
                    slot[0].set()
                if turn_task is None or turn_task.done():
                    store.stop_execution(session_id)
                    await send_final({"type": "session_stopped"})
                    break
    except WebSocketDisconnect:
        pass
    except Exception as error:
        await _safe_send(_payload_with_ids({
            "type": "run_error", "error": f"{type(error).__name__}: {error}"
        }))
    finally:
        # A persisted Task turn belongs to its Dispatch, not to one renderer
        # socket. Window reloads, navigation, and transient WebSocket reconnects
        # must not throw away model work that is already running. Events keep
        # being persisted by send(); only explicit cancel/stop or server shutdown
        # may cancel the orchestration.
        with pending_lock:
            slots = list(pending.values())
        for slot in slots:
            slot[1] = False
            slot[0].set()
        if turn_task and not turn_task.done():
            with suppress(asyncio.CancelledError):
                await asyncio.shield(turn_task)
        with _TASK_RUNTIMES_LOCK:
            if orch is not None and _TASK_RUNTIMES.get(session_id) is orch:
                _TASK_RUNTIMES.pop(session_id, None)


@app.websocket("/run/{agent_id}")
async def run_agent(ws: WebSocket, agent_id: str):
    """WS 연결 하나 = 오케스트레이터 대화 하나.

    승인은 왕복이다 — 워커 스레드가 응답을 기다리는 동안에도 WS는 계속 메시지를
    받을 수 있어야 한다. 그래서 턴은 태스크로 띄우고 이 루프는 수신만 한다.
    첫 message가 실행 시작이고, 소켓이 닫히면 대화가 끝난다.
    """
    origin = ws.headers.get("origin")
    protocols = {
        p.strip() for p in ws.headers.get("sec-websocket-protocol", "").split(",") if p.strip()
    }
    if not _origin_allowed(origin) or "janus" not in protocols or not any(
        _token_valid(p) for p in protocols if p != "janus"
    ):
        # accept 전 close는 HTTP 403으로 핸드쉐이크를 거부한다.
        await ws.close(code=1008)
        return
    await ws.accept(subprotocol="janus")
    loop = asyncio.get_running_loop()
    pending: dict[str, list] = {}   # req_id -> [threading.Event, approved]
    pending_lock = threading.Lock()
    turn_task: asyncio.Task | None = None
    orch: runtime.Orchestration | None = None
    run_owner_id = agent_id
    run_id = f"{datetime.now().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:4]}"
    workspace_context = _legacy_workspace_context(f"task_legacy_{run_id}")

    def approver(
        node_id: str, tool: str, args: dict, context: WorkspaceContext
    ) -> bool:
        """agent 워커 스레드에서 **블로킹**으로 호출된다."""
        req_id = uuid.uuid4().hex[:12]
        ev = threading.Event()
        with pending_lock:
            pending[req_id] = [ev, False]
        asyncio.run_coroutine_threadsafe(
            ws.send_json({
                "type": "approval_request", "id": req_id,
                "node_id": node_id, "tool": tool, "args": args,
                **context.identifiers(),
            }),
            loop,
        )
        if not ev.wait(timeout=APPROVAL_TIMEOUT):
            with pending_lock:
                pending.pop(req_id, None)
            return False
        with pending_lock:
            slot = pending.pop(req_id, None)
        return bool(slot and slot[1])

    async def _safe_send(ev: dict) -> None:
        try:
            await ws.send_json(ev)
        except Exception:
            pass  # 연결이 이미 끊김 — 잔여 이벤트는 버린다

    def send(ev: dict) -> None:
        """runtime 워커 스레드에서 안전하게 WS로 밀어넣는다."""
        try:
            asyncio.run_coroutine_threadsafe(_safe_send(ev), loop)
        except RuntimeError:
            pass  # 루프가 이미 닫힘 — 연결 종료 후의 잔여 이벤트

    def save() -> None:
        if orch is not None:
            _save_run(agent_id, run_id, {"task": orch.first_message or ""},
                      orch.snapshot_spans(), orch.cancelled_turn,
                      orch.snapshot_telemetry(), owner_id=run_owner_id)

    def run_identifiers() -> dict:
        context = workspace_context
        if orch is not None and (orch.current_dispatch_id or orch.last_dispatch_id):
            context = context.for_dispatch(
                str(orch.current_dispatch_id or orch.last_dispatch_id)
            )
        return {
            **context.identifiers(),
            "session_id": orch.telemetry.session_id if orch is not None else None,
        }

    async def do_turn(text: str):
        nonlocal orch, run_owner_id
        if orch is None:
            p = _path(agent_id)
            if not p.is_file():
                await ws.send_json({"type": "run_error", "error": f"없는 에이전트: {agent_id}"})
                return
            try:
                spec = S.load(p)
                run_owner_id = _run_owner_id(agent_id)
                orch = runtime.Orchestration(
                    spec, send=send, approver=approver,
                    workspace_context=workspace_context,
                )
            except (S.SpecError, yaml.YAMLError) as e:
                await ws.send_json({"type": "run_error", "error": str(e)})
                return
            await ws.send_json({
                "type": "run_start", "agent_id": agent_id,
                **workspace_context.identifiers(),
                "session_id": orch.telemetry.session_id,
            })
        try:
            await asyncio.to_thread(orch.turn, text)
        except Exception as e:
            orch.turn_failed = True
            send({
                "type": "run_error", "error": f"{type(e).__name__}: {e}",
                "recovery": recovery.classify_failure(e),
                **run_identifiers(),
            })
        finally:
            save()   # 턴마다 같은 run_id로 덮어쓴다 — 대화 도중 죽어도 기록이 남는다
            # send()로 보낸다 — 직접 await하면 워커가 예약해 둔 span/event 전송을
            # 추월해 turn_end가 먼저 도착할 수 있다. 같은 FIFO 경로가 순서를 지킨다.
            send({"type": "turn_end", **run_identifiers()})
            # 저장 후 — 히스토리 갱신이 빈손이 안 되게

    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")

            if t == "message":
                if turn_task and not turn_task.done():
                    continue          # 턴이 돌고 있으면 무시 (컴포저도 잠겨 있다)
                turn_task = asyncio.create_task(do_turn(str(msg.get("text") or "")))

            elif t == "approval_response":
                with pending_lock:
                    slot = pending.get(msg.get("id"))
                if slot:
                    slot[1] = bool(msg.get("approved"))
                    slot[0].set()

            elif t == "cancel":
                # 현재 턴만 중단 — 세션은 살아서 다음 message가 대화를 잇는다
                if orch is not None:
                    orch.cancel_all()
                # 대기 중인 승인은 전부 거부로 풀어준다 — 안 그러면 스레드가 매달린다
                with pending_lock:
                    slots = list(pending.values())
                for slot in slots:
                    slot[1] = False
                    slot[0].set()

            elif t == "stop_worker":
                if orch is not None:
                    orch.stop_worker(str(msg.get("node_id") or ""))

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "run_error", "error": f"{type(e).__name__}: {e}"})
        except Exception:
            pass
    finally:
        if orch is not None:
            orch.cancel_all()   # 연결이 끊기면 워커도 멈춰야 한다
        if turn_task and not turn_task.done():
            turn_task.cancel()  # asyncio 쪽 백스톱
        with pending_lock:
            slots = list(pending.values())
        for slot in slots:   # 매달린 워커 스레드를 풀어준다
            slot[1] = False
            slot[0].set()
        save()


def main():
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ.get("JANUS_PORT", "8765")),
        log_level="info",
        # SIGTERM 뒤 스트리밍 연결을 기다리다 supervisor의 5초 grace를 넘기면
        # SIGKILL을 맞아 SQLite 쓰기가 끊긴다. 남은 연결은 강제로 닫고 정상 종료한다.
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )


if __name__ == "__main__":
    main()
