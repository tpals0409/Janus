"""실행 계측 계약.

wall clock은 표시용 시작 시각에만 쓰고, 모든 duration은 monotonic_ns에서 계산한다.
상위 시간 구간(active_turn/user_wait)은 서로 겹치지 않는다. model/tool/queue/
verification 구간은 원인 분석용 중첩 상세 구간이라 wall time에 단순 합산하지 않는다.
"""

from __future__ import annotations

import resource
import sys
import threading
import time
import uuid
from collections.abc import Callable
from datetime import datetime, timezone

SCHEMA_VERSION = 2


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:16]}"


def _ms(ns: int) -> float:
    return round(ns / 1_000_000, 3)


def process_peak_rss_bytes() -> int:
    """stdlib만으로 현재 프로세스의 peak RSS를 byte로 정규화한다."""
    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # macOS는 byte, Linux/BSD 계열은 KiB를 반환한다.
    return int(value if sys.platform == "darwin" else value * 1024)


class ExecutionTelemetry:
    """한 Task/Session의 monotonic timeline.

    clock를 주입할 수 있어 전체 시간 회계가 정확한지 sleep 없는 테스트가 가능하다.
    """

    _PAIRS = {
        "resource_queue_enter": (
            "resource_queue", {"resource_lease_acquired", "resource_queue_end"}
        ),
        "model_generation_start": ("model_generation", {"model_generation_end"}),
        "tool_run_start": ("tool_run", {"tool_run_end"}),
        "verification_start": ("verification", {"verification_end"}),
    }

    def __init__(
        self,
        *,
        task_id: str | None = None,
        workspace_id: str | None = None,
        session_id: str | None = None,
        clock: Callable[[], int] = time.perf_counter_ns,
    ):
        self.task_id = task_id or _id("task")
        self.workspace_id = workspace_id or _id("workspace")
        self.session_id = session_id or _id("session")
        self.clock = clock
        self.origin_ns = clock()
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.lock = threading.Lock()
        self.events: list[dict] = []
        self.intervals: list[dict] = []
        self._open: dict[tuple[str, str], tuple[int, dict]] = {}
        self._active_turn: tuple[int, str] | None = None
        # Orchestration은 첫 user message를 받은 직후 만들어진다. 생성부터 begin_turn까지의
        # 짧은 구간도 미귀속으로 버리지 않고 user_wait로 회계한다.
        self._waiting_since_ns: int | None = self.origin_ns
        self.memory_snapshots = [self._memory("session_start", self.origin_ns)]

    def elapsed_ms(self, now_ns: int | None = None) -> float:
        return _ms((self.clock() if now_ns is None else now_ns) - self.origin_ns)

    def new_dispatch_id(self) -> str:
        return _id("dispatch")

    def _base(self, *, dispatch_id: str | None, worker_id: str | None) -> dict:
        return {
            "task_id": self.task_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "dispatch_id": dispatch_id,
            "worker_id": worker_id,
        }

    def _interval(
        self,
        category: str,
        start_ns: int,
        end_ns: int,
        *,
        dispatch_id: str | None,
        worker_id: str | None,
        operation_id: str | None = None,
        status: str = "success",
        **data,
    ) -> dict:
        return {
            "category": category,
            "operation_id": operation_id,
            "started_ms": self.elapsed_ms(start_ns),
            "duration_ms": _ms(max(0, end_ns - start_ns)),
            "_duration_ns": max(0, end_ns - start_ns),
            "status": status,
            **self._base(dispatch_id=dispatch_id, worker_id=worker_id),
            **data,
        }

    def begin_turn(self, dispatch_id: str | None = None) -> str:
        now = self.clock()
        dispatch_id = dispatch_id or self.new_dispatch_id()
        with self.lock:
            if self._active_turn is not None:
                raise RuntimeError("active turn이 이미 있습니다")
            if self._waiting_since_ns is not None:
                self.intervals.append(self._interval(
                    "user_wait", self._waiting_since_ns, now,
                    dispatch_id=None, worker_id=None,
                ))
                self._waiting_since_ns = None
            self._active_turn = (now, dispatch_id)
        return dispatch_id

    def end_turn(self, dispatch_id: str, *, status: str) -> None:
        now = self.clock()
        with self.lock:
            active = self._active_turn
            if active is None or active[1] != dispatch_id:
                raise RuntimeError("종료할 active turn이 없습니다")
            self.intervals.append(self._interval(
                "active_turn", active[0], now,
                dispatch_id=dispatch_id, worker_id=None, status=status,
            ))
            self._active_turn = None
            self._waiting_since_ns = now
            self.memory_snapshots.append(self._memory("turn_end", now))

    def record_event(
        self,
        kind: str,
        *,
        node_id: str,
        dispatch_id: str | None,
        worker_id: str | None,
        **data,
    ) -> dict:
        now = self.clock()
        event = {
            "kind": kind,
            "node_id": node_id,
            "at_ms": self.elapsed_ms(now),
            **self._base(dispatch_id=dispatch_id, worker_id=worker_id),
            **data,
        }
        operation_id = str(data.get("operation_id") or "")
        with self.lock:
            self.events.append(event)
            pair = self._PAIRS.get(kind)
            if pair and operation_id:
                category, _ = pair
                self._open[(category, operation_id)] = (now, dict(event))
            elif operation_id:
                for start_kind, (category, end_kinds) in self._PAIRS.items():
                    if kind not in end_kinds:
                        continue
                    opened = self._open.pop((category, operation_id), None)
                    if opened is not None:
                        start_ns, start_event = opened
                        detail = {
                            k: v for k, v in start_event.items()
                            if k not in {
                                "kind", "at_ms", "task_id", "workspace_id", "session_id",
                                "dispatch_id", "worker_id", "node_id", "operation_id",
                            }
                        }
                        self.intervals.append(self._interval(
                            category, start_ns, now,
                            dispatch_id=dispatch_id,
                            worker_id=worker_id,
                            operation_id=operation_id,
                            status=str(data.get("status") or "success"),
                            node_id=node_id,
                            **detail,
                        ))
                    break
        return event

    def _memory(self, reason: str, now_ns: int) -> dict:
        return {
            "at_ms": self.elapsed_ms(now_ns),
            "reason": reason,
            "process_peak_rss_bytes": process_peak_rss_bytes(),
        }

    def snapshot(self, *, usage: dict[str, dict], worker_count: int) -> dict:
        now = self.clock()
        with self.lock:
            intervals = [dict(item) for item in self.intervals]
            if self._active_turn is not None:
                start, dispatch_id = self._active_turn
                intervals.append(self._interval(
                    "active_turn", start, now, dispatch_id=dispatch_id,
                    worker_id=None, status="running",
                ))
            elif self._waiting_since_ns is not None:
                intervals.append(self._interval(
                    "user_wait", self._waiting_since_ns, now,
                    dispatch_id=None, worker_id=None, status="running",
                ))
            events = [dict(item) for item in self.events]
            memory = [dict(item) for item in self.memory_snapshots]

        totals_ns: dict[str, int] = {}
        for interval in intervals:
            category = interval["category"]
            totals_ns[category] = totals_ns.get(category, 0) + interval.pop("_duration_ns")
        totals = {category: _ms(value) for category, value in totals_ns.items()}
        prompt_tokens = sum(v.get("prompt_tokens", 0) for v in usage.values())
        completion_tokens = sum(v.get("completion_tokens", 0) for v in usage.values())
        elapsed_ns = now - self.origin_ns
        elapsed = _ms(elapsed_ns)
        accounted_ns = (
            totals_ns.get("active_turn", 0) + totals_ns.get("user_wait", 0)
        )
        accounted = _ms(accounted_ns)
        return {
            "schema_version": SCHEMA_VERSION,
            "clock": "monotonic_ns",
            "started_at": self.started_at,
            "task_id": self.task_id,
            "workspace_id": self.workspace_id,
            "session_id": self.session_id,
            "elapsed_ms": elapsed,
            "top_level_accounted_ms": accounted,
            "top_level_unaccounted_ms": _ms(max(0, elapsed_ns - accounted_ns)),
            "totals_ms": totals,
            "tokens": {
                "prompt": prompt_tokens,
                "completion": completion_tokens,
                "total": prompt_tokens + completion_tokens,
            },
            "worker_count": worker_count,
            "memory_snapshots": memory,
            "intervals": intervals,
            "events": events,
        }
