"""Janus terminals 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

from fastapi import APIRouter

from .. import domain as D
from .. import terminal_service as terminal_mod
from ..shared import (
    get_domain_store,
    get_terminal_manager,
)

router = APIRouter()

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



@router.get("/tasks/{task_id}/terminals")
def list_task_terminals(task_id: str):
    store = get_domain_store()
    store.get_task(task_id)
    return [_terminal_json(item) for item in store.list_task_terminals(task_id)]



@router.post("/tasks/{task_id}/terminals")
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



@router.get("/tasks/{task_id}/terminals/{terminal_id}")
def get_task_terminal(task_id: str, terminal_id: str, after_offset: int = 0):
    item = get_domain_store().get_task_terminal(terminal_id)
    if item["task_id"] != task_id:
        raise D.Conflict("terminal이 다른 Task에 속합니다")
    return _terminal_json(item, after_offset=after_offset)



@router.post("/tasks/{task_id}/terminals/{terminal_id}/input")
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



@router.post("/tasks/{task_id}/terminals/{terminal_id}/resize")
def resize_task_terminal(task_id: str, terminal_id: str, body: dict):
    session = _owned_live_terminal(task_id, terminal_id)
    try:
        session.resize(int(body.get("columns") or 80), int(body.get("rows") or 24))
    except (TypeError, ValueError, OSError) as error:
        raise D.Conflict(f"terminal resize 실패: {error}") from error
    return {"terminal_id": terminal_id, "resized": True}



@router.delete("/tasks/{task_id}/terminals/{terminal_id}")
def stop_task_terminal(task_id: str, terminal_id: str):
    session = _owned_live_terminal(task_id, terminal_id)
    session.stop()
    item = get_domain_store().finish_task_terminal(
        terminal_id, state="stopped", exit_code=session.process.poll()
    )
    return _terminal_json(item)
