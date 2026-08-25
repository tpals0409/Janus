"""Process-wide local resource scheduling for Janus runtimes.

The scheduler is deliberately independent from asyncio. Agent turns and runtime
workers execute in ordinary threads, so a condition-variable queue gives every
call path the same blocking lease contract.
"""

from __future__ import annotations

import itertools
import math
import os
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from enum import StrEnum


class ResourceClass(StrEnum):
    MODEL_GENERATION = "model_generation"
    CPU_TOOL = "cpu_tool"
    IO_TOOL = "io_tool"
    VERIFICATION = "verification"


# 오케스트레이터 1 + 워커 4 = 5가 동시에 생성할 수 있다. 가중치는 공유되지만 KV 캐시는
# 스트림마다 따로 잡히므로, 스왑이 보이면 JANUS_MODEL_SLOTS를 낮춰 되돌린다.
# ponytail: 고정 슬롯 수. 남은 메모리로 자동 산정이 필요해지면 그때 재보자.
MODEL_GENERATION_SLOTS = max(1, int(os.environ.get("JANUS_MODEL_SLOTS", "5")))
DEFAULT_CAPS: dict[ResourceClass, int] = {
    ResourceClass.MODEL_GENERATION: MODEL_GENERATION_SLOTS,
    ResourceClass.CPU_TOOL: 2,
    ResourceClass.IO_TOOL: 8,
    ResourceClass.VERIFICATION: 1,
}
DEFAULT_QUEUE_TIMEOUT = 300.0


class SchedulerClosed(RuntimeError):
    pass


class LeaseCancelled(RuntimeError):
    pass


class LeaseTimeout(TimeoutError):
    pass


@dataclass(frozen=True)
class _Waiter:
    id: str
    resource: ResourceClass
    priority: int
    sequence: int
    queued_at: float


class ResourceLease:
    """A capacity token that is always safe to release more than once."""

    def __init__(
        self, scheduler: ResourceScheduler, lease_id: str,
        resource: ResourceClass, owner_id: str | None,
    ):
        self.scheduler = scheduler
        self.id = lease_id
        self.resource = resource
        self.owner_id = owner_id
        self._released = False
        self._lock = threading.Lock()

    def release(self) -> None:
        with self._lock:
            if self._released:
                return
            self._released = True
        self.scheduler._release(self.id)

    def __enter__(self) -> ResourceLease:
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


class ResourceScheduler:
    """Priority scheduler with aging so old low-priority work cannot starve.

    Higher integer priorities run first. Every ``aging_interval`` spent in the
    queue adds one effective priority point; the boost is unbounded.
    """

    def __init__(
        self,
        caps: Mapping[ResourceClass | str, int] | None = None,
        *,
        aging_interval: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
        cancel_poll_interval: float = 0.05,
    ):
        configured = dict(DEFAULT_CAPS)
        for name, cap in (caps or {}).items():
            resource = ResourceClass(name)
            if int(cap) < 1:
                raise ValueError(f"resource cap은 1 이상이어야 합니다: {resource}={cap}")
            configured[resource] = int(cap)
        if aging_interval <= 0:
            raise ValueError("aging_interval은 0보다 커야 합니다")
        self.caps = configured
        self.aging_interval = float(aging_interval)
        self.clock = clock
        self.cancel_poll_interval = max(0.005, float(cancel_poll_interval))
        self._condition = threading.Condition(threading.RLock())
        self._waiters: dict[ResourceClass, list[_Waiter]] = {
            resource: [] for resource in ResourceClass
        }
        self._active: dict[ResourceClass, int] = {
            resource: 0 for resource in ResourceClass
        }
        self._leases: dict[
            str, tuple[ResourceClass, str | None, threading.Event | None]
        ] = {}
        self._sequence = itertools.count()
        self._closed = False

    def _effective_priority(self, waiter: _Waiter, now: float) -> int:
        waited = max(0.0, now - waiter.queued_at)
        return waiter.priority + int(waited / self.aging_interval)

    def _next_waiter(self, resource: ResourceClass, now: float) -> _Waiter | None:
        waiters = self._waiters[resource]
        if not waiters:
            return None
        return max(
            waiters,
            key=lambda item: (self._effective_priority(item, now), -item.sequence),
        )

    def acquire(
        self,
        resource: ResourceClass | str,
        *,
        priority: int = 0,
        owner_id: str | None = None,
        cancel: threading.Event | None = None,
        timeout: float | None = DEFAULT_QUEUE_TIMEOUT,
        on_wait: Callable[[dict], None] | None = None,
    ) -> ResourceLease:
        resource = ResourceClass(resource)
        started = self.clock()
        waiter = _Waiter(
            id=f"waiter_{uuid.uuid4().hex[:16]}",
            resource=resource,
            priority=int(priority),
            sequence=next(self._sequence),
            queued_at=started,
        )
        with self._condition:
            if self._closed:
                raise SchedulerClosed("ResourceScheduler가 종료됐습니다")
            self._waiters[resource].append(waiter)
            wait_reported = False
            while True:
                now = self.clock()
                if self._closed:
                    self._waiters[resource].remove(waiter)
                    self._condition.notify_all()
                    raise SchedulerClosed("ResourceScheduler가 종료됐습니다")
                if cancel is not None and cancel.is_set():
                    self._waiters[resource].remove(waiter)
                    self._condition.notify_all()
                    raise LeaseCancelled(f"{resource} lease 대기가 취소됐습니다")
                if timeout is not None and now - started >= timeout:
                    self._waiters[resource].remove(waiter)
                    self._condition.notify_all()
                    raise LeaseTimeout(f"{resource} lease 대기 timeout({timeout:g}s)")
                next_waiter = self._next_waiter(resource, now)
                if self._active[resource] < self.caps[resource] and next_waiter is waiter:
                    self._waiters[resource].remove(waiter)
                    lease_id = f"lease_{uuid.uuid4().hex[:16]}"
                    self._active[resource] += 1
                    self._leases[lease_id] = (resource, owner_id, cancel)
                    return ResourceLease(self, lease_id, resource, owner_id)
                if not wait_reported and on_wait is not None:
                    ordered = sorted(
                        self._waiters[resource],
                        key=lambda item: (
                            -self._effective_priority(item, now), item.sequence
                        ),
                    )
                    reason = (
                        "capacity_exhausted"
                        if self._active[resource] >= self.caps[resource]
                        else "higher_priority_waiter"
                    )
                    try:
                        on_wait({
                            "reason": reason,
                            "resource": resource.value,
                            "position": ordered.index(waiter) + 1,
                            "queued": len(ordered),
                            "active": self._active[resource],
                            "cap": self.caps[resource],
                            "priority": waiter.priority,
                        })
                    except Exception:
                        pass
                    wait_reported = True
                wait_for = self.cancel_poll_interval
                if timeout is not None:
                    wait_for = min(wait_for, max(0.001, timeout - (now - started)))
                self._condition.wait(wait_for)

    def _release(self, lease_id: str) -> None:
        with self._condition:
            lease = self._leases.pop(lease_id, None)
            if lease is None:
                return
            resource, _owner_id, _cancel = lease
            self._active[resource] -= 1
            self._condition.notify_all()

    def snapshot(self) -> dict:
        with self._condition:
            now = self.clock()
            return {
                "closed": self._closed,
                "resources": {
                    resource.value: {
                        "cap": self.caps[resource],
                        "active": self._active[resource],
                        "queued": len(self._waiters[resource]),
                        "next_priority": (
                            self._effective_priority(next_waiter, now)
                            if (next_waiter := self._next_waiter(resource, now)) else None
                        ),
                    }
                    for resource in ResourceClass
                },
                "active_leases": len(self._leases),
                "leases": [
                    {"id": lease_id, "resource": resource.value, "owner_id": owner_id}
                    for lease_id, (resource, owner_id, _cancel) in self._leases.items()
                ],
            }

    def close(self) -> None:
        with self._condition:
            self._closed = True
            for _resource, _owner_id, cancel in self._leases.values():
                if cancel is not None:
                    cancel.set()
            self._condition.notify_all()

    def wait_for_idle(self, timeout: float = 10.0) -> bool:
        """Wait until acquired leases are returned and queued requests have exited."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while self._leases or any(self._waiters.values()):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return False
                self._condition.wait(remaining)
            return True


_DEFAULT_SCHEDULER = ResourceScheduler()


def default_scheduler() -> ResourceScheduler:
    """Return the one scheduler shared by all Task and legacy runtimes."""
    return _DEFAULT_SCHEDULER


def assess_vram_sizing(
    events: list[dict],
    *,
    minimum_samples: int = 10,
    p95_wait_threshold_ms: float = 1000,
) -> dict:
    """Gate expensive VRAM sizing behind sustained measured slot contention."""
    if minimum_samples < 1:
        raise ValueError("minimum_samples must be positive")
    if p95_wait_threshold_ms < 0:
        raise ValueError("p95_wait_threshold_ms must not be negative")
    waits = sorted(
        float(event["queue_wait_ms"])
        for event in events
        if event.get("kind") == "lease_acquired"
        and event.get("resource") == ResourceClass.MODEL_GENERATION.value
        and isinstance(event.get("queue_wait_ms"), (int, float))
    )
    p95 = waits[max(0, math.ceil(len(waits) * 0.95) - 1)] if waits else 0.0
    enough = len(waits) >= minimum_samples
    bottleneck = enough and p95 >= p95_wait_threshold_ms
    return {
        "status": "recommended" if bottleneck else "deferred",
        "sample_count": len(waits),
        "minimum_samples": minimum_samples,
        "p95_wait_ms": round(p95, 3),
        "p95_wait_threshold_ms": float(p95_wait_threshold_ms),
        "reason": (
            "measured_model_slot_bottleneck"
            if bottleneck
            else "insufficient_samples"
            if not enough
            else "model_slot_wait_below_threshold"
        ),
    }
