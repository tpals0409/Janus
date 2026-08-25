"""Janus reviews 라우터 — server.py에서 분리되었다."""

from __future__ import annotations

from fastapi import APIRouter

from .. import domain as D
from ..server import (
    _review_snapshot,
    get_domain_store,
    get_workspace_service,
)

router = APIRouter()

@router.get("/tasks/{task_id}/review")
def get_task_review(task_id: str):
    task, _workspace, changes = _review_snapshot(task_id)
    store = get_domain_store()
    return {
        "task_status": task["status"], "revision": changes["revision"],
        "unmerged": changes["unmerged"],
        "comments": store.list_review_comments(task_id),
        "decisions": store.list_review_decisions(task_id),
    }



@router.post("/tasks/{task_id}/review/comments")
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



@router.patch("/review/comments/{comment_id}")
def resolve_task_review_comment(comment_id: str, body: dict):
    return get_domain_store().resolve_review_comment(
        comment_id, resolved=bool(body.get("resolved", True))
    )



@router.post("/tasks/{task_id}/review/decision")
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
