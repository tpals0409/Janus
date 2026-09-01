"""Janus verifications 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

import json
import threading
from pathlib import Path

from fastapi import APIRouter

from .. import domain as D
from .. import scheduler as scheduler_mod
from .. import shared, verification
from ..shared import (
    _publish_change,
    _verification_commands,
    _verification_workspace,
    get_domain_store,
    get_workspace_service,
)
from ..workspace import WorkspaceContext

router = APIRouter()

@router.get("/tasks/{task_id}/changeset")
def get_task_changeset(task_id: str):
    store = get_domain_store()
    task = store.get_task(task_id)
    workspace = store.get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    if workspace["state"] != "ready" or not workspace.get("root_path"):
        raise D.Conflict("ready Workspace가 있어야 ChangeSet을 읽을 수 있습니다")
    return get_workspace_service().changeset(
        repo_path=workspace["repo_path"],
        root_path=workspace["root_path"],
        base_ref=task["base_ref"],
    )



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
        # 실행 중에 워크스페이스가 바뀌었으면 이 결과가 어느 리비전의 것인지
        # 말할 수 없다. 통과로 기록하면 "검증된 변경"이라는 계약이 깨진다 —
        # 에이전트가 검증을 걸어놓고 그 사이 파일을 고치는 경로가 여기다.
        try:
            _, _, after = _verification_workspace(item["task_id"])
            drifted = after["revision"] != item["revision"]
        except Exception:
            drifted = False
            after = None
        if drifted and not result.get("error"):
            result = {
                **result,
                "error": (
                    "검증 실행 중 작업 공간이 바뀌어 결과를 이 리비전에 귀속할 수 "
                    f"없습니다 (시작 {item['revision'][:12]} → 종료 "
                    f"{after['revision'][:12]}). 변경을 멈추고 다시 검증하세요."
                ),
            }
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
        with shared._VERIFICATION_JOBS_LOCK:
            if shared._VERIFICATION_JOBS.get(run_id) is threading.current_thread():
                shared._VERIFICATION_JOBS.pop(run_id, None)



def _start_verification_job(run_id: str) -> None:
    with shared._VERIFICATION_JOBS_LOCK:
        thread = threading.Thread(
            target=_run_verification_job, args=(run_id,),
            name=f"janus-verification-{run_id}", daemon=True,
        )
        shared._VERIFICATION_JOBS[run_id] = thread
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



@router.get("/tasks/{task_id}/verifications")
def list_task_verifications(task_id: str):
    get_domain_store().get_task(task_id)
    return get_domain_store().list_verification_runs(task_id)



@router.post("/tasks/{task_id}/verifications", status_code=202)
def run_task_verifications(task_id: str, body: dict):
    return _create_verification_runs(task_id, body)



@router.post("/verifications/{run_id}/rerun", status_code=202)
def rerun_verification(run_id: str):
    previous = get_domain_store().get_verification_run(run_id)
    return _create_verification_runs(previous["task_id"], {
        "commands": [{"kind": previous["kind"], "command": previous["command"]}],
        "trigger": "manual",
    })[0]
