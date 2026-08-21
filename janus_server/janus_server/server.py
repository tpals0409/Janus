"""Janus 서버 — 그래프 CRUD + 실행 스트리밍.

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
import threading
import uuid
from datetime import datetime
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import compile as C
from . import spec as S
from . import tools as T
from . import trace

AGENTS_DIR = Path(__file__).parent / "agents"
RUNS_DIR = Path(__file__).parent / "runs"    # 실행 기록. 앱을 닫아도 남는다.

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


def _save_run(agent_id: str, inputs: dict, spans: list, cancelled: bool) -> None:
    """한 번의 실행을 JSONL 아닌 단일 JSON 파일로 남긴다. 이벤트 로그가 직렬화
    가능하므로 스팬을 그대로 떨구면 나중에 그대로 다시 그릴 수 있다."""
    if not spans:
        return
    d = RUNS_DIR / agent_id
    d.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_id = f"{stamp}-{uuid.uuid4().hex[:4]}"
    total = max((s.get("started_ms", 0) + (s.get("duration_ms") or 0)) for s in spans)
    last = spans[-1].get("output") or {}
    summary = next((str(v) for v in last.values() if v), "")
    (d / f"{run_id}.json").write_text(json.dumps({
        "id": run_id, "agent_id": agent_id, "at": stamp,
        "inputs": inputs, "cancelled": cancelled,
        "duration_ms": total, "node_count": len(spans),
        "summary": summary[:120], "spans": spans,
    }, ensure_ascii=False), encoding="utf-8")

app = FastAPI(title="Janus", version="0.1.0")
# Vite 개발 서버(다른 포트)에서 부르되, 그 기동에 선택된 origin만 허용한다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=sorted(ALLOWED_ORIGINS),
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "X-Janus-Token"],
)


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
    return {"ok": True, "version": app.version, "mlx": mlx}


@app.get("/agents")
def list_agents():
    out = []
    for p in sorted(AGENTS_DIR.glob("*.yaml")):
        try:
            raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError as e:
            out.append({"id": p.stem, "name": p.stem, "error": str(e)[:200]})
            continue
        out.append({
            "id": p.stem,
            "name": raw.get("name", p.stem),
            "description": raw.get("description", ""),
            "node_count": len(raw.get("nodes") or []),
        })
    return out


def _blank_spec(name: str) -> dict:
    """새 에이전트의 최소 그래프 — 이 상태로도 검증을 통과해야 한다."""
    return {
        "name": name,
        "version": 1,
        "nodes": [
            {"id": "start", "type": "start", "outputs": ["input"],
             "position": {"x": 80, "y": 200}},
            {"id": "end", "type": "end", "inputs": {"result": "{{ start.input }}"},
             "position": {"x": 420, "y": 200}},
        ],
        "edges": [{"from": "start", "to": "end"}],
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
    S.validate(spec)  # 빈 그래프도 유효해야 한다 — 아니면 버그다
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
    raw = yaml.safe_load(p.read_text(encoding="utf-8"))
    # 유효하지 않아도 돌려준다 — 캔버스에서 고쳐야 하므로
    try:
        S.validate(raw)
        errors = []
    except S.SpecError as e:
        errors = str(e).splitlines()
    return {"id": agent_id, "spec": raw, "yaml": S.dumps(raw), "errors": errors}


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
    return {"path": str(T.get_workspace())}


@app.post("/workspace")
def set_workspace(body: dict):
    path = body.get("path")
    if not path:
        raise HTTPException(400, "path가 필요합니다")
    try:
        p = T.set_workspace(path)
    except ValueError as e:
        raise HTTPException(400, str(e))
    return {"path": str(p)}


# IDE성 파일 접근 — 전부 tools._resolve의 jail을 통과한다(워크스페이스 밖 거부).
_IGNORE = {".git", "node_modules", ".venv", "__pycache__", "out", "dist"}


@app.get("/workspace/tree")
def workspace_tree(path: str = ""):
    """디렉토리 한 층. 재귀 아님 — 큰 프로젝트에서 폭발하지 않게 lazy로 편다."""
    try:
        root = T._resolve(path or ".")
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
        p = T._resolve(path)
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
    return [{"name": n, "provider": "local"} for n in sorted(C.LOCAL_MODELS)]


APPROVAL_TIMEOUT = 300  # 초. 무응답은 거부로 친다.


@app.websocket("/run/{agent_id}")
async def run_agent(ws: WebSocket, agent_id: str):
    """실행과 수신을 분리한다.

    승인은 왕복이다 — 에이전트 워커 스레드가 응답을 기다리는 동안에도 WS는 계속
    메시지를 받을 수 있어야 한다. 그래서 실행은 태스크로 띄우고 이 루프는 수신만 한다.
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
    run_task: asyncio.Task | None = None
    cancel_event = threading.Event()

    def approver(node_id: str, tool: str, args: dict) -> bool:
        """agent 워커 스레드에서 **블로킹**으로 호출된다."""
        req_id = uuid.uuid4().hex[:12]
        ev = threading.Event()
        pending[req_id] = [ev, False]
        asyncio.run_coroutine_threadsafe(
            ws.send_json({"type": "approval_request", "id": req_id,
                          "node_id": node_id, "tool": tool, "args": args}),
            loop,
        )
        if not ev.wait(timeout=APPROVAL_TIMEOUT):
            pending.pop(req_id, None)
            return False
        return pending.pop(req_id)[1]

    async def do_run(inputs: dict):
        p = _path(agent_id)
        if not p.is_file():
            await ws.send_json({"type": "run_error", "error": f"없는 에이전트: {agent_id}"})
            return
        try:
            spec = S.load(p)
            graph = C.build(spec)
        except S.SpecError as e:
            await ws.send_json({"type": "run_error", "error": str(e)})
            return

        node_types = {n["id"]: n["type"] for n in spec["nodes"]}
        state = C.initial_state(spec, inputs)
        await ws.send_json({"type": "run_start", "agent_id": agent_id})
        run_spans: list = []
        cancelled = False
        try:
            async for ev in trace.run(graph, state, node_types, C.RECURSION_LIMIT,
                                      approver=approver, cancel_event=cancel_event):
                if ev["type"] == "span_end":
                    run_spans.append(ev["span"])
                elif ev["type"] == "run_end":
                    cancelled = bool(ev.get("cancelled"))
                await ws.send_json(ev)
        except asyncio.CancelledError:
            # task.cancel() 백스톱으로 끊긴 경우 — UI가 '실행 중'에 갇히면 안 된다
            cancelled = True
            await ws.send_json({"type": "run_end", "cancelled": True})
        finally:
            _save_run(agent_id, inputs, run_spans, cancelled)

    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")

            if t == "run":
                if run_task and not run_task.done():
                    continue          # 이미 돌고 있으면 무시
                cancel_event.clear()
                run_task = asyncio.create_task(do_run(msg.get("inputs") or {}))

            elif t == "approval_response":
                slot = pending.get(msg.get("id"))
                if slot:
                    slot[1] = bool(msg.get("approved"))
                    slot[0].set()

            elif t == "cancel":
                # ① 에이전트 워커 스레드에 취소 신호 — 생성 중에도 스트림을 닫고 나온다
                cancel_event.set()
                # ② asyncio 쪽 백스톱
                if run_task and not run_task.done():
                    run_task.cancel()
                # 대기 중인 승인은 전부 거부로 풀어준다 — 안 그러면 스레드가 매달린다
                for slot in pending.values():
                    slot[1] = False
                    slot[0].set()

    except WebSocketDisconnect:
        pass
    except Exception as e:
        try:
            await ws.send_json({"type": "run_error", "error": f"{type(e).__name__}: {e}"})
        except Exception:
            pass
    finally:
        cancel_event.set()   # 연결이 끊기면 워커도 멈춰야 한다
        if run_task and not run_task.done():
            run_task.cancel()
        for slot in pending.values():   # 매달린 워커 스레드를 풀어준다
            slot[1] = False
            slot[0].set()


def main():
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8765, log_level="info")


if __name__ == "__main__":
    main()
