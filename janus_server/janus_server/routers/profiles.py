"""Janus profiles 라우터 — server.py에서 분리되었다."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..server import (
    _agent_profile_json,
    _model_profile_json,
    _pin_library_skills,
    _skill_summary,
    get_domain_store,
)

router = APIRouter()

@router.get("/profiles/agents/{profile_id}/skills")
def list_profile_skills(profile_id: str):
    return [
        _skill_summary(item)
        for item in get_domain_store().list_agent_profile_skills(profile_id)
    ]



@router.put("/profiles/agents/{profile_id}/skills/{skill_id}")
def set_profile_skill(profile_id: str, skill_id: str, body: dict):
    try:
        return _skill_summary(get_domain_store().set_agent_profile_skill(
            agent_profile_id=profile_id,
            skill_id=skill_id,
            skill_version_id=(str(body["skill_version_id"]) if body.get("skill_version_id") else None),
            activation_mode=str(body.get("activation_mode") or "off"),
            priority=int(body.get("priority") or 0),
        ))
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error



@router.get("/profiles/models")
def list_model_profiles():
    return [_model_profile_json(item) for item in get_domain_store().list_model_profiles()]



@router.get("/profiles/agents")
def list_agent_profiles():
    return [_agent_profile_json(item) for item in get_domain_store().list_agent_profiles()]



@router.post("/profiles/agents")
def create_agent_profile(body: dict):
    try:
        profile = get_domain_store().create_agent_profile(
            name=str(body.get("name") or ""),
            description=str(body.get("description") or ""),
            system_prompt=str(body.get("system_prompt") or ""),
            tools=[str(item) for item in body.get("tools") or []],
            approval=str(body.get("approval") or "ask"),
            worker_policy=str(body.get("worker_policy") or "autonomous"),
            max_steps=int(body.get("max_steps") or 15),
            model_profile_id=str(body.get("model_profile_id") or "model_qwen38_27b_4bit"),
            budget=body.get("budget") if isinstance(body.get("budget"), dict) else None,
            context_policy=(
                body.get("context_policy") if isinstance(body.get("context_policy"), dict) else None
            ),
        )
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    _pin_library_skills(get_domain_store(), profile["id"])
    return _agent_profile_json(profile)



@router.put("/profiles/agents/{profile_id}")
def update_agent_profile(profile_id: str, body: dict):
    try:
        profile = get_domain_store().update_agent_profile(profile_id, **body)
    except (TypeError, ValueError) as error:
        raise HTTPException(400, str(error)) from error
    return _agent_profile_json(profile)
