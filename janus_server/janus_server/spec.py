"""오케스트레이터 스펙 — YAML 파일 하나 = 에이전트(오케스트레이터) 하나.

그래프가 아니다. 워커는 오케스트레이터가 create_worker로 런타임에 만들고
트레이스에만 존재한다. 스펙은 오케스트레이터의 평평한 설정뿐이다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

APPROVAL_MODES = {"auto", "ask"}
WORKER_POLICIES = {"none", "fixed_one", "autonomous"}


class SpecError(ValueError):
    """스펙이 유효하지 않음. 메시지에 모든 문제를 담는다."""


def load(path: str | Path) -> dict:
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    validate(spec)
    return spec


def dumps(spec: dict) -> str:
    return yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100)


def validate(spec: Any) -> None:
    """문제를 전부 모아 한 번에 던진다. 하나씩 고치게 만들지 않는다."""
    from . import tools as T

    if not isinstance(spec, dict):
        raise SpecError("스펙 최상위는 매핑이어야 합니다")

    errs: list[str] = []

    name = spec.get("name")
    if not isinstance(name, str) or not name.strip():
        errs.append("name이 필요합니다")

    model = spec.get("model")
    if not isinstance(model, str) or not model.strip():
        errs.append("model이 필요합니다 (로컬 모델 이름)")

    tools = spec.get("tools", [])
    if not isinstance(tools, list) or not all(isinstance(t, str) for t in tools):
        errs.append("tools는 도구 이름 리스트여야 합니다")
        tools = []
    if "create_worker" in tools:
        errs.append("create_worker는 항상 주입됩니다 — tools에 적지 마세요")
    unknown = [t for t in tools if t != "create_worker" and t not in T.REGISTRY]
    if unknown:
        errs.append(f"알 수 없는 도구 {unknown} (가능: {sorted(T.REGISTRY)})")

    approval = spec.get("approval", "auto")
    if approval not in APPROVAL_MODES:
        errs.append(f"approval은 {sorted(APPROVAL_MODES)} 중 하나여야 합니다")

    worker_policy = spec.get("worker_policy", "autonomous")
    if worker_policy not in WORKER_POLICIES:
        errs.append(f"worker_policy는 {sorted(WORKER_POLICIES)} 중 하나여야 합니다")
    if not isinstance(spec.get("allow_autonomous_workers", False), bool):
        errs.append("allow_autonomous_workers는 boolean이어야 합니다")

    # 거부 메커니즘이 제거된 모델에 셸/쓰기를 쥐여주는 것이므로 승인이 실제 안전장치다.
    risky = sorted(set(tools) & T.DANGEROUS)
    if risky and approval == "auto":
        errs.append(
            f"{risky} 를 가진 에이전트는 approval: auto 를 쓸 수 없습니다 "
            "(approval: ask 로 바꾸거나 해당 도구를 빼세요)")

    steps = spec.get("max_steps", 15)
    if not isinstance(steps, int) or not (1 <= steps <= 100):
        errs.append("max_steps는 1~100 사이 정수여야 합니다")

    if errs:
        raise SpecError("스펙 오류 %d건:\n  - %s" % (len(errs), "\n  - ".join(errs)))


# ─────────────────────────── self-check ───────────────────────────


def demo():
    good = {"name": "T", "model": "qwen3.8-27b", "system_prompt": "hi",
            "tools": ["grep", "read_file"], "approval": "auto", "max_steps": 10}
    validate(good)

    def fails(mutate, needle):
        import copy
        s = copy.deepcopy(good)
        mutate(s)
        try:
            validate(s)
        except SpecError as e:
            assert needle in str(e), f"기대 {needle!r}, 실제: {e}"
            return
        raise AssertionError(f"거부됐어야 함: {needle}")

    fails(lambda s: s.pop("name"), "name이 필요")
    fails(lambda s: s.pop("model"), "model이 필요")
    fails(lambda s: s.update(tools=["nope"]), "알 수 없는 도구")
    fails(lambda s: s.update(tools="grep"), "리스트여야")
    fails(lambda s: s.update(approval="whatever"), "approval은")
    fails(lambda s: s.update(worker_policy="many"), "worker_policy는")
    fails(lambda s: s.update(max_steps=0), "max_steps는")
    fails(lambda s: s.update(max_steps=999), "max_steps는")
    # 핵심 안전 규칙 두 개
    fails(lambda s: s.update(tools=["run_bash"]), "approval: auto 를 쓸 수 없습니다")
    fails(lambda s: s["tools"].append("create_worker"), "항상 주입됩니다")
    # 위험 도구 + ask 는 OK
    validate({**good, "tools": ["run_bash"], "approval": "ask"})
    # 도구 없는 오케스트레이터도 OK (create_worker는 어차피 주입된다)
    validate({**good, "tools": []})

    print("OK — 오케스트레이터 스펙 검증 통과")


if __name__ == "__main__":
    demo()
