"""Pure adaptive-orchestration policy decisions.

The runtime consumes the returned snapshot but never recalculates it.  This keeps
every Dispatch reproducible: the Task signals, scheduler pressure, retry cause,
effective worker topology, and budget that existed at dispatch time travel together.
"""

from __future__ import annotations

import re
from copy import deepcopy

from . import intent as intent_mod
from .budget import merge_budget, normalize_budget

TASK_CLASSES = {
    "single_file_bug", "multi_file_refactor", "multi_component_build",
    "investigation", "planning", "visual_prototype", "operations",
    "test_heavy", "general",
}


# "워커 2개"만 세지 말 것 — 한국어로는 "워커 두개"라고 더 자주 쓴다.
KOREAN_NUMERALS = {
    "한": 1, "하나": 1, "두": 2, "둘": 2, "세": 3, "셋": 3, "네": 4, "넷": 4,
    "다섯": 5, "여섯": 6, "일곱": 7, "여덟": 8, "아홉": 9, "열": 10,
}
_COUNT = r"[0-9]+|" + "|".join(sorted(KOREAN_NUMERALS, key=len, reverse=True))


def _as_count(token: str) -> int | None:
    if token.isdigit():
        return int(token)
    return KOREAN_NUMERALS.get(token)


def requested_worker_count(task: dict) -> int | None:
    """Extract an explicit numeric worker request from the Task contract."""
    text = " ".join(
        str(task.get(key) or "") for key in ("title", "objective")
    ).lower()
    patterns = (
        rf"(?:workers?|워커)\s*({_COUNT})",
        rf"({_COUNT})\s*(?:개(?:의|를|가|는)?\s*)?(?:workers?|워커)",
    )
    counts = [
        value
        for pattern in patterns
        for match in re.finditer(pattern, text)
        if (value := _as_count(match.group(1))) is not None
    ]
    return max(counts) if counts else None


def classify_task(task: dict) -> tuple[str, list[str]]:
    text = " ".join(
        str(task.get(key) or "") for key in ("title", "objective", "acceptance_command")
    ).lower()
    signals: list[str] = []

    if str(task.get("workflow_stage") or "") == "mockup":
        return "visual_prototype", ["explicit_mockup_workflow"]

    investigation_words = intent_mod.INVESTIGATION_TASK_WORDS
    refactor_words = (
        "refactor", "migration", "across", "multiple files", "architecture",
        "리팩터", "마이그레이션", "여러 파일", "전반", "아키텍처",
    )
    multi_component_words = (
        "full service", "web service", "full-stack", "full stack",
        "backend", "frontend", "browser ui",
        "전체 서비스", "웹 서비스", "백엔드", "프론트엔드",
    )
    test_words = (
        "test", "pytest", "unittest", "vitest", "jest", "cargo test", "검증", "테스트",
    )
    bug_words = ("bug", "fix", "regression", "오류", "버그", "수정")
    single_words = ("single file", "one file", "한 파일", "단일 파일")

    planning_words = (
        "implementation plan", "technical plan", "project plan", "task breakdown",
        "roadmap", "milestone plan", "구현 계획", "기술 계획", "작업 계획",
        "단계별 계획", "작업 분해", "로드맵",
    )
    prototype_words = (
        "prototype", "mockup", "wireframe", "visual draft", "ui draft",
        "프로토타입", "목업", "와이어프레임", "시안",
    )
    operations_words = (
        "package mac", "package:mac", "deployment pipeline", "release pipeline",
        "runtime process", "service process", "model server", "health check",
        "mac 패키징", "앱 패키징", "배포 파이프라인", "릴리스 파이프라인",
        "런타임 프로세스", "서비스 프로세스", "모델 서버", "헬스 체크",
    )

    # intent.has_any로 판정한다. 부분 문자열 매칭은 intent 모듈이 존재하는 이유인
    # 바로 그 오검을 되살린다 — "test"가 latest·contest에, "fix"가 prefix·fixture에
    # 걸려 "latest 스키마 마이그레이션"이 test_heavy 토폴로지로 라우팅됐다.
    if intent_mod.has_any(text, planning_words):
        signals.append("explicit_planning_language")
        return "planning", signals
    if intent_mod.has_any(text, prototype_words):
        signals.append("visual_prototype_language")
        return "visual_prototype", signals
    if intent_mod.has_any(text, operations_words):
        signals.append("runtime_operations_language")
        return "operations", signals
    if intent_mod.has_any(text, investigation_words):
        signals.append("investigation_language")
        return "investigation", signals
    if intent_mod.has_any(text, refactor_words):
        signals.append("cross_cutting_language")
        return "multi_file_refactor", signals
    # A product build commonly mentions its acceptance tests, but that must not
    # collapse independent backend/UI/data work into the narrower test topology.
    if sum(intent_mod.has_any(text, (word,)) for word in multi_component_words) >= 2:
        signals.append("multi_component_product_language")
        return "multi_component_build", signals
    if intent_mod.has_any(text, test_words):
        signals.append("verification_heavy_language")
        return "test_heavy", signals
    if intent_mod.has_any(text, bug_words) and intent_mod.has_any(text, single_words):
        signals.extend(("bug_fix_language", "single_file_scope"))
        return "single_file_bug", signals
    return "general", ["no_specialized_signal"]


def classify_failure(
    previous_dispatch: dict | None,
    verification_runs: list[dict] | None = None,
) -> tuple[str | None, str | None]:
    """Return a stable failure class and the evidence that selected it."""
    verification_runs = verification_runs or []
    if previous_dispatch is None:
        return None, None
    previous_id = previous_dispatch.get("id")
    # 명령별로 **최신** 실행만 본다. 전체 목록에서 실패를 찾으면, 나중에 통과한
    # 재실행이 있어도 옛 실패가 남아 이후 모든 dispatch를 verification_failure
    # 토폴로지에 영구히 묶는다.
    newest: dict[tuple[str, str], dict] = {}
    for item in sorted(
        verification_runs,
        key=lambda run: (str(run.get("created_at") or ""), str(run.get("id") or "")),
        reverse=True,
    ):
        newest.setdefault(
            (str(item.get("kind") or ""), str(item.get("command") or "")), item
        )
    failed_verification = next(
        (
            item for item in newest.values()
            if item.get("status") in {"failed", "error"}
            and (item.get("dispatch_id") in {None, previous_id})
        ),
        None,
    )
    if failed_verification is not None:
        return "verification_failure", str(failed_verification.get("id") or "verification")

    status = str(previous_dispatch.get("status") or "")
    error = str(previous_dispatch.get("error") or "").lower()
    exhausted = str(previous_dispatch.get("budget_exhausted_reason") or "").lower()
    if exhausted or "budget exhausted" in error:
        return "budget_exhausted", exhausted or error
    if "timeout" in error or "timed out" in error:
        return "timeout", error
    if "tool" in error or "command" in error:
        return "tool_error", error
    if status == "cancelled":
        return "cancelled", error or "cancelled"
    if status == "failed":
        return "runtime_failure", error or "failed"
    return None, None


def decide(
    *, task: dict, base_profile: dict, scheduler_snapshot: dict,
    previous_dispatch: dict | None = None,
    verification_runs: list[dict] | None = None,
) -> dict:
    """Calculate one immutable policy snapshot for a new Dispatch."""
    task_class, task_signals = classify_task(task)
    model = deepcopy(
        scheduler_snapshot.get("resources", {}).get("model_generation", {})
    )
    cap = max(1, int(model.get("cap", 1)))
    active = max(0, int(model.get("active", 0)))
    queued = max(0, int(model.get("queued", 0)))
    free_slots = max(0, cap - active)
    closed = bool(scheduler_snapshot.get("closed"))
    failure_type, failure_evidence = classify_failure(
        previous_dispatch, verification_runs
    )
    explicit_workers = requested_worker_count(task)

    base_budget = normalize_budget(
        base_profile.get("budget"), max_steps=int(base_profile.get("max_steps", 15))
    )
    budget_override: dict[str, dict[str, int]] = {}
    worker_policy = str(base_profile.get("worker_policy") or "autonomous")
    roles = ["implementer", "verifier"]
    role_sequence: list[str] = []
    allow_autonomous = False
    reasons = [f"task:{task_class}"]

    if task_class == "single_file_bug":
        worker_policy = "none"
        roles = []
        budget_override = {
            "dispatch": {"step_limit": max(8, min(base_budget["dispatch"]["step_limit"], 18))},
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        }
        reasons.append("direct_owner_for_narrow_change")
    elif task_class == "planning":
        worker_policy = "fixed_one"
        roles = ["planner"]
        role_sequence = ["planner"]
        budget_override = {
            "worker": {"step_limit": min(base_budget["worker"]["step_limit"], 8)},
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        }
        reasons.append("single_read_only_planner")
    elif task_class == "visual_prototype":
        worker_policy = "fixed_one"
        roles = ["prototyper"]
        role_sequence = ["prototyper"]
        budget_override = {
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        }
        reasons.append("single_visual_prototyper")
    elif task_class == "operations":
        worker_policy = "fixed_one"
        roles = ["operator"]
        role_sequence = ["operator"]
        budget_override = {
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        }
        reasons.append("single_runtime_operator")
    elif task_class == "investigation":
        worker_policy = "autonomous" if explicit_workers and explicit_workers > 1 else "fixed_one"
        roles = ["scout"]
        worker_count = max(1, explicit_workers or 1)
        role_sequence = ["scout"] * worker_count
        allow_autonomous = bool(explicit_workers and explicit_workers > 1)
        budget_override = {
            "worker": {
                "token_limit": max(base_budget["worker"]["token_limit"], 16_384),
                "step_limit": min(base_budget["worker"]["step_limit"], 8),
            },
            "workers": {
                "total_limit": min(worker_count, base_budget["workers"]["total_limit"]),
                # Worker lifetimes may overlap while the model scheduler serializes
                # their generation on a one-slot local model. Do not discard an
                # explicitly requested worker merely because another one is queued.
                "concurrent_limit": min(
                    worker_count, base_budget["workers"]["concurrent_limit"]
                ),
            },
        }
        reasons.append(
            "explicit_read_only_fanout" if worker_count > 1 else "read_only_scout"
        )
    elif task_class == "test_heavy":
        worker_policy = "autonomous"
        roles = ["implementer", "verifier"]
        role_sequence = ["implementer", "verifier"]
        allow_autonomous = cap > 1
        budget_override = {
            "dispatch": {"step_limit": max(base_budget["dispatch"]["step_limit"], 24)},
            "workers": {"total_limit": 2, "concurrent_limit": min(2, cap)},
        }
        reasons.append("separate_implementation_and_verification")
    elif task_class in {"multi_file_refactor", "multi_component_build"}:
        worker_policy = "autonomous"
        roles = ["scout", "implementer", "verifier"]
        role_sequence = ["scout", "implementer", "verifier"]
        # A one-slot local model still benefits from sequentially owned workers;
        # model generation concurrency and worker delegation are separate limits.
        allow_autonomous = task_class == "multi_component_build" or cap > 1
        budget_override = {
            "dispatch": {
                "token_limit": max(base_budget["dispatch"]["token_limit"], 40_960),
                "step_limit": max(base_budget["dispatch"]["step_limit"], 36),
            },
            "worker": {
                "token_limit": (
                    max(base_budget["worker"]["token_limit"], 49_152)
                    if task_class == "multi_component_build"
                    else base_budget["worker"]["token_limit"]
                ),
                "time_limit_ms": (
                    max(base_budget["worker"]["time_limit_ms"], 1_200_000)
                    if task_class == "multi_component_build"
                    else base_budget["worker"]["time_limit_ms"]
                ),
                "step_limit": (
                    max(base_budget["worker"]["step_limit"], 12)
                    if task_class == "multi_component_build"
                    else base_budget["worker"]["step_limit"]
                ),
            },
            "workers": {
                "total_limit": (
                    min(4, base_budget["workers"]["total_limit"])
                    if task_class == "multi_component_build"
                    else min(4, max(1, cap + 1))
                ),
                "concurrent_limit": min(4, cap, base_budget["workers"]["total_limit"]),
            },
        }
        reasons.append(
            "multi_component_role_pipeline"
            if task_class == "multi_component_build"
            else "cross_cutting_role_pipeline"
        )

    # Retry strategy is driven by evidence, and always bounded to this next attempt.
    retry_strategy = "fresh_attempt"
    retry_allowed = failure_type not in {"cancelled"}
    if failure_type == "verification_failure":
        worker_policy = "autonomous"
        roles = ["verifier", "implementer"]
        role_sequence = ["verifier", "implementer"]
        allow_autonomous = True
        budget_override = {
            **budget_override,
            "workers": {"total_limit": 2, "concurrent_limit": 1},
        }
        retry_strategy = "diagnose_then_repair"
        reasons.append("retry:verification_diagnosis_first")
    elif failure_type == "budget_exhausted":
        worker_policy = "none"
        roles = []
        budget_override = {
            **budget_override,
            "dispatch": {
                "token_limit": min(131_072, base_budget["dispatch"]["token_limit"] * 3 // 2),
                "time_limit_ms": min(3_600_000, base_budget["dispatch"]["time_limit_ms"] * 3 // 2),
                "step_limit": min(100, base_budget["dispatch"]["step_limit"] * 3 // 2),
            },
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        }
        retry_strategy = "expanded_parent_budget"
        reasons.append("retry:reserve_budget_for_parent")
    elif failure_type == "timeout":
        worker_policy = "none"
        roles = []
        budget_override = {
            **budget_override,
            "queue": {"timeout_ms": min(900_000, base_budget["queue"]["timeout_ms"] * 2)},
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        }
        retry_strategy = "defer_fanout_and_extend_timeout"
        reasons.append("retry:timeout_backoff")
    elif failure_type == "tool_error":
        worker_policy = "fixed_one"
        roles = ["verifier"]
        role_sequence = ["verifier"]
        retry_strategy = "inspect_tool_boundary_once"
        reasons.append("retry:tool_boundary_verification")
    elif failure_type == "runtime_failure":
        worker_policy = "fixed_one"
        roles = ["scout"]
        role_sequence = ["scout"]
        retry_strategy = "reconnaissance_then_parent"
        reasons.append("retry:runtime_reconnaissance")
    elif failure_type == "cancelled":
        retry_strategy = "manual_only"
        reasons.append("retry:cancelled_requires_explicit_restart")

    # Queue pressure wins over task fan-out.  A one-slot model is sequential, so it
    # never advertises multi-worker concurrency even when a Task would benefit from it.
    if closed or queued >= cap:
        worker_policy = "none"
        roles = []
        allow_autonomous = False
        budget_override["workers"] = {"total_limit": 1, "concurrent_limit": 1}
        reasons.append("queue:closed_or_saturated")
    elif active >= cap or queued > 0:
        if worker_policy == "autonomous":
            worker_policy = "fixed_one"
            role_sequence = role_sequence[:1]
            roles = roles[:1]
        allow_autonomous = False
        budget_override["workers"] = {"total_limit": 1, "concurrent_limit": 1}
        reasons.append("queue:backpressure_single_worker")
    elif cap == 1:
        allow_autonomous = allow_autonomous or failure_type == "verification_failure"
        if not (explicit_workers and explicit_workers > 1):
            budget_override["workers"] = {
                **budget_override.get("workers", {}), "concurrent_limit": 1,
            }
        reasons.append(
            "queue:single_generation_slot_sequential_workers"
            if explicit_workers and explicit_workers > 1
            else "queue:single_generation_slot"
        )
    else:
        allowed_concurrent = min(
            free_slots, budget_override.get("workers", {}).get(
                "concurrent_limit", base_budget["workers"]["concurrent_limit"]
            )
        )
        budget_override["workers"] = {
            **budget_override.get("workers", {}),
            "concurrent_limit": max(1, allowed_concurrent),
        }
        reasons.append(f"queue:{free_slots}_free_generation_slots")

    effective_budget = merge_budget(base_budget, budget_override)
    previous_id = previous_dispatch.get("id") if previous_dispatch else None
    return {
        "version": 1,
        "task_class": task_class,
        "task_signals": task_signals,
        "scheduler": {
            "closed": closed,
            "model_generation": {
                "cap": cap, "active": active, "queued": queued, "free": free_slots,
            },
        },
        "effective": {
            "worker_policy": worker_policy,
            "worker_roles": roles,
            "worker_role_sequence": role_sequence,
            "allow_autonomous_workers": allow_autonomous,
            "budget": effective_budget,
        },
        "retry": {
            "previous_dispatch_id": previous_id,
            "failure_type": failure_type,
            "evidence": failure_evidence,
            "strategy": retry_strategy,
            "allowed": retry_allowed,
        },
        "reasons": reasons,
    }
