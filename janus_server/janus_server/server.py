"""Janus 서버 — 에이전트 CRUD + 오케스트레이터 대화 스트리밍.

렌더러는 이 서버하고만 통신한다. Electron main은 창과 다이얼로그만 담당한다.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import secrets
import sys
import tempfile
import threading
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import runtime
from . import domain as D
from . import spec as S
from . import tools as T
from .workspace import WorkspaceContext

AGENTS_DIR = Path(__file__).parent / "agents"
RUNS_DIR = Path(__file__).parent / "runs"    # 실행 기록. 앱을 닫아도 남는다.
STATE_FILE = Path(
    os.environ.get("JANUS_STATE_FILE", str(Path.home() / ".janus" / "state.json"))
).expanduser()
DOMAIN_DB_FILE = Path(
    os.environ.get("JANUS_DB_FILE", str(Path.home() / ".janus" / "janus.sqlite3"))
).expanduser()
_STATE_LOCK = threading.Lock()
_LEGACY_WORKSPACE_LOCK = threading.Lock()
_LEGACY_WORKSPACE_ROOT = (Path(__file__).parent / "workspace").resolve()
_DOMAIN_LOCK = threading.Lock()
_DOMAIN_STORE: D.DomainStore | None = None
_DOMAIN_STORE_PATH: Path | None = None

# Electron main이 기동마다 만든 토큰을 서버/렌더러에만 나눠준다.
# 수동 기동도 무인증으로 열리지 않도록, env가 없으면 서버가 자체적으로
# 일회용 토큰을 만들고 콘솔에만 알린다.
AUTH_TOKEN = os.environ.get("JANUS_AUTH_TOKEN") or secrets.token_hex(32)
if "JANUS_AUTH_TOKEN" not in os.environ:
    print(f"[janus] generated auth token: {AUTH_TOKEN}", file=sys.stderr)

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
    global _DOMAIN_STORE, _DOMAIN_STORE_PATH
    path = Path(os.environ.get("JANUS_DB_FILE", str(DOMAIN_DB_FILE))).expanduser().resolve()
    with _DOMAIN_LOCK:
        if _DOMAIN_STORE is None or _DOMAIN_STORE_PATH != path:
            _DOMAIN_STORE = D.DomainStore(path)
            _DOMAIN_STORE_PATH = path
        return _DOMAIN_STORE


def _save_run(agent_id: str, run_id: str, inputs: dict, spans: list,
              cancelled: bool, telemetry: dict | None = None) -> None:
    """실행 하나를 단일 JSON 파일로 남긴다. run_id가 고정이라 대화가 이어질 때마다
    같은 파일을 덮어쓴다 — 한 대화 = 한 기록."""
    if not spans:
        return
    d = RUNS_DIR / agent_id
    d.mkdir(parents=True, exist_ok=True)
    total = max((s.get("started_ms", 0) + (s.get("duration_ms") or 0)) for s in spans)
    first = spans[0].get("output") or {}
    summary = next((str(v) for v in first.values() if v), "")
    (d / f"{run_id}.json").write_text(json.dumps({
        "id": run_id, "agent_id": agent_id, "at": run_id.rsplit("-", 1)[0],
        "inputs": inputs, "cancelled": cancelled,
        "duration_ms": total, "node_count": len(spans),
        "summary": summary[:120], "spans": spans,
        "telemetry": telemetry,
    }, ensure_ascii=False), encoding="utf-8")

app = FastAPI(title="Janus", version="0.1.0")


@app.exception_handler(D.NotFound)
async def domain_not_found(_request: Request, error: D.NotFound):
    return JSONResponse({"detail": str(error)}, status_code=404)


@app.exception_handler(D.Conflict)
async def domain_conflict(_request: Request, error: D.Conflict):
    return JSONResponse({"detail": str(error)}, status_code=409)


@app.exception_handler(D.InvalidTransition)
async def domain_transition(_request: Request, error: D.InvalidTransition):
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
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Janus-Token"],
)


def _path(agent_id: str) -> Path:
    # 경로 조작 차단 — id는 파일명 한 조각이어야 한다
    if "/" in agent_id or "\\" in agent_id or agent_id.startswith("."):
        raise HTTPException(400, f"잘못된 agent id: {agent_id!r}")
    return AGENTS_DIR / f"{agent_id}.yaml"


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


# ─────────────────────────── P1 ADE domain API ───────────────────────────


@app.get("/projects")
def list_projects(include_archived: bool = False):
    return get_domain_store().list_projects(include_archived=include_archived)


@app.post("/projects")
def create_project(body: dict):
    return get_domain_store().create_project(
        name=str(body.get("name") or ""), repo_path=str(body.get("repo_path") or "")
    )


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    return get_domain_store().get_project(project_id)


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


@app.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = get_domain_store().get_task(task_id)
    task["workspace"] = get_domain_store().get_task_workspace(task_id)
    task["dispatches"] = get_domain_store().list_dispatches(task_id)
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


@app.delete("/tasks/{task_id}")
def archive_task(task_id: str):
    return get_domain_store().archive_task(task_id)


def _agent_profile_json(profile: dict) -> dict:
    return {**profile, "tools": json.loads(profile["tools_json"])}


def _model_profile_json(profile: dict) -> dict:
    return {**profile, "config": json.loads(profile["config_json"])}


@app.get("/profiles/models")
def list_model_profiles():
    return [_model_profile_json(item) for item in get_domain_store().list_model_profiles()]


@app.get("/profiles/agents")
def list_agent_profiles():
    return [_agent_profile_json(item) for item in get_domain_store().list_agent_profiles()]


@app.post("/profiles/agents")
def create_agent_profile(body: dict):
    profile = get_domain_store().create_agent_profile(
        name=str(body.get("name") or ""),
        description=str(body.get("description") or ""),
        system_prompt=str(body.get("system_prompt") or ""),
        tools=[str(item) for item in body.get("tools") or []],
        approval=str(body.get("approval") or "ask"),
        worker_policy=str(body.get("worker_policy") or "autonomous"),
        max_steps=int(body.get("max_steps") or 15),
        model_profile_id=str(body.get("model_profile_id") or "model_qwen38_27b_4bit"),
    )
    return _agent_profile_json(profile)


@app.put("/profiles/agents/{profile_id}")
def update_agent_profile(profile_id: str, body: dict):
    profile = get_domain_store().update_agent_profile(profile_id, **body)
    return _agent_profile_json(profile)


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
    try:
        S.validate(new_spec)
    except S.SpecError as e:
        # 저장은 하지 않고 문제를 돌려준다
        return {"saved": False, "errors": str(e).splitlines()}
    _path(agent_id).write_text(S.dumps(new_spec), encoding="utf-8")
    return {"saved": True, "errors": [], "yaml": S.dumps(new_spec)}


@app.get("/runs/{agent_id}")
def list_runs(agent_id: str):
    d = RUNS_DIR / agent_id
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
    f = RUNS_DIR / agent_id / f"{run_id}.json"
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
                      orch.snapshot_telemetry())

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
        nonlocal orch
        if orch is None:
            p = _path(agent_id)
            if not p.is_file():
                await ws.send_json({"type": "run_error", "error": f"없는 에이전트: {agent_id}"})
                return
            try:
                spec = S.load(p)
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
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
