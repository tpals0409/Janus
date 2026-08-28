"""Janus workspaces 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

import threading
from pathlib import Path

from fastapi import APIRouter

from .. import domain as D
from .. import shared
from ..shared import (
    _publish_change,
    _workspace_job_active,
    get_domain_store,
    get_workspace_service,
)

router = APIRouter()

def _run_workspace_preparation(workspace_id: str) -> None:
    store = get_domain_store()
    try:
        workspace = store.get_workspace(workspace_id)
        task = store.get_task(workspace["task_id"])

        store.update_workspace_preparation(workspace_id, progress="validating")

        def report(stage: str, details: dict) -> None:
            store.update_workspace_preparation(workspace_id, progress=stage)
            _publish_change(
                "workspace", "progress", workspace_id=workspace_id,
                task_id=workspace["task_id"], progress=stage, **details,
            )

        # Task마다 전용 worktree와 janus/ branch를 받는다. 사용자의 체크아웃에서
        # 직접 작업하면 에이전트 실패가 그들의 작업 트리를 오염시키고, 되돌릴 경계가
        # git뿐이다. 0d53440이 워크플로 엔진을 지우며 이 배선을 함께 끊었다.
        prepared = get_workspace_service().prepare(
            workspace_id=workspace_id,
            task_id=workspace["task_id"],
            title=task["title"],
            repo_path=workspace["repo_path"],
            base_ref=workspace["base_ref"],
            existing_root=workspace["root_path"] if workspace["owned"] else None,
            existing_branch=workspace["branch_name"] if workspace["owned"] else None,
            progress=report,
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
        with shared._WORKSPACE_JOBS_LOCK:
            if shared._WORKSPACE_JOBS.get(workspace_id) is threading.current_thread():
                shared._WORKSPACE_JOBS.pop(workspace_id, None)



def _start_workspace_preparation(workspace_id: str) -> None:
    with shared._WORKSPACE_JOBS_LOCK:
        existing = shared._WORKSPACE_JOBS.get(workspace_id)
        if existing is not None and existing.is_alive():
            raise D.Conflict(f"Workspace 준비가 이미 진행 중입니다: {workspace_id}")
        thread = threading.Thread(
            target=_run_workspace_preparation,
            args=(workspace_id,),
            name=f"janus-workspace-{workspace_id}",
            daemon=True,
        )
        shared._WORKSPACE_JOBS[workspace_id] = thread
        thread.start()



@router.get("/tasks/{task_id}/workspace")
def get_task_workspace(task_id: str):
    get_domain_store().get_task(task_id)
    workspace = get_domain_store().get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    return {**workspace, "job_active": _workspace_job_active(workspace["id"])}



@router.post("/tasks/{task_id}/workspace/prepare", status_code=202)
def prepare_task_workspace(task_id: str):
    store = get_domain_store()
    task = store.get_task(task_id)
    if store.get_task_workspace(task_id) is not None:
        raise D.Conflict("Workspace가 이미 있습니다. failed면 retry를 사용하세요.")
    project = store.get_project(task["project_id"])
    workspace = store.create_workspace(
        task_id=task_id, repo_path=project["repo_path"], base_ref=task["base_ref"],
    )
    store.transition_task(task_id, "preparing", expected="todo")
    _start_workspace_preparation(workspace["id"])
    return {
        **store.get_workspace(workspace["id"]),
        "job_active": _workspace_job_active(workspace["id"]),
    }



@router.post("/tasks/{task_id}/workspace/retry", status_code=202)
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



@router.get("/tasks/{task_id}/workspace/status")
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



@router.post("/tasks/{task_id}/workspace/commit")
def commit_workspace_changes(task_id: str, body: dict):
    """변경사항 패널의 직접 commit — 리뷰 게이트 없는 수동 git commit.

    review 수락을 요구하는 ship/commit과 별개의 편의 경로다. 안전장치는
    service.commit_changes가 그대로 강제한다(빈 메시지·빈 커밋·unmerged·
    detached HEAD 거부). 성공은 shipment로 기록해 push 게이트("Janus가 기록한
    현재 commit")와 정합을 유지한다.
    """
    store = get_domain_store()
    store.get_task(task_id)
    workspace = store.get_task_workspace(task_id)
    if workspace is None:
        raise D.NotFound(f"Task의 Workspace가 없습니다: {task_id}")
    if workspace["state"] != "ready" or not workspace.get("root_path"):
        raise D.Conflict("ready Workspace가 있어야 commit할 수 있습니다")
    result = get_workspace_service().commit_changes(
        repo_path=workspace["repo_path"], root_path=workspace["root_path"],
        message=str(body.get("message") or ""),
    )
    shipment = store.record_task_shipment(
        task_id=task_id, action="commit", commit_sha=result["commit_sha"],
        branch_name=result["branch_name"],
    )
    return {"result": result, "shipment": shipment}



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



@router.post("/tasks/{task_id}/workspace/archive")
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



@router.delete("/tasks/{task_id}/workspace/force")
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



@router.delete("/tasks/{task_id}/workspace/branch")
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
