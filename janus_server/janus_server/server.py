"""Janus 서버 — 그래프 CRUD + 실행 스트리밍.

렌더러는 이 서버하고만 통신한다. Electron main은 창과 다이얼로그만 담당한다.
"""

from __future__ import annotations

import asyncio
import re
import threading
import uuid
from pathlib import Path

import yaml
from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from . import compile as C
from . import spec as S
from . import tools as T
from . import trace

AGENTS_DIR = Path(__file__).parent / "agents"

app = FastAPI(title="Janus", version="0.1.0")
# Vite 개발 서버(다른 포트)에서 부르므로 필요하다. 로컬 단일 사용자 도구다.
app.add_middleware(CORSMiddleware, allow_origins=["*"],
                   allow_methods=["*"], allow_headers=["*"])


def _path(agent_id: str) -> Path:
    # 경로 조작 차단 — id는 파일명 한 조각이어야 한다
    if "/" in agent_id or "\\" in agent_id or agent_id.startswith("."):
        raise HTTPException(400, f"잘못된 agent id: {agent_id!r}")
    return AGENTS_DIR / f"{agent_id}.yaml"


@app.get("/health")
def health():
    return {"ok": True, "version": app.version}


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
    await ws.accept()
    loop = asyncio.get_running_loop()
    pending: dict[str, list] = {}   # req_id -> [threading.Event, approved]
    run_task: asyncio.Task | None = None

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

        node_ids = {n["id"] for n in spec["nodes"]}
        state = C.initial_state(spec, inputs)
        await ws.send_json({"type": "run_start", "agent_id": agent_id})
        async for ev in trace.run(graph, state, node_ids, C.RECURSION_LIMIT,
                                  approver=approver):
            await ws.send_json(ev)

    try:
        while True:
            msg = await ws.receive_json()
            t = msg.get("type")

            if t == "run":
                if run_task and not run_task.done():
                    continue          # 이미 돌고 있으면 무시
                run_task = asyncio.create_task(do_run(msg.get("inputs") or {}))

            elif t == "approval_response":
                slot = pending.get(msg.get("id"))
                if slot:
                    slot[1] = bool(msg.get("approved"))
                    slot[0].set()

            elif t == "cancel":
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
