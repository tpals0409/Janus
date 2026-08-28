"""Janus 서버 — Task·Session 중심의 로컬 ADE 백엔드.

렌더러는 이 서버하고만 통신한다. Electron main은 창과 다이얼로그만 담당한다.
앱 조립(미들웨어·예외 핸들러·health/maintenance)만 이 파일에 있고,
상태와 공유 헬퍼는 shared.py, 도메인 API는 routers/에 산다.
"""

from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import diagnostics, recovery
from . import domain as D
from . import workspace_service as WS
from .shared import (  # noqa: F401 — 테스트·스크립트가 server.<이름>으로도 참조한다
    _BACKUP_LOCK,
    _EVENT_BUS,
    _IGNORE,
    _TASK_RUNTIMES,
    _TASK_RUNTIMES_LOCK,
    ALLOWED_ORIGINS,
    APPROVAL_TIMEOUT,
    AUTH_TOKEN,
    BACKUPS_DIR,
    DIAGNOSTICS_DIR,
    DOMAIN_DB_FILE,
    GRACEFUL_SHUTDOWN_SECONDS,
    SKILLS_DIR,
    WORKTREES_DIR,
    _agent_profile_json,
    _delegation_base_ref,
    _dispatch_json,
    _ensure_packaged_skills,
    _evaluation_comparison_json,
    _learning_json,
    _model_profile_json,
    _origin_allowed,
    _pin_library_skills,
    _publish_change,
    _review_snapshot,
    _session_approval_key,
    _skill_json,
    _skill_summary,
    _token_valid,
    _verification_commands,
    _verification_workspace,
    _workspace_job_active,
    get_domain_store,
    get_github_service,
    get_terminal_manager,
    get_workspace_service,
    shutdown_local_resources,
)
from .version import __version__


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


def main():
    import uvicorn

    uvicorn.run(
        app, host="127.0.0.1", port=int(os.environ.get("JANUS_PORT", "8765")),
        log_level="info",
        # SIGTERM 뒤 스트리밍 연결을 기다리다 supervisor의 5초 grace를 넘기면
        # SIGKILL을 맞아 SQLite 쓰기가 끊긴다. 남은 연결은 강제로 닫고 정상 종료한다.
        timeout_graceful_shutdown=GRACEFUL_SHUTDOWN_SECONDS,
    )


# ── 도메인 라우터 장착 — routers는 shared만 보므로 순환이 없다 ──────────
from .routers import (  # noqa: E402 — app이 먼저 있어야 데코레이터 없는 조립이 단순하다
    development,
    evaluations,
    mcp,
    model,
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
app.include_router(mcp.router)
app.include_router(model.router)
app.include_router(terminals.router)
app.include_router(development.router)
app.include_router(skills.router)
app.include_router(profiles.router)
app.include_router(sessions.router)


if __name__ == "__main__":
    main()
