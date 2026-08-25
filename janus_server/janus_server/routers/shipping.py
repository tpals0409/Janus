"""Janus shipping 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

import json
import shlex

from fastapi import APIRouter

from .. import domain as D
from .. import github_service as github_mod
from .. import workspace_service as WS
from ..shared import (
    _review_snapshot,
    get_domain_store,
    get_github_service,
    get_workspace_service,
)

router = APIRouter()

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



@router.get("/tasks/{task_id}/shipments")
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



@router.post("/tasks/{task_id}/ship/commit")
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



@router.post("/tasks/{task_id}/ship/push")
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



@router.get("/tasks/{task_id}/pull-request")
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



@router.post("/tasks/{task_id}/pull-request")
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



@router.post("/tasks/{task_id}/pull-request/refresh")
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



@router.get("/tasks/{task_id}/ship/handoff")
def task_ship_handoff(task_id: str):
    _task, workspace, _changes = _review_snapshot(task_id)
    _shipment, current = _committed_shipment(task_id, workspace)
    head = current["commit_sha"]
    repo = shlex.quote(str(workspace["repo_path"]))
    branch = shlex.quote(str(workspace["branch_name"]))
    return {
        "executed": True,
        "commit_sha": head,
        "branch_name": workspace["branch_name"],
        "local_apply_command": None,
        "push_command": f"git -C {repo} push origin {branch}",
        "notice": "The commit already exists in the project checkout; no cherry-pick is required.",
    }
