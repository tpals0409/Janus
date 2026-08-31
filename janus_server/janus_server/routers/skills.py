"""Janus skills 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

import json
import urllib.error
from pathlib import Path

from fastapi import APIRouter, HTTPException

from .. import domain as D
from .. import skills as skill_mod
from ..shared import (
    _skill_json,
    _skill_summary,
    get_domain_store,
)

router = APIRouter()

def _store_skill_artifact(artifact: dict) -> dict:
    return _skill_json(get_domain_store().import_skill_version(**artifact))



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
        imported = [
            _store_skill_artifact(artifact)
            for artifact in skill_mod.local_artifacts(
                str(root),
                source_kind=body.get("source_kind"),
                namespace=body.get("namespace"),
            )
        ]
        return {"source": str(root), "skills": imported}
    except skill_mod.SkillImportError as error:
        raise HTTPException(400, str(error)) from error



_github_skill_artifacts = skill_mod.github_artifacts


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
