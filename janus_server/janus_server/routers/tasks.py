"""Janus tasks 라우터 — server.py에서 분리되었다."""

from __future__ import annotations

from fastapi import APIRouter

from .. import domain as D
from .. import workspace_service as WS
from ..server import (
    _dispatch_json,
    _workspace_job_active,
    get_domain_store,
    get_workspace_service,
)

router = APIRouter()

@router.get("/tasks/{task_id}")
def get_task(task_id: str):
    task = get_domain_store().get_task(task_id)
    task["workspace"] = get_domain_store().get_task_workspace(task_id)
    task["dispatches"] = [
        _dispatch_json(item) for item in get_domain_store().list_dispatches(task_id)
    ]
    return task



@router.patch("/tasks/{task_id}")
def update_task(task_id: str, body: dict):
    fields = {
        key: str(body[key])
        for key in ("title", "objective", "acceptance_command", "base_ref")
        if key in body
    }
    return get_domain_store().update_task(task_id, **fields)



@router.post("/tasks/{task_id}/transition")
def transition_task(task_id: str, body: dict):
    return get_domain_store().transition_task(
        task_id, str(body.get("status") or ""),
        expected=str(body["expected"]) if body.get("expected") is not None else None,
    )



@router.post("/tasks/{task_id}/mockup/approve")
def approve_task_mockup(task_id: str):
    store = get_domain_store()
    _require_mockup_review_boundary(store, task_id)
    return store.approve_task_mockup(task_id)



@router.post("/tasks/{task_id}/mockup/reject")
def reject_task_mockup(task_id: str, body: dict):
    store = get_domain_store()
    _require_mockup_review_boundary(store, task_id)
    return store.reject_task_mockup(
        task_id, str(body.get("feedback") or ""),
    )



def _require_mockup_review_boundary(store: D.DomainStore, task_id: str) -> None:
    task = store.get_task(task_id)
    if task["workflow_stage"] != "mockup" or task["status"] != "needs_you":
        raise D.Conflict("목업 검토 대기 상태에서만 승인하거나 거절할 수 있습니다")
    dispatch = store.latest_dispatch(task_id)
    if dispatch is None or dispatch["status"] != "needs_you":
        raise D.Conflict("목업 검토를 요청한 최신 Dispatch가 없습니다")
    sessions = store.list_sessions(task_id)
    if (
        not sessions
        or sessions[0]["dispatch_id"] != dispatch["id"]
        or sessions[0]["status"] != "idle"
    ):
        raise D.Conflict("목업 검토를 요청한 AgentSession이 대기 중이 아닙니다")



@router.delete("/tasks/{task_id}")
def archive_task(task_id: str):
    """Task를 목록에서 감춘다. 원본 저장소와 작업 내용은 건드리지 않는다."""
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
