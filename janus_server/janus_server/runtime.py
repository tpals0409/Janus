"""오케스트레이터-워커 실행 엔진.

에이전트 = 오케스트레이터 1개. 오케스트레이터는 `create_worker` 스킬로 런타임에
워커를 만들고, 워커는 그 실행의 트레이스에만 존재한다(저장·재사용 없음).

LangGraph 없이 실행을 직접 제어하므로 스팬을 명시적으로 열고 닫는다 — 이벤트
귀속 추측(구 trace.py)이 필요 없다. 이벤트는 워커 스레드에서 `send` 콜백으로
바로 나간다.
"""

from __future__ import annotations

import glob
import hashlib
import json
import os
import re
import threading
import uuid
from typing import Callable

from openai import OpenAI

from . import agent as agent_mod
from . import budget as budget_mod
from . import spec as spec_mod
from . import scheduler as scheduler_mod
from . import telemetry as telemetry_mod
from . import tools as T
from .workspace import WorkspaceContext

# UI의 짧은 이름 -> 로컬에 실제로 존재하는 스냅샷 경로.
#
# 절대 repo ID("orcarouter/Qwen3.8-...")를 보내면 안 된다. mlx_vlm.server는 로드되지
# 않은 모델 id를 받으면 HuggingFace에서 **리포 전체를**(모든 quant, ~80GB) 내려받기
# 시작하고, 그동안 요청은 응답 없이 매달린다. 로컬 경로만 넘긴다.
LOCAL_MODELS = {
    "qwen3.8-27b": "~/.cache/huggingface/hub/"
                   "models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit",
}

MLX_BASE_URL = "http://localhost:8080/v1"
WORKER_SYSTEM_MAX_CHARS = 2_000
WORKER_TASK_MAX_CHARS = 6_000
WORKER_CONTEXT_MAX_CHARS = 4_000
WORKER_ROLES = {"implementer", "researcher", "verifier"}
MAX_MODEL_QUEUE_FOR_SPAWN = 1
SINGLE_SLOT_PARENT_RESERVE_NUMERATOR = 6
SINGLE_SLOT_PARENT_RESERVE_DENOMINATOR = 10
TIGHT_DISPATCH_STEP_LIMIT = 16
EXPLICIT_WORKER_PHRASES = (
    "create_worker", "spawn worker", "spawn a worker", "delegate to a worker",
    "worker를", "워커를", "위임",
)


def worker_spawn_pressure(snapshot: dict, *, max_model_queue: int =
                          MAX_MODEL_QUEUE_FOR_SPAWN) -> str | None:
    """현재 로컬 생성 queue가 worker fan-out을 더 받을 수 있는지 판정한다."""
    if snapshot.get("closed"):
        return "scheduler_closed"
    model = snapshot["resources"][scheduler_mod.ResourceClass.MODEL_GENERATION.value]
    if int(model.get("queued", 0)) >= max_model_queue:
        return "model_queue_backpressure"
    return None


def worker_spawn_fit(
    policy: str, user_task: str | None, *, allow_autonomous_workers: bool = False,
) -> str | None:
    """1-slot 로컬 모델에서 첫 worker가 중복 구현 비용을 만들지 않게 한다.

    생성이 직렬인 1-slot 환경에서는 역할과 무관하게 worker가 추가 prefill/generation을
    만든다. 사용자가 명시적으로 위임했거나 profile이 override한 경우에만 허용한다.
    """
    if policy != "autonomous" or user_task is None or allow_autonomous_workers:
        return None
    lowered = user_task.lower()
    if any(phrase in lowered for phrase in EXPLICIT_WORKER_PHRASES):
        return None
    return "autonomous_implementer_overhead"


def effective_worker_step_limit(
    requested: object,
    configured: int,
    dispatch_snapshot: dict,
    scheduler_snapshot: dict,
) -> int:
    """Keep enough dispatch steps for the parent to integrate worker output.

    A worker on a one-slot local model is sequential work, not parallel work. On a
    tight dispatch, letting it consume the ordinary eight-step worker allowance can
    leave the parent unable to inspect partial edits, correct them, or even produce a
    final response. Reserve 60% of the dispatch step budget for the parent in that
    topology. Larger/default dispatches still retain the configured worker cap.
    """
    try:
        requested_limit = max(1, min(int(requested), 50))
    except (TypeError, ValueError):
        requested_limit = 8
    limit = min(int(configured), requested_limit)
    model = scheduler_snapshot["resources"][
        scheduler_mod.ResourceClass.MODEL_GENERATION.value
    ]
    if int(model.get("cap", 1)) > 1:
        return limit

    dispatch_limit = int(dispatch_snapshot["limits"]["step_limit"])
    dispatch_used = int(dispatch_snapshot["usage"]["steps"])
    parent_reserve = (
        dispatch_limit * SINGLE_SLOT_PARENT_RESERVE_NUMERATOR
        + SINGLE_SLOT_PARENT_RESERVE_DENOMINATOR - 1
    ) // SINGLE_SLOT_PARENT_RESERVE_DENOMINATOR
    worker_room = max(1, dispatch_limit - dispatch_used - parent_reserve)
    return min(limit, worker_room)


def effective_worker_role(
    policy: str,
    requested_role: str,
    dispatch_step_limit: int,
    scheduler_snapshot: dict,
) -> tuple[str, str | None]:
    """Turn a forced sequential implementer into a scout on tight local runs.

    ``fixed_one`` is useful as a policy/control experiment, but on one generation
    slot a second implementer duplicates the parent's full edit loop. A read-only
    scout still satisfies the one-worker topology while leaving one owner for edits.
    The adaptation is explicit in telemetry and tool results.
    """
    model = scheduler_snapshot["resources"][
        scheduler_mod.ResourceClass.MODEL_GENERATION.value
    ]
    if (
        policy == "fixed_one"
        and requested_role == "implementer"
        and int(model.get("cap", 1)) == 1
        and int(dispatch_step_limit) <= TIGHT_DISPATCH_STEP_LIMIT
    ):
        return "researcher", "single_slot_tight_dispatch_scout"
    return requested_role, None


def resolve_local_model(name: str) -> str:
    pattern = LOCAL_MODELS.get(name)
    if pattern is None:
        raise spec_mod.SpecError(
            f"모르는 모델 {name!r} (등록됨: {sorted(LOCAL_MODELS)})"
        )
    hits = glob.glob(os.path.expanduser(pattern))
    if not hits:
        raise spec_mod.SpecError(
            f"{name!r}의 로컬 파일을 찾을 수 없습니다: {pattern}\n"
            "  먼저 받으세요: hf download orcarouter/Qwen3.8-27B-Uncensored-MLX --include '4-bit/*'"
        )
    return hits[0]


def make_client() -> OpenAI:
    # ponytail: local-only. 클라우드 provider가 실제로 필요해지면 spec에 provider 필드 재추가.
    # 모듈 함수로 둔 이유: 테스트가 monkeypatch로 FakeClient를 꽂는다.
    return OpenAI(base_url=MLX_BASE_URL, api_key="none")


# ─────────────────────────── 클리핑 (구 trace.py에서 구출) ───────────────────────────

MAX_STR = 4000
MAX_LIST = 50


def _clip(v):
    """저장·전송분을 자른다 — 원문이 27B 출력이면 수십 KB가 우습다."""
    if isinstance(v, str):
        return v if len(v) <= MAX_STR else v[:MAX_STR] + f"… (+{len(v) - MAX_STR}자)"
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    if isinstance(v, list):
        clipped = [_clip(x) for x in v[:MAX_LIST]]
        if len(v) > MAX_LIST:
            clipped.append(f"… (+{len(v) - MAX_LIST}개)")
        return clipped
    return v


ORCH_ID = "orchestrator"  # 실행 간 고정 — A/B 비교가 node_id로 매칭된다


class Orchestration:
    """WS 연결 하나 = 오케스트레이터 대화 하나.

    send(dict)                     : 스레드 안전 WS 송신 (서버가 제공)
    approver(node_id, tool, args, context): 블로킹 승인 브리지 (서버가 제공)
    """

    def __init__(self, spec: dict, *, send: Callable[[dict], None],
                 approver: Callable[[str, str, dict, WorkspaceContext], bool] | None,
                 workspace_context: WorkspaceContext,
                 task_id: str | None = None, session_id: str | None = None,
                 clock: Callable[[], int] | None = None,
                 scheduler: scheduler_mod.ResourceScheduler | None = None,
                 priority: int | None = None,
                 queue_timeout: float | None = None,
                 budget: dict | None = None,
                 budget_usage: dict | None = None):
        self.spec = spec
        self.send = send
        self.client = make_client()
        self.model = resolve_local_model(spec["model"])
        self.tools = list(spec.get("tools") or [])
        self.max_steps = spec.get("max_steps", 15)
        self.worker_policy = spec.get("worker_policy", "autonomous")
        self.allow_autonomous_workers = bool(spec.get("allow_autonomous_workers", False))
        self.worker_enabled = self.worker_policy != "none"
        if task_id is not None and task_id != workspace_context.task_id:
            raise ValueError("task_id와 WorkspaceContext.task_id가 다릅니다")
        self.workspace_context = workspace_context
        self.active_workspace_context: WorkspaceContext | None = None
        self.scheduler = scheduler or scheduler_mod.default_scheduler()
        self.budget = budget_mod.normalize_budget(budget, max_steps=self.max_steps)
        self.max_steps = int(self.budget["dispatch"]["step_limit"])
        self.priority = int(
            self.budget["queue"]["priority"] if priority is None else priority
        )
        self.queue_timeout = (
            self.budget["queue"]["timeout_ms"] / 1000
            if queue_timeout is None else queue_timeout
        )
        self.dispatch_budget = budget_mod.BudgetTracker(
            "dispatch", self.budget["dispatch"], initial_usage=budget_usage
        )
        self.budget_exhausted_reason: str | None = None

        self.cancel = threading.Event()
        self.worker_cancels: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.node_events: dict[str, list] = {}
        self.node_usage: dict[str, dict] = {}
        self.spans: list[dict] = []          # [0]=오케스트레이터, 이후 워커 스폰 순
        self.worker_seq = 0
        self.active_workers = 0
        self.worker_requests: dict[str, dict] = {}
        telemetry_kwargs = {
            "task_id": workspace_context.task_id,
            "workspace_id": workspace_context.workspace_id,
            "session_id": session_id,
        }
        if clock is not None:
            telemetry_kwargs["clock"] = clock
        self.telemetry = telemetry_mod.ExecutionTelemetry(**telemetry_kwargs)
        self.current_dispatch_id: str | None = None
        self.last_dispatch_id: str | None = None
        self.first_message: str | None = None
        self.last_text = ""
        self.cancelled_turn = False
        self.turn_failed = False  # 턴이 예외로 죽음 — 저장본이 success로 거짓말하지 않게

        # 승인 매핑: auto → 전부 허용, ask → 브리지, 브리지 없음 → 거부.
        # 위험 도구의 실제 게이트는 tools.dispatch다 — 여기는 정책 선택일 뿐.
        approval = spec.get("approval", "auto")
        if approval == "auto":
            self._approve_for = lambda nid, context: (lambda name, args: True)
        elif approver is not None:
            self._approve_for = lambda nid, context: (
                lambda name, args: approver(nid, name, args, context)
            )
        else:
            self._approve_for = lambda nid, context: (lambda name, args: False)

        self.create_worker = self._make_create_worker()
        registry = dict(T.REGISTRY)
        if self.worker_enabled:
            registry[self.create_worker["name"]] = self.create_worker
        runtime_tools = self.tools + (["create_worker"] if self.worker_enabled else [])
        self.session = agent_mod.Session(
            agent_mod.build_system_prompt(
                spec.get("system_prompt") or "You are an orchestrator.",
                runtime_tools, registry=registry),
            registry=registry)

    # ── 스팬/이벤트 ──

    def _sink(self, node_id: str, kind: str, data: dict) -> None:
        clipped = {k: _clip(v) for k, v in data.items()}
        measured = self.telemetry.record_event(
            kind, node_id=node_id, dispatch_id=self.current_dispatch_id,
            worker_id=None if node_id == ORCH_ID else node_id, **clipped,
        )
        ev = {"type": "agent_event", **measured}
        with self.lock:
            self.node_events.setdefault(node_id, []).append(ev)
            if kind == "usage":
                u = self.node_usage.setdefault(
                    node_id, {"prompt_tokens": 0, "completion_tokens": 0})
                u["prompt_tokens"] += data.get("prompt_tokens", 0)
                u["completion_tokens"] += data.get("completion_tokens", 0)
        self.send(ev)

    def _open_span(self, node_id: str, *, label: str | None,
                   parent_id: str | None, input: dict) -> dict:
        span = {"id": uuid.uuid4().hex[:12], "node_id": node_id, "status": "running",
                "started_ms": self.telemetry.elapsed_ms(), "input": _clip(input),
                "parent_id": parent_id, "label": label,
                "task_id": self.telemetry.task_id,
                "workspace_id": self.telemetry.workspace_id,
                "session_id": self.telemetry.session_id,
                "dispatch_id": self.current_dispatch_id,
                "worker_id": None if node_id == ORCH_ID else node_id}
        with self.lock:
            self.spans.append(span)
        self.send({"type": "span_start", "span": dict(span)})
        return span

    def _close_span(self, span: dict, status: str, output: dict) -> None:
        with self.lock:
            span["status"] = status
            span["duration_ms"] = round(
                self.telemetry.elapsed_ms() - span["started_ms"], 3
            )
            span["output"] = _clip(output)
            span["events"] = list(self.node_events.get(span["node_id"], []))
            span["usage"] = self.node_usage.get(span["node_id"])
        self.send({"type": "span_end", "span": dict(span)})

    # ── create_worker 스킬 ──

    def _make_create_worker(self) -> dict:
        def handler(name: str = "", system_prompt: str = "", task: str = "",
                    tools: list | None = None, max_steps: int = 8,
                    role: str = "implementer", context: str = "") -> dict:
            workspace_context = self.active_workspace_context
            if workspace_context is None:
                return {"error": "active WorkspaceContext가 없습니다"}

            requested_role = str(role).lower().strip()
            if requested_role not in WORKER_ROLES:
                return {"error": f"알 수 없는 worker role: {requested_role}"}
            scheduler_state = self.scheduler.snapshot()
            model_state = scheduler_state["resources"][
                scheduler_mod.ResourceClass.MODEL_GENERATION.value
            ]
            role, role_adaptation = effective_worker_role(
                self.worker_policy,
                requested_role,
                int(self.budget["dispatch"]["step_limit"]),
                scheduler_state,
            )
            # 부분집합 규칙: 워커 도구 ⊆ 오케스트레이터의 spec.tools.
            # researcher/verifier는 결과를 수정하지 못하도록 읽기 전용 교집합만 받는다.
            requested_tools = list(dict.fromkeys(str(t) for t in (tools or [])))
            allowed = [tool for tool in requested_tools if tool in self.tools]
            if role in {"researcher", "verifier"}:
                allowed = [tool for tool in allowed if tool in T.READ_ONLY]

            raw_system = str(system_prompt) or "You are a focused worker agent."
            raw_task = str(task) or "(no task)"
            if role_adaptation is not None:
                raw_system = (
                    "You are a read-only scout for a single-slot local coding agent. "
                    "Inspect only the files needed for the delegated task. Return concise, "
                    "specific evidence: current definitions, required edits, invariants, and "
                    "paths that must not change. Never attempt a write or edit tool, never "
                    "broaden the contract, and finish immediately after the investigation."
                )
                raw_task = (
                    "Investigate the delegated task without changing the workspace. Return a "
                    "concise implementation handoff for the parent.\n\nOriginal delegated task:\n"
                    + raw_task
                )
            raw_context = str(context or "")
            prepared_system = raw_system[:WORKER_SYSTEM_MAX_CHARS]
            prepared_task = raw_task[:WORKER_TASK_MAX_CHARS]
            prepared_context = raw_context[:WORKER_CONTEXT_MAX_CHARS]
            if prepared_context:
                prepared_task += "\n\nRelevant context (only what this subtask needs):\n" + prepared_context
            if role == "verifier":
                prepared_system += (
                    "\n\nYou are a read-only verifier. Check the supplied result and evidence; "
                    "do not modify files. Return findings to the orchestrator."
                )
            elif role == "researcher":
                prepared_system += (
                    "\n\nInvestigate and return concise evidence; leave final integration to "
                    "the orchestrator."
                )
            elif role == "implementer":
                prepared_system += (
                    "\n\nAfter applying the requested edits, return a concise factual result "
                    "immediately. If run_bash is not in your tools, do not search for a shell, "
                    "Python executable, or test runner; leave independent verification to the "
                    "orchestrator. Do not broaden the original contract."
                )

            fingerprint = hashlib.sha256(json.dumps(
                {"name": str(name), "requested_role": requested_role, "role": role,
                 "system": prepared_system,
                 "task": prepared_task, "tools": allowed},
                ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")).hexdigest()[:20]
            rejection: str | None = None
            reused: dict | None = None
            with self.lock:
                if self.worker_policy == "none":
                    rejection = "worker_policy_none"
                elif ((existing := self.worker_requests.get(fingerprint)) is not None
                      and existing["status"] in {"completed", "completed_partial"}):
                    reused = dict(existing)
                elif self.worker_policy == "fixed_one" and self.worker_seq >= 1:
                    rejection = "worker_policy_fixed_one"
                elif self.worker_seq >= int(self.budget["workers"]["total_limit"]):
                    rejection = "worker_total_budget"
                elif self.active_workers >= int(self.budget["workers"]["concurrent_limit"]):
                    rejection = "worker_concurrent_budget"
                elif existing is not None and existing["status"] == "running":
                    rejection = "duplicate_worker_running"
                elif (fit := worker_spawn_fit(
                    self.worker_policy, self.first_message,
                    allow_autonomous_workers=self.allow_autonomous_workers,
                )) is not None:
                    rejection = fit
                elif (pressure := worker_spawn_pressure(scheduler_state)) is not None:
                    rejection = pressure
                else:
                    self.worker_seq += 1
                    seq = self.worker_seq
                    self.active_workers += 1
                    concurrent = self.active_workers
                    self.dispatch_budget.record_worker_start(concurrent)
                    slug = re.sub(
                        r"[^a-z0-9]+", "-", str(name).lower()
                    ).strip("-") or "worker"
                    wid = f"w{seq}-{slug}"
                    self.worker_requests[fingerprint] = {
                        "status": "running", "worker": wid, "role": role,
                    }
            if reused is not None:
                self._sink(ORCH_ID, "worker_result_reused", {
                    "worker": reused["worker"], "role": role,
                    "fingerprint": fingerprint,
                })
                return {
                    "worker": reused["worker"], "role": role,
                    "result": reused["result"], "reused": True,
                }
            if rejection is not None:
                self._sink(ORCH_ID, "worker_spawn_suppressed", {
                    "reason": rejection, "role": role,
                    "fingerprint": fingerprint,
                    "model_generation": model_state,
                })
                messages = {
                    "worker_policy_none": "worker policy가 none이라 worker를 만들 수 없습니다",
                    "duplicate_worker_running": "같은 subtask worker가 이미 실행 중입니다",
                    "worker_policy_fixed_one": "worker policy가 fixed_one이라 추가 worker를 만들 수 없습니다",
                    "worker_total_budget": "worker total budget을 소진했습니다",
                    "worker_concurrent_budget": "worker concurrent budget을 소진했습니다",
                    "autonomous_implementer_overhead": (
                        "단일 model slot에서 중복 implementer 비용이 예상돼 spawn을 억제했습니다"
                    ),
                    "model_queue_backpressure": "model queue 압력 때문에 worker spawn을 억제했습니다",
                    "scheduler_closed": "scheduler가 종료돼 worker spawn을 억제했습니다",
                }
                if rejection in {
                    "worker_policy_fixed_one", "autonomous_implementer_overhead",
                    "duplicate_worker_running", "model_queue_backpressure",
                }:
                    previous = next(iter(self.worker_requests.values()), None)
                    prior = str((previous or {}).get("result") or "").strip()
                    guidance = (
                        f"Worker spawn suppressed ({rejection}). "
                        "Do not call create_worker again. Inspect the current workspace and "
                        "complete/integrate the task directly."
                    )
                    if prior:
                        guidance = prior + "\n\n" + guidance
                    return {
                        "worker": (previous or {}).get("worker"),
                        "role": role, "result": guidance,
                        "suppressed": True, "reason": rejection,
                    }
                return {"error": messages[rejection], "reason": rejection}
            # extra_tools를 안 넘기므로 워커는 create_worker를 절대 못 받는다 (깊이 1).
            dispatch_snapshot = self.dispatch_budget.snapshot()
            steps = effective_worker_step_limit(
                max_steps,
                int(self.budget["worker"]["step_limit"]),
                dispatch_snapshot,
                scheduler_state,
            )
            if role_adaptation is not None:
                # One local generation can issue several parallel read calls. Waiting for a
                # second/third scout generation is sequential overhead; the parent owns edits.
                steps = 1

            cancel = threading.Event()
            worker_limits = dict(self.budget["worker"])
            worker_limits["step_limit"] = steps
            worker_budget = budget_mod.BudgetTracker(f"worker:{wid}", worker_limits)
            worker_budget.begin_active()
            self.worker_cancels[wid] = cancel
            # span_start를 본 UI/headless harness가 즉시 stop을 보내도 놓치지 않도록
            # cancel handle을 공개한 뒤 span 이벤트를 보낸다.
            span = self._open_span(wid, label=str(name) or wid,
                                   parent_id=self.spans[0]["id"] if self.spans else None,
                                   input={"task": prepared_task, "tools": allowed,
                                          "role": role, "requested_role": requested_role,
                                          "role_adaptation": role_adaptation,
                                          "context_chars": len(prepared_context)})
            if role_adaptation is not None:
                self._sink(wid, "worker_role_adapted", {
                    "requested_role": requested_role,
                    "effective_role": role,
                    "reason": role_adaptation,
                    "model_generation_cap": model_state.get("cap", 1),
                    "dispatch_step_limit": self.budget["dispatch"]["step_limit"],
                })
            self._sink(wid, "worker_step_budget_reserved", {
                "requested_steps": max_steps,
                "effective_steps": steps,
                "dispatch_steps_used": dispatch_snapshot["usage"]["steps"],
                "dispatch_step_limit": dispatch_snapshot["limits"]["step_limit"],
                "model_generation_cap": model_state.get("cap", 1),
            })
            self._sink(wid, "worker_context_prepared", {
                "role": role,
                "system_chars": len(prepared_system),
                "task_chars": len(prepared_task),
                "context_chars": len(prepared_context),
                "requested_tools": requested_tools,
                "allowed_tools": allowed,
                "truncated": (
                    len(raw_system) > WORKER_SYSTEM_MAX_CHARS
                    or len(raw_task) > WORKER_TASK_MAX_CHARS
                    or len(raw_context) > WORKER_CONTEXT_MAX_CHARS
                ),
            })
            write_calls: dict[str, str] = {}
            changed_paths: set[str] = set()
            worker_trace_lock = threading.Lock()

            def emit_worker(kind: str, **data) -> None:
                call_id = str(data.get("call_id") or "")
                if kind == "tool_start" and data.get("name") in {"write_file", "edit_file"}:
                    path = str((data.get("args") or {}).get("path") or "").strip()
                    if call_id and path:
                        with worker_trace_lock:
                            write_calls[call_id] = path
                elif kind == "tool_run_end" and data.get("status") == "success":
                    with worker_trace_lock:
                        if path := write_calls.get(call_id):
                            changed_paths.add(path)
                self._sink(wid, kind, data)

            try:
                text, _ = agent_mod.run(
                    client=self.client, model=self.model,
                    system_prompt=prepared_system,
                    task=prepared_task,
                    tool_names=allowed,
                    workspace_context=workspace_context,
                    approve=self._approve_for(wid, workspace_context),
                    emit=emit_worker,
                    max_steps=steps,
                    cancel=cancel,
                    scheduler=self.scheduler,
                    priority=self.priority,
                    queue_timeout=self.queue_timeout,
                    budget_trackers=[worker_budget, self.dispatch_budget],
                )
            except Exception as e:
                with self.lock:
                    self.worker_requests[fingerprint]["status"] = "failed"
                self._close_span(span, "error", {"error": f"{type(e).__name__}: {e}"})
                return {"error": f"worker {wid} failed: {type(e).__name__}: {e}"}
            finally:
                worker_budget.end_active()
                with self.lock:
                    self.active_workers -= 1
                self.worker_cancels.pop(wid, None)

            if self.dispatch_budget.exhausted_reason:
                self.budget_exhausted_reason = self.dispatch_budget.exhausted_reason
            if worker_budget.exhausted_reason:
                budget_snapshot = worker_budget.snapshot()
                partial_result = str(text or "").strip()
                touched = ", ".join(sorted(changed_paths)) or "none recorded"
                if role_adaptation is not None:
                    integration = (
                        f"Worker role adapted from {requested_role} to read-only {role} "
                        f"({role_adaptation}) and reached its focused local budget at "
                        f"{worker_budget.exhausted_reason}. Do not spawn another worker. "
                        "Use any evidence above and complete the original implementation directly "
                        "without broadening its contract. Do not repeat broad discovery already "
                        "performed by the scout. If run_bash is unavailable, do not inspect an "
                        "unrelated test harness as a substitute; the outer runner verifies it."
                    )
                else:
                    integration = (
                        f"Worker reached its focused local budget at "
                        f"{worker_budget.exhausted_reason}. Successful write targets: {touched}. "
                        "Do not spawn another worker. Validate these changes strictly against the "
                        "original request or project contract; do not invent undocumented behavior "
                        "or speculative tests. If the requested files and behavior are already "
                        "covered, finish directly. Otherwise make only the necessary correction."
                    )
                rendered_result = (
                    partial_result + "\n\n" + integration if partial_result else integration
                )
                with self.lock:
                    self.worker_requests[fingerprint].update(
                        status="completed_partial", result=rendered_result,
                        partial=True, warning=worker_budget.exhausted_reason,
                    )
                self._close_span(span, "error", {
                    "error": worker_budget.exhausted_reason,
                    "partial_result": text,
                    "budget": budget_snapshot,
                })
                return {
                    "worker": wid, "role": role, "result": rendered_result,
                    "requested_role": requested_role,
                    "role_adaptation": role_adaptation,
                    "partial": True, "warning": worker_budget.exhausted_reason,
                    "budget": budget_snapshot,
                }

            if cancel.is_set():
                with self.lock:
                    self.worker_requests[fingerprint]["status"] = "failed"
                self._close_span(span, "error", {"error": "사용자가 워커를 중단함"})
                return {"worker": wid, "role": role, "result": text,
                        "cancelled": "worker was stopped by the user before finishing"}
            with self.lock:
                self.worker_requests[fingerprint].update(
                    status="completed", result=text
                )
            self._close_span(span, "success", {"result": text, "role": role})
            rendered_text = str(text or "")
            if role_adaptation is not None:
                rendered_text = (
                    f"Worker role adapted from {requested_role} to {role} "
                    f"({role_adaptation}) for single-slot efficiency. "
                    "Use this as read-only evidence and complete the implementation directly. "
                    "Do not repeat broad glob/grep/read discovery already covered by the scout. "
                    "Respect the original allowed and forbidden paths exactly. If run_bash is "
                    "unavailable, leave verification to the outer runner and finish after the "
                    "required edits.\n\n"
                    + rendered_text
                )
                with self.lock:
                    self.worker_requests[fingerprint]["result"] = rendered_text
            return {
                "worker": wid, "role": role, "requested_role": requested_role,
                "role_adaptation": role_adaptation, "result": rendered_text,
            }

        return T._t(
            "create_worker", handler,
            lambda v: str(v.get("result") or v.get("warning") or v.get("error") or ""),
            T._obj(["name", "system_prompt", "task"],
                   name={"type": "string", "description": "Short worker name."},
                   system_prompt={"type": "string",
                                  "description": "Role and rules for the worker."},
                   task={"type": "string", "description": "The concrete subtask."},
                   role={"type": "string",
                         "enum": ["implementer", "researcher", "verifier"],
                         "description": "Worker role. Verifier is forced read-only."},
                   context={"type": "string",
                            "description": "Only the minimal context needed by this subtask."},
                   tools={"type": "array", "items": {"type": "string"},
                          "description": "Tool names for the worker — subset of your own."},
                   max_steps={"type": "number", "description": "Step budget (default 8)."}),
            "Spawn a worker agent for a separable subtask and get its result.",
            "Spawn only for a separable subtask. Pass minimal context and the smallest "
            "tool subset. Use role=verifier for read-only result checks. Duplicate work "
            "and excess model queue pressure are suppressed.",
            resource_class="cpu_tool",
        )

    # ── 턴 실행 ──

    def turn(self, text: str, *, dispatch_id: str | None = None) -> None:
        """블로킹 — asyncio.to_thread로 호출된다. ReAct 한 턴."""
        self.cancel.clear()
        self.cancelled_turn = False
        self.turn_failed = False
        dispatch_id = self.telemetry.begin_turn(dispatch_id)
        self.current_dispatch_id = dispatch_id
        self.last_dispatch_id = dispatch_id
        context = self.workspace_context.for_dispatch(dispatch_id)
        self.active_workspace_context = context
        self.dispatch_budget.begin_active()
        if self.first_message is None:
            self.first_message = text
            self._open_span(ORCH_ID, label=self.spec.get("name"), parent_id=None,
                            input={"task": text})
        try:
            last, _ = agent_mod.run(
                client=self.client, model=self.model,
                system_prompt=self.spec.get("system_prompt") or "",  # session이 이미 보유
                task=text,
                tool_names=self.tools + (["create_worker"] if self.worker_enabled else []),
                workspace_context=context,
                approve=self._approve_for(ORCH_ID, context),
                emit=lambda kind, **d: self._sink(ORCH_ID, kind, d),
                max_steps=self.max_steps,
                cancel=self.cancel,
                extra_tools=[self.create_worker] if self.worker_enabled else [],
                session=self.session,
                scheduler=self.scheduler,
                priority=self.priority,
                queue_timeout=self.queue_timeout,
                budget_trackers=[self.dispatch_budget],
            )
            if last:
                self.last_text = last
            if self.cancel.is_set():
                self.cancelled_turn = True
        except Exception:
            self.turn_failed = True
            raise
        finally:
            self.dispatch_budget.end_active()
            self.budget_exhausted_reason = self.dispatch_budget.exhausted_reason
            status = ("error" if self.turn_failed else
                      "cancelled" if self.cancelled_turn else "success")
            self.telemetry.end_turn(dispatch_id, status=status)
            self.current_dispatch_id = None
            self.active_workspace_context = None
        # turn_end는 서버가 저장을 마친 뒤 보낸다 — 여기서 보내면 히스토리 갱신이 빈손

    # ── 취소 ──

    def cancel_all(self) -> None:
        """현재 턴 중단 (오케스트레이터 + 라이브 워커 전부). 세션은 유지된다."""
        # ponytail: cancel == stop-turn; "대화 리셋"이 필요해지면 그건 새 WS 연결이다.
        self.cancel.set()
        for ev in list(self.worker_cancels.values()):
            ev.set()

    def stop_worker(self, node_id: str) -> None:
        ev = self.worker_cancels.get(node_id)
        if ev is not None:
            ev.set()

    # ── 저장 스냅샷 ──

    def snapshot_spans(self) -> list[dict]:
        """저장용 사본 — 오케스트레이터 스팬을 채워 영원한 running이 남지 않게 마감."""
        with self.lock:
            spans = [dict(s) for s in self.spans]
            for s in spans:
                if s["node_id"] == ORCH_ID:
                    s["status"] = ("error" if self.cancelled_turn or self.turn_failed
                                   else "success")
                    s["duration_ms"] = round(
                        self.telemetry.elapsed_ms() - s["started_ms"], 3
                    )
                    s["output"] = _clip({"reply": self.last_text})
                    s["events"] = list(self.node_events.get(ORCH_ID, []))
                    s["usage"] = self.node_usage.get(ORCH_ID)
                elif s["status"] == "running":
                    # 저장 시점에 아직 도는 워커 — 이벤트만이라도 남긴다
                    s["events"] = list(self.node_events.get(s["node_id"], []))
                    s["usage"] = self.node_usage.get(s["node_id"])
        return spans

    def snapshot_telemetry(self) -> dict:
        return self.telemetry.snapshot(
            usage=self.node_usage,
            worker_count=self.worker_seq,
        )

    def snapshot_budget(self) -> dict:
        return self.dispatch_budget.snapshot()
