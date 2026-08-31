"""대화 세션 안에서 스킬을 만들고 가져오는 경로.

설정 화면의 import와 같은 컴파일·저장 파이프라인을 쓴다 — 채팅에서 만든 스킬도
frontmatter 검증, capability 매핑, 버전 관리를 똑같이 받는다는 뜻이다. 이 모듈만
저장소와 실행 중인 오케스트레이션을 함께 안다: 도구 레이어(tools.py)는 여전히
저장소를 모르고, 런타임은 여전히 세션 스킬 목록의 주인으로 남는다.

세션에서 만든 스킬의 좌표:
  source_kind='project', namespace='project', source_locator='janus-session:<name>'
이름이 같으면 같은 source_key라 새 버전이 된다 — 같은 스킬을 고쳐 부르는 일이
충돌이 아니라 개정으로 기록된다.
"""

from __future__ import annotations

import re
import tempfile
from pathlib import Path

from . import skills as skill_mod

NAMESPACE = "project"
SOURCE_KIND = "project"
ACTIVATION_MODES = ("auto", "manual", "off")
MAX_INSTRUCTIONS_CHARS = 40_000


def safe_skill_name(value: object) -> str:
    """SKILL.md frontmatter가 받는 슬러그로 정규화한다."""
    slug = re.sub(r"[^a-z0-9-]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:64]


def _skill_markdown(name: str, description: str, instructions: str) -> str:
    # description은 한 줄이어야 frontmatter가 깨지지 않는다.
    summary = " ".join(str(description).split())[:500]
    return f"---\nname: {name}\ndescription: {summary}\n---\n\n{instructions.strip()}\n"


def _store():
    # 지연 import — tools 레이어가 저장소를 끌어오지 않게 한다 (cli_runner와 같은 패턴).
    from .shared import get_domain_store

    return get_domain_store()


def _session_for_dispatch(store, context) -> dict | None:
    dispatch_id = getattr(context, "dispatch_id", None)
    if not dispatch_id:
        return None
    task_id = getattr(context, "task_id", None)
    if not task_id:
        return None
    for session in store.list_sessions(task_id):
        if session.get("dispatch_id") == dispatch_id:
            return session
    return None


def _agent_profile_id(store, context) -> str | None:
    dispatch_id = getattr(context, "dispatch_id", None)
    if not dispatch_id:
        return None
    try:
        return str(store.get_dispatch(dispatch_id)["agent_profile_id"])
    except Exception:
        return None


def _publish_to_live_session(store, context, skill: dict) -> bool:
    """실행 중인 오케스트레이션의 스킬 목록에 새 스킬을 얹는다.

    세션의 스킬 스냅샷은 접속 시점에 고정된다. 그것만 믿으면 방금 만든 스킬을
    쓰려고 앱을 다시 연결해야 한다 — 만든 직후 쓸 수 있어야 대화형으로 만드는
    의미가 있다. 영속 스냅샷은 다음 접속의 sync가 정상 경로로 채운다.
    """
    from . import shared

    session = _session_for_dispatch(store, context)
    if session is None:
        return False
    with shared._TASK_RUNTIMES_LOCK:
        orchestration = shared._TASK_RUNTIMES.get(session["id"])
    snapshots = getattr(orchestration, "skill_snapshots", None)
    if snapshots is None:
        return False
    snapshots.append(skill)
    return True


def _activate(store, context, stored: dict, activation_mode: str) -> dict:
    """새 스킬을 현재 AgentProfile에 붙이고, 살아 있는 세션에도 얹는다.

    import_skill_version은 **버전 행**을 돌려준다: id=skill_version_id,
    skill_id=스킬 머리. 세션 스냅샷 계약은 둘을 따로 요구하므로 여기서 나눈다.
    """
    profile_id = _agent_profile_id(store, context)
    live = False
    if profile_id and activation_mode != "off":
        store.set_agent_profile_skill(
            agent_profile_id=profile_id,
            skill_id=stored["skill_id"],
            skill_version_id=stored["id"],
            activation_mode=activation_mode,
            priority=0,
        )
        live = _publish_to_live_session(store, context, {
            "skill_id": stored["skill_id"],
            "skill_version_id": stored["id"],
            "namespace": stored["namespace"],
            "name": stored["name"],
            "description": stored.get("description") or "",
            "version": stored.get("version") or 1,
            "activation_mode": activation_mode,
            "compatibility": stored.get("compatibility") or "native",
            "compiled": stored.get("compiled") or {},
        })
    return {
        "skill": f"{stored['namespace']}:{stored['name']}",
        "activation_mode": activation_mode,
        "usable_now": live,
        "profile_attached": bool(profile_id) and activation_mode != "off",
    }


def _stored(artifact: dict) -> dict:
    from .shared import _skill_json

    return _skill_json(_store().import_skill_version(**artifact))


def create_skill(
    name: str, description: str, instructions: str,
    activation_mode: str = "manual", *, _context=None,
) -> dict:
    """대화에서 받은 절차를 SKILL.md로 굳혀 라이브러리에 등록한다."""
    slug = safe_skill_name(name)
    if not slug:
        return {"error": "name은 영문 소문자·숫자·하이픈으로 된 이름이어야 합니다"}
    summary = " ".join(str(description or "").split())
    if not summary:
        return {"error": "description이 필요합니다 — 이 스킬을 언제 쓰는지 한 줄로 적으세요"}
    body = str(instructions or "").strip()
    if len(body) < 40:
        return {"error": "instructions가 너무 짧습니다. 실행 가능한 절차를 적으세요"}
    if len(body) > MAX_INSTRUCTIONS_CHARS:
        return {"error": f"instructions가 {MAX_INSTRUCTIONS_CHARS}자를 넘습니다"}
    mode = str(activation_mode or "manual").lower()
    if mode not in ACTIVATION_MODES:
        return {"error": f"activation_mode는 {list(ACTIVATION_MODES)} 중 하나여야 합니다"}

    with tempfile.TemporaryDirectory(prefix="janus-skill-authoring-") as temporary:
        directory = Path(temporary) / slug
        directory.mkdir()
        (directory / "SKILL.md").write_text(
            _skill_markdown(slug, summary, body), encoding="utf-8",
        )
        try:
            artifact = skill_mod.compile_skill_directory(
                directory,
                source_kind=SOURCE_KIND,
                source_locator=f"janus-session:{slug}",
                namespace=NAMESPACE,
            )
        except skill_mod.SkillImportError as error:
            return {"error": f"스킬을 컴파일할 수 없습니다: {error}"}
    stored = _stored(artifact)
    store = _store()
    return {
        "created": True,
        "version": stored.get("version") or 1,
        "warnings": (stored.get("report") or {}).get("warnings") or [],
        **_activate(store, _context, stored, mode),
    }


def import_skill(source: str, activation_mode: str = "manual", *, _context=None) -> dict:
    """GitHub URL 또는 로컬 폴더에서 기존 스킬을 가져온다."""
    locator = str(source or "").strip()
    if not locator:
        return {"error": "source가 필요합니다 (GitHub URL 또는 로컬 폴더 경로)"}
    if len(locator) > 2_048:
        return {"error": "source가 너무 깁니다"}
    mode = str(activation_mode or "manual").lower()
    if mode not in ACTIVATION_MODES:
        return {"error": f"activation_mode는 {list(ACTIVATION_MODES)} 중 하나여야 합니다"}

    try:
        if locator.lower().startswith(("http://", "https://", "github.com/")):
            _metadata, artifacts = skill_mod.github_artifacts(locator)
        else:
            artifacts = skill_mod.local_artifacts(locator)
    except skill_mod.SkillImportError as error:
        return {"error": str(error)}
    except Exception as error:  # 네트워크·파일시스템 실패를 턴을 죽이지 않는 오류로
        return {"error": f"스킬을 가져올 수 없습니다: {type(error).__name__}: {error}"}
    if not artifacts:
        return {"error": "가져올 스킬을 찾지 못했습니다 (SKILL.md 없음)"}

    store = _store()
    imported = []
    for artifact in artifacts[:20]:
        stored = _stored(artifact)
        imported.append(_activate(store, _context, stored, mode))
    return {"imported": len(imported), "source": locator, "skills": imported}


def render(value: dict) -> str:
    if value.get("created"):
        state = "바로 사용 가능" if value.get("usable_now") else "다음 접속부터 사용 가능"
        warnings = value.get("warnings") or []
        note = f" · 경고: {'; '.join(warnings)}" if warnings else ""
        return (
            f"스킬 {value['skill']} v{value['version']} 등록됨 "
            f"({value['activation_mode']}, {state}){note}"
        )
    names = ", ".join(item["skill"] for item in value.get("skills") or [])
    return f"{value.get('imported', 0)}개 스킬을 가져왔습니다: {names}"
