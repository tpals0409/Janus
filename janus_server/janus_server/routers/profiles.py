"""Janus profiles 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import cli_runner
from ..shared import (
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



@router.put("/profiles/models/{profile_id}/config")
def set_model_profile_config(profile_id: str, body: dict):
    """구독형 실행기의 모델·사고 강도 선택.

    effort 어휘는 CLI마다 다르므로 해당 provider가 아는 값만 받는다 — 모르는 값을
    저장해 두면 실행 시점에 CLI 인자 오류로 턴이 통째로 실패한다.
    """
    store = get_domain_store()
    profile = store.get_model_profile(profile_id)
    provider = str(profile["provider"])
    if not cli_runner.is_cli_provider(provider):
        raise HTTPException(400, "구독형 ModelProfile에만 설정할 수 있습니다")
    model = str(body.get("model") or "").strip()
    effort = str(body.get("effort") or "").strip().lower()
    if len(model) > 100:
        raise HTTPException(400, "model 이름이 너무 깁니다")
    allowed = cli_runner.CLI_EFFORTS[provider]
    if effort and effort not in allowed:
        raise HTTPException(400, f"{provider}가 모르는 effort입니다: {sorted(allowed)}")
    config = {key: value for key, value in
              (("model", model), ("effort", effort)) if value}
    return _model_profile_json(store.update_model_profile_config(profile_id, config))



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
