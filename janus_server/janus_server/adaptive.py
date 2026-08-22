"""Pure adaptive-orchestration policy decisions.

The runtime consumes the returned snapshot but never recalculates it.  This keeps
every Dispatch reproducible: the Task signals, scheduler pressure, retry cause,
effective worker topology, and budget that existed at dispatch time travel together.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .budget import merge_budget, normalize_budget


TASK_CLASSES = {
    "single_file_bug", "multi_file_refactor", "investigation", "test_heavy", "general",
}


def classify_task(task: dict) -> tuple[str, list[str]]:
    text = " ".join(
        str(task.get(key) or "") for key in ("title", "objective", "acceptance_command")
    ).lower()
    signals: list[str] = []

    investigation_words = (
        "investigate", "diagnose", "analyze", "audit", "research", "explain",
        "조사", "진단", "분석", "감사", "원인",
    )
    refactor_words = (
        "refactor", "migration", "across", "multiple files", "architecture",
        "리팩터", "마이그레이션", "여러 파일", "전반", "아키텍처",
    )
    test_words = (
        "test", "pytest", "unittest", "vitest", "jest", "cargo test", "검증", "테스트",
    )
    bug_words = ("bug", "fix", "regression", "오류", "버그", "수정")
    single_words = ("single file", "one file", "한 파일", "단일 파일")

    if any(word in text for word in investigation_words):
        signals.append("investigation_language")
        return "investigation", signals
    if any(word in text for word in refactor_words):
        signals.append("cross_cutting_language")
        return "multi_file_refactor", signals
    if any(word in text for word in test_words):
        signals.append("verification_heavy_language")
        return "test_heavy", signals
    if any(word in text for word in bug_words) and any(word in text for word in single_words):
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
    failed_verification = next(
        (
            item for item in verification_runs
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
    elif task_class == "investigation":
        worker_policy = "fixed_one"
        roles = ["researcher"]
        role_sequence = ["researcher"]
        budget_override = {
            "worker": {"step_limit": min(base_budget["worker"]["step_limit"], 5)},
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        }
        reasons.append("read_only_scout")
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
    elif task_class == "multi_file_refactor":
        worker_policy = "autonomous"
        roles = ["researcher", "implementer", "verifier"]
        role_sequence = ["researcher", "implementer", "verifier"]
        allow_autonomous = cap > 1
        budget_override = {
            "dispatch": {
                "token_limit": max(base_budget["dispatch"]["token_limit"], 40_960),
                "step_limit": max(base_budget["dispatch"]["step_limit"], 36),
            },
            "workers": {"total_limit": min(4, max(1, cap + 1)), "concurrent_limit": min(2, cap)},
        }
        reasons.append("cross_cutting_role_pipeline")

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
        roles = ["researcher"]
        role_sequence = ["researcher"]
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
        allow_autonomous = failure_type == "verification_failure"
        budget_override["workers"] = {
            **budget_override.get("workers", {}), "concurrent_limit": 1,
        }
        reasons.append("queue:single_generation_slot")
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
