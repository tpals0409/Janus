"""Janus projects 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

import json
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import domain as D
from .. import shared
from .. import tools as T
from ..shared import (
    _agent_profile_json,
    _delegation_base_ref,
    _evaluation_comparison_json,
    _learning_json,
    _verification_commands,
    get_domain_store,
)
from ..workspace import WorkspaceContext

router = APIRouter()

def _project_json(project: dict) -> dict:
    value = dict(project)
    raw = value.pop("verification_commands_json", "[]")
    value["verification_commands"] = json.loads(raw)
    return value



@router.get("/projects")
def list_projects(include_archived: bool = False):
    return [
        _project_json(item)
        for item in get_domain_store().list_projects(include_archived=include_archived)
    ]



@router.post("/projects")
def create_project(body: dict):
    return _project_json(get_domain_store().create_project(
        name=str(body.get("name") or ""), repo_path=str(body.get("repo_path") or "")
    ))



@router.get("/projects/{project_id}")
def get_project(project_id: str):
    return _project_json(get_domain_store().get_project(project_id))



def _project_workspace_context(project_id: str) -> WorkspaceContext:
    project = get_domain_store().get_project(project_id)
    try:
        return WorkspaceContext(
            root=Path(project["repo_path"]),
            task_id=f"project_{project_id}",
            workspace_id=f"project_{project_id}",
        )
    except ValueError as error:
        raise HTTPException(404, str(error)) from error



@router.get("/projects/{project_id}/tree")
def project_tree(project_id: str, path: str = ""):
    """선택한 프로젝트 디렉토리의 한 계층을 IDE 탐색기용으로 반환한다."""
    context = _project_workspace_context(project_id)
    try:
        root = T._resolve(path or ".", context)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not root.is_dir():
        raise HTTPException(404, f"디렉토리가 아님: {path}")
    entries = []
    for item in sorted(root.iterdir(), key=lambda item: (item.is_file(), item.name.lower())):
        if item.name in shared._IGNORE or item.name.startswith("."):
            continue
        entries.append({
            "name": item.name,
            "type": "dir" if item.is_dir() else "file",
            "size": item.stat().st_size if item.is_file() else None,
        })
        if len(entries) >= 500:
            break
    return {"path": path, "entries": entries}



@router.get("/projects/{project_id}/file")
def project_file(project_id: str, path: str):
    context = _project_workspace_context(project_id)
    try:
        item = T._resolve(path, context)
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    if not item.is_file():
        raise HTTPException(404, f"파일 없음: {path}")
    if item.stat().st_size > 1_000_000:
        return {"path": path, "content": None, "error": "1MB 초과 — 뷰어로 열기엔 너무 큼"}
    raw = item.read_bytes()
    if b"\x00" in raw[:8192]:
        return {"path": path, "content": None, "error": "바이너리 파일"}
    return {
        "path": path,
        "content": raw.decode("utf-8", errors="replace"),
        "error": None,
    }



@router.post("/projects/{project_id}/agent-profile/promote")
def promote_project_agent_profile(project_id: str, body: dict):
    store = get_domain_store()
    comparison = _evaluation_comparison_json(
        store.get_evaluation_comparison(str(body.get("comparison_id") or ""))
    )
    project = store.promote_project_agent_profile(
        project_id, comparison_id=comparison["id"]
    )
    profile_id = project["default_agent_profile_id"]
    return {
        "project": _project_json(project),
        "agent_profile": _agent_profile_json(store.get_agent_profile(profile_id)),
        "comparison_id": comparison["id"],
        "verdict": comparison["result"].get("verdict"),
    }



@router.put("/projects/{project_id}/verification-commands")
def set_project_verification_commands(project_id: str, body: dict):
    commands = _verification_commands(body.get("commands"))
    return _project_json(
        get_domain_store().set_project_verification_commands(project_id, commands)
    )



@router.delete("/projects/{project_id}")
def archive_project(project_id: str):
    return get_domain_store().archive_project(project_id)



@router.get("/projects/{project_id}/tasks")
def list_project_tasks(project_id: str, include_archived: bool = False):
    get_domain_store().get_project(project_id)
    return get_domain_store().list_tasks(project_id, include_archived=include_archived)



@router.post("/projects/{project_id}/tasks")
def create_task(project_id: str, body: dict):
    return get_domain_store().create_task(
        project_id=project_id,
        title=str(body.get("title") or ""),
        objective=str(body.get("objective") or ""),
        acceptance_command=str(body.get("acceptance_command") or ""),
        base_ref=str(body.get("base_ref") or "main"),
        workflow_stage=str(body.get("workflow_stage") or "direct"),
    )



def _delegation_title(objective: str) -> str:
    first_line = " ".join(objective.strip().splitlines()[0].split())
    title = re.split(r"[.!?]+(?:\s|$)", first_line, maxsplit=1)[0].strip(" '\"‘’“”")
    title = re.sub(r"^(?:일단|우선|이제)\s+", "", title)
    title = re.sub(
        r"\s*(?:진행하자|확인해줄래|확인해봐|확인해줘|말해줘|해볼래|해봐|해줘|해주세요|하자)[.?!]*$",
        "", title,
    ).strip()
    title = re.sub(r"(?:을|를)$", "", title).strip()
    if not title:
        title = first_line
    return title if len(title) <= 36 else f"{title[:35]}…"



def _delegation_acceptance(project: dict, repo: Path) -> str:
    configured = json.loads(project.get("verification_commands_json") or "[]")
    for preferred in ("acceptance", "test", "typecheck", "lint", "custom"):
        match = next((item for item in configured if item.get("kind") == preferred), None)
        if match and str(match.get("command") or "").strip():
            return str(match["command"]).strip()

    package = repo / "package.json"
    if package.is_file():
        try:
            scripts = json.loads(package.read_text(encoding="utf-8")).get("scripts") or {}
        except (OSError, json.JSONDecodeError):
            scripts = {}
        if scripts.get("test"):
            if (repo / "pnpm-lock.yaml").is_file():
                return "pnpm test"
            if (repo / "yarn.lock").is_file():
                return "yarn test"
            return "npm test"
        if scripts.get("typecheck"):
            manager = "pnpm" if (repo / "pnpm-lock.yaml").is_file() else "npm run"
            return f"{manager} typecheck"
    if (repo / "pyproject.toml").is_file():
        return "uv run pytest -q" if (repo / "uv.lock").is_file() else "python -m pytest -q"
    if (repo / "Cargo.toml").is_file():
        return "cargo test"
    if (repo / "go.mod").is_file():
        return "go test ./..."
    return "git diff --check"



@router.post("/projects/{project_id}/delegations")
def delegate_project_work(project_id: str, body: dict):
    objective = str(body.get("objective") or "").strip()
    if not objective:
        raise D.Conflict("위임할 목표를 입력하세요")
    if len(objective) > 12_000:
        raise D.Conflict("위임 목표는 12,000자 이하여야 합니다")
    store = get_domain_store()
    project = store.get_project(project_id)
    repo = Path(project["repo_path"]).resolve()
    return store.create_task(
        project_id=project_id,
        title=_delegation_title(objective),
        objective=objective,
        acceptance_command=_delegation_acceptance(project, repo),
        base_ref=_delegation_base_ref(repo),
        workflow_stage=str(body.get("workflow_stage") or "direct"),
    )



@router.get("/projects/{project_id}/learnings")
def list_project_learnings(project_id: str, include_inactive: bool = True):
    return [
        _learning_json(item) for item in get_domain_store().list_project_learnings(
            project_id, active_only=not include_inactive,
        )
    ]



@router.patch("/projects/{project_id}/learnings/{learning_id}")
def update_project_learning(project_id: str, learning_id: str, body: dict):
    store = get_domain_store()
    item = store.get_project_learning(learning_id)
    if item["project_id"] != project_id:
        raise D.NotFound(f"없는 ProjectLearning: {learning_id}")
    return _learning_json(store.set_project_learning_status(
        learning_id, str(body.get("status") or ""),
    ))
