"""Thread-safe execution budgets shared by orchestrator and runtime workers."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from contextlib import ExitStack
from copy import deepcopy

# dispatch 예산은 한 턴이 아니라 **세션 전체**에 누적된다. 실측으로 5턴짜리 대화가
# 모델 호출 11회에 26k~34k 토큰을 썼다 — 예전 32,768은 대화 하나를 못 버텼다.
# 호출당 약 2,600 토큰을 기준으로 100회 남짓 버티도록 잡았다.
DEFAULT_BUDGET = {
    "dispatch": {"token_limit": 262_144, "time_limit_ms": 3_600_000, "step_limit": 60},
    "worker": {"token_limit": 49_152, "time_limit_ms": 300_000, "step_limit": 8},
    "workers": {"total_limit": 4, "concurrent_limit": 4},
    "queue": {"timeout_ms": 300_000, "priority": 0},
}


def normalize_budget(value: dict | None, *, max_steps: int | None = None) -> dict:
    result = deepcopy(DEFAULT_BUDGET)
    if max_steps is not None:
        result["dispatch"]["step_limit"] = int(max_steps)
    for section in result:
        supplied = (value or {}).get(section)
        if not isinstance(supplied, dict):
            continue
        for key in result[section]:
            if key not in supplied:
                continue
            parsed = int(supplied[key])
            if parsed < 0 or (key != "priority" and parsed == 0):
                raise ValueError(f"budget {section}.{key} 값이 올바르지 않습니다: {parsed}")
            result[section][key] = parsed
    if result["workers"]["concurrent_limit"] > result["workers"]["total_limit"]:
        raise ValueError("worker concurrent_limit은 total_limit을 넘을 수 없습니다")
    return result


def merge_budget(base: dict, override: dict | None) -> dict:
    merged = deepcopy(base)
    for section, values in (override or {}).items():
        if section in merged and isinstance(values, dict):
            merged[section].update(values)
    return normalize_budget(merged)


def empty_usage() -> dict:
    return {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "steps": 0,
        "active_time_ms": 0.0,
        "workers_started": 0,
        "peak_concurrent_workers": 0,
    }


def claim_step_all(trackers: list[BudgetTracker]) -> bool:
    """Atomically charge one step to every applicable budget scope."""
    ordered = sorted(trackers, key=id)
    with ExitStack() as stack:
        for tracker in ordered:
            stack.enter_context(tracker.lock)
        for tracker in ordered:
            if not tracker._check_time_locked():
                return False
            if int(tracker.usage["steps"]) >= int(tracker.limits["step_limit"]):
                tracker._exhaust("step_limit")
                return False
        for tracker in ordered:
            tracker.usage["steps"] = int(tracker.usage["steps"]) + 1
    return True


class BudgetTracker:
    """One scope's cumulative token/time/step budget."""

    def __init__(
        self, scope: str, limits: dict, *, initial_usage: dict | None = None,
        clock: Callable[[], int] = time.perf_counter_ns,
    ):
        self.scope = scope
        self.limits = dict(limits)
        self.clock = clock
        self.lock = threading.RLock()
        self.usage = {**empty_usage(), **(initial_usage or {})}
        self._active_since_ns: int | None = None
        self.exhausted_reason: str | None = None

    def begin_active(self) -> None:
        with self.lock:
            if self._active_since_ns is None:
                self._active_since_ns = self.clock()

    def end_active(self) -> None:
        with self.lock:
            if self._active_since_ns is not None:
                self.usage["active_time_ms"] += (
                    self.clock() - self._active_since_ns
                ) / 1_000_000
                self._active_since_ns = None
            self._check_time_locked()

    def _active_time_locked(self) -> float:
        value = float(self.usage["active_time_ms"])
        if self._active_since_ns is not None:
            value += (self.clock() - self._active_since_ns) / 1_000_000
        return value

    def _exhaust(self, reason: str) -> bool:
        if self.exhausted_reason is None:
            self.exhausted_reason = f"{self.scope}:{reason}"
        return False

    def _check_time_locked(self) -> bool:
        if self.exhausted_reason is not None:
            return False
        if self._active_time_locked() >= int(self.limits["time_limit_ms"]):
            return self._exhaust("time_limit")
        return True

    def claim_step(self) -> bool:
        with self.lock:
            if not self._check_time_locked():
                return False
            if int(self.usage["steps"]) >= int(self.limits["step_limit"]):
                return self._exhaust("step_limit")
            self.usage["steps"] = int(self.usage["steps"]) + 1
            return True

    def add_tokens(self, prompt: int, completion: int) -> bool:
        with self.lock:
            self.usage["prompt_tokens"] = int(self.usage["prompt_tokens"]) + int(prompt)
            self.usage["completion_tokens"] = (
                int(self.usage["completion_tokens"]) + int(completion)
            )
            total = int(self.usage["prompt_tokens"]) + int(self.usage["completion_tokens"])
            if total >= int(self.limits["token_limit"]):
                return self._exhaust("token_limit")
            return self._check_time_locked()

    def record_worker_start(self, concurrent: int) -> None:
        with self.lock:
            self.usage["workers_started"] = int(self.usage["workers_started"]) + 1
            self.usage["peak_concurrent_workers"] = max(
                int(self.usage["peak_concurrent_workers"]), int(concurrent)
            )

    def exhaust_if_step_limit_reached(self) -> bool:
        with self.lock:
            if int(self.usage["steps"]) >= int(self.limits["step_limit"]):
                return self._exhaust("step_limit")
            return True

    def available(self) -> bool:
        with self.lock:
            return self._check_time_locked()

    def snapshot(self) -> dict:
        with self.lock:
            usage = dict(self.usage)
            usage["active_time_ms"] = round(self._active_time_locked(), 3)
            return {
                "scope": self.scope,
                "limits": dict(self.limits),
                "usage": usage,
                "exhausted_reason": self.exhausted_reason,
            }


class BudgetCancel:
    """Event-like view that also becomes set when a tracker exceeds time."""

    def __init__(self, external: threading.Event | None, trackers: list[BudgetTracker]):
        self.external = external
        self.trackers = trackers

    def is_set(self) -> bool:
        return bool(self.external and self.external.is_set()) or any(
            not tracker.available() for tracker in self.trackers
        )

    def set(self) -> None:
        """Event 프로토콜의 나머지 절반. 호출부는 이것을 Event로 보고 취소를 건다 —
        읽기만 구현하면 scheduler.close()가 종료 도중 AttributeError로 죽는다."""
        if self.external is None:
            self.external = threading.Event()
        self.external.set()
