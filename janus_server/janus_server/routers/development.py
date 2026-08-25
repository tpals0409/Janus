"""Janus development 라우터 — server.py에서 분리되었다."""

from __future__ import annotations

import os
import subprocess
import uuid
from contextlib import suppress
from pathlib import Path

from fastapi import APIRouter

from .. import domain as D
from ..server import (
    get_domain_store,
)

router = APIRouter()

def _task_development_root(task_id: str) -> tuple[dict, Path]:
    workspace = get_domain_store().get_task_workspace(task_id)
    if workspace is None or workspace["state"] != "ready" or not workspace["root_path"]:
        raise D.Conflict("ready Task workspace가 필요합니다")
    root = Path(workspace["root_path"]).resolve()
    if not root.is_dir():
        raise D.Conflict("Task workspace 경로가 없습니다")
    return workspace, root



def _task_development_path(task_id: str, value: str) -> tuple[dict, Path, Path]:
    workspace, root = _task_development_root(task_id)
    relative = Path(str(value or "."))
    if relative.is_absolute():
        raise D.Conflict("Task file path는 상대경로여야 합니다")
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise D.Conflict("Task workspace 밖의 file path입니다")
    return workspace, root, target



@router.get("/tasks/{task_id}/development/files")
def list_task_development_files(task_id: str, path: str = "."):
    _workspace, root, target = _task_development_path(task_id, path)
    if not target.is_dir():
        raise D.NotFound(f"디렉토리가 없습니다: {path}")
    entries = []
    for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
        if child.name == ".git":
            continue
        relative = str(child.relative_to(root))
        entries.append({
            "name": child.name, "path": relative,
            "type": "directory" if child.is_dir() else "file",
            "size": child.stat().st_size if child.is_file() else None,
        })
        if len(entries) >= 500:
            break
    return {"path": "" if path == "." else path, "entries": entries, "truncated": len(entries) >= 500}



@router.get("/tasks/{task_id}/development/file")
def read_task_development_file(task_id: str, path: str, rev: str = "worktree"):
    _workspace, root, target = _task_development_path(task_id, path)
    if rev in {"head", "index"}:
        # diff 컨텍스트 펼치기용 — 레이어에 맞는 판본을 준다 (committed→head, staged→index).
        spec = f"HEAD:{target.relative_to(root).as_posix()}" if rev == "head" \
            else f":0:{target.relative_to(root).as_posix()}"
        result = subprocess.run(
            ["git", "-C", str(root), "show", spec],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            raise D.NotFound(f"{rev}에 파일이 없습니다: {path}")
        raw = result.stdout
        if len(raw) > 2_000_000:
            raise D.Conflict("editor는 2MB 이하 text file만 엽니다")
        if b"\0" in raw[:8192]:
            raise D.Conflict("binary file은 editor에서 열 수 없습니다")
        return {
            "path": str(target.relative_to(root)),
            "content": raw.decode("utf-8", errors="replace"),
            "size": len(raw), "mtime_ns": None,
        }
    if rev != "worktree":
        raise D.Conflict("rev는 worktree/head/index 중 하나여야 합니다")
    if not target.is_file():
        raise D.NotFound(f"파일이 없습니다: {path}")
    size = target.stat().st_size
    if size > 2_000_000:
        raise D.Conflict("editor는 2MB 이하 text file만 엽니다")
    raw = target.read_bytes()
    if b"\0" in raw[:8192]:
        raise D.Conflict("binary file은 editor에서 열 수 없습니다")
    return {
        "path": str(target.relative_to(root)), "content": raw.decode("utf-8", errors="replace"),
        "size": size, "mtime_ns": target.stat().st_mtime_ns,
    }



@router.put("/tasks/{task_id}/development/file")
def write_task_development_file(task_id: str, body: dict):
    path = str(body.get("path") or "")
    content = body.get("content")
    if not path or not isinstance(content, str):
        raise D.Conflict("path와 text content가 필요합니다")
    encoded = content.encode("utf-8")
    if len(encoded) > 2_000_000:
        raise D.Conflict("editor는 2MB 이하 text file만 저장합니다")
    _workspace, root, target = _task_development_path(task_id, path)
    expected_mtime = body.get("expected_mtime_ns")
    if expected_mtime is not None and target.exists() and target.stat().st_mtime_ns != int(expected_mtime):
        raise D.Conflict("파일이 외부에서 변경됐습니다. 다시 열고 수정하세요")
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.janus-{uuid.uuid4().hex}.tmp")
    try:
        temporary.write_bytes(encoded)
        os.replace(temporary, target)
    except OSError as error:
        with suppress(OSError):
            temporary.unlink()
        raise D.Conflict(f"파일 저장 실패: {error}") from error
    return {
        "path": str(target.relative_to(root)), "saved": True,
        "size": len(encoded), "mtime_ns": target.stat().st_mtime_ns,
    }



@router.get("/tasks/{task_id}/development/search")
def search_task_development_files(task_id: str, q: str):
    _workspace, root = _task_development_root(task_id)
    query = str(q).strip()
    if not query or len(query) > 200:
        raise D.Conflict("검색어는 1~200자여야 합니다")
    try:
        completed = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--color=never", "--fixed-strings",
             "--glob", "!.git/**", "--max-count", "50", query, "."],
            cwd=root, capture_output=True, text=True, timeout=15,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        raise D.Conflict(f"file search 실패: {error}") from error
    if completed.returncode not in {0, 1}:
        raise D.Conflict(completed.stderr.strip() or "file search 실패")
    matches = []
    for line in completed.stdout.splitlines()[:500]:
        file_path, separator, rest = line.removeprefix("./").partition(":")
        line_number, separator2, text_value = rest.partition(":")
        if separator and separator2:
            matches.append({
                "path": file_path, "line": int(line_number), "text": text_value[:1000],
            })
    return {"query": query, "matches": matches, "truncated": len(completed.stdout.splitlines()) > 500}
