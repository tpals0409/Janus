"""Janus skills 라우터 — server.py에서 분리되었다."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import urllib.error
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import domain as D
from .. import skills as skill_mod
from ..server import (
    _skill_json,
    _skill_summary,
    get_domain_store,
)

router = APIRouter()

def _store_skill_artifact(artifact: dict) -> dict:
    return _skill_json(get_domain_store().import_skill_version(**artifact))



def _local_source_kind(path: Path, requested: object) -> str:
    kind = str(requested or "").strip().lower()
    if kind in {"codex", "claude", "local", "project"}:
        return kind
    lowered = {part.lower() for part in path.parts}
    if ".claude" in lowered:
        return "claude"
    if ".codex" in lowered or ".agents" in lowered:
        return "codex"
    return "local"



def _local_skill_namespace(root: Path, kind: str, requested: object) -> str:
    if requested is not None and str(requested).strip():
        return str(requested).strip()
    generic = {"skills", ".skills", ".codex", ".claude", ".agents"}
    label = root.name if root.name.lower() not in generic else root.parent.name
    slug = re.sub(r"[^a-z0-9._-]+", "-", label.lower()).strip("-._") or "source"
    digest = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:8]
    return f"{kind}-{slug[:70]}-{digest}"



@router.get("/skills")
def list_skills(include_archived: bool = False):
    return [
        _skill_summary(item)
        for item in get_domain_store().list_skills(include_archived=include_archived)
    ]



@router.get("/skills/{skill_id}")
def get_skill(skill_id: str):
    return _skill_json(get_domain_store().get_skill(skill_id))



@router.post("/skills/import/local")
def import_local_skills(body: dict):
    raw_path = str(body.get("path") or "").strip()
    if not raw_path:
        raise HTTPException(400, "path가 필요합니다")
    root = Path(raw_path).expanduser().resolve()
    try:
        directories = skill_mod.discover_skill_directories(root)
        if not directories:
            raise skill_mod.SkillImportError("선택한 폴더에서 SKILL.md를 찾지 못했습니다")
        kind = _local_source_kind(root, body.get("source_kind"))
        namespace = _local_skill_namespace(root, kind, body.get("namespace"))
        imported = []
        for directory in directories:
            relative = directory.relative_to(root).as_posix()
            artifact = skill_mod.compile_skill_directory(
                directory,
                source_kind=kind,
                source_locator=str(root),
                source_subpath="" if relative == "." else relative,
                namespace=namespace,
            )
            imported.append(_store_skill_artifact(artifact))
        return {"source": str(root), "skills": imported}
    except skill_mod.SkillImportError as error:
        raise HTTPException(400, str(error)) from error



def _github_skill_artifacts(url: str) -> tuple[dict, list[dict]]:
    with tempfile.TemporaryDirectory(prefix="janus-skill-github-") as temporary:
        source = skill_mod.download_github_skills(url, temporary)
        raw_namespace = f"github-{source['owner']}-{source['repository']}".lower()
        namespace = (
            raw_namespace if len(raw_namespace) <= 100
            else f"{raw_namespace[:87]}-{hashlib.sha256(raw_namespace.encode()).hexdigest()[:12]}"
        )
        artifacts = []
        for directory in source["skill_directories"]:
            relative = directory.relative_to(source["root"]).as_posix()
            prefix = str(source.get("subpath") or "").strip("/")
            subpath = "/".join(item for item in (prefix, relative) if item and item != ".")
            artifact = skill_mod.compile_skill_directory(
                directory,
                source_kind="github",
                source_locator=source["canonical_url"],
                source_subpath=subpath,
                source_revision=source["revision"],
                namespace=namespace,
            )
            artifact["original"]["github"] = {
                "owner": source["owner"],
                "repository": source["repository"],
                "revision": source["revision"],
                "requested_ref": source["requested_ref"],
                "license": source["license"],
            }
            artifacts.append(artifact)
        return {
            "source": source["canonical_url"],
            "revision": source["revision"],
            "license": source["license"],
        }, artifacts



def _skill_artifact_preview(artifact: dict) -> dict:
    compiled = artifact["compiled"]
    return {
        key: artifact[key]
        for key in (
            "namespace", "name", "description", "source_kind", "source_locator",
            "source_subpath", "source_revision", "content_hash", "compatibility",
        )
    } | {
        "compiled": {
            key: compiled.get(key)
            for key in ("format", "activation", "execution", "capabilities")
        },
        "report": artifact["report"],
    }



@router.post("/skills/preview/github")
def preview_github_skills(body: dict):
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url이 필요합니다")
    if len(url) > 2_048:
        raise HTTPException(400, "GitHub URL이 너무 깁니다")
    try:
        metadata, artifacts = _github_skill_artifacts(url)
        return {**metadata, "url": url, "skills": [
            _skill_artifact_preview(artifact) for artifact in artifacts
        ]}
    except skill_mod.SkillImportError as error:
        raise HTTPException(400, str(error)) from error
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise HTTPException(502, f"GitHub 스킬을 가져올 수 없습니다: {error}") from error



@router.post("/skills/import/github")
def import_github_skills(body: dict):
    url = str(body.get("url") or "").strip()
    if not url:
        raise HTTPException(400, "url이 필요합니다")
    if len(url) > 2_048:
        raise HTTPException(400, "GitHub URL이 너무 깁니다")
    selected = body.get("selected_subpaths")
    if selected is not None and (
        not isinstance(selected, list)
        or not selected
        or len(selected) > skill_mod.MAX_SKILL_FILES
        or not all(isinstance(item, str) for item in selected)
        or any(not item or len(item) > 1_024 for item in selected)
        or len(selected) != len(set(selected))
    ):
        raise HTTPException(400, "selected_subpaths는 중복 없는 하나 이상의 문자열 배열이어야 합니다")
    try:
        metadata, artifacts = _github_skill_artifacts(url)
        expected_revision = str(body.get("expected_revision") or "")
        if expected_revision and expected_revision != metadata["revision"]:
            raise D.Conflict(
                f"미리보기 이후 GitHub revision이 변경됐습니다: "
                f"expected={expected_revision}, actual={metadata['revision']}"
            )
        if selected is not None:
            requested = set(selected)
            available = {artifact["source_subpath"] for artifact in artifacts}
            unknown = sorted(requested - available)
            if unknown:
                raise D.Conflict(f"미리보기에 없던 스킬 경로입니다: {unknown}")
            artifacts = [
                artifact for artifact in artifacts if artifact["source_subpath"] in requested
            ]
        return {
            **metadata,
            "skills": [_store_skill_artifact(artifact) for artifact in artifacts],
        }
    except skill_mod.SkillImportError as error:
        raise HTTPException(400, str(error)) from error
    except (OSError, urllib.error.URLError, json.JSONDecodeError) as error:
        raise HTTPException(502, f"GitHub 스킬을 가져올 수 없습니다: {error}") from error
