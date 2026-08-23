"""에이전트 런타임 — 세션을 가진 노드 하나.

`qwen3.8mlx/agent/loop.py`에서 이식했다. 바뀐 점:
  - 도구를 통합 레지스트리에서 가져오고, 에이전트마다 **부분집합**만 갖는다
  - 모델 클라이언트는 호출자(runtime.py)가 만들어 넘긴다 (순환 임포트 회피)
  - emit이 UI로 흘러갈 것을 전제로 이벤트 이름을 고정한다

이 모듈은 UI도 그래프도 모른다. 표시와 승인은 콜백으로 빠진다.
"""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from typing import Callable

from openai import OpenAI

from . import tools as T
from . import budget as budget_mod
from . import scheduler as scheduler_mod
from .workspace import WorkspaceContext

DEFAULT_MAX_STEPS = 15
DEFAULT_MAX_OUTPUT_TOKENS = 12_288
CIRCUIT_BREAK = 3  # 같은 도구가 연속 N회 실패하면 중단
EMPTY_RESPONSE_RETRIES = 2
DEFAULT_CONTEXT_MAX_CHARS = 24_000
DEFAULT_CONTEXT_RECENT_BLOCKS = 8
MAX_PROJECT_SUMMARY_CHARS = 4_000


class Session:
    """append-only 이벤트 로그. 모델용 메시지는 여기서 파생된다.

    events가 단일 진실 원천이다. UI는 이 로그를 읽어 트랜스크립트를 그린다.
    tool_result 이벤트는 도구가 반환한 dict 원본을 그대로 보관한다.
    """

    def __init__(self, system_prompt: str, registry: dict | None = None, *,
                 context_max_chars: int | None = DEFAULT_CONTEXT_MAX_CHARS,
                 context_recent_blocks: int = DEFAULT_CONTEXT_RECENT_BLOCKS,
                 summary_max_chars: int = MAX_PROJECT_SUMMARY_CHARS):
        self.system_prompt = system_prompt
        self.events: list[dict] = []
        self.registry = registry  # 실행별 도구(create_worker 등)의 렌더러를 찾기 위해
        self.context_max_chars = context_max_chars
        self.context_recent_blocks = max(1, int(context_recent_blocks))
        self.summary_max_chars = max(500, int(summary_max_chars))
        self.context_stats: dict = {}
        self._last_prefix_hash: str | None = None

    def append(self, kind: str, **data):
        self.events.append({"kind": kind, **data})

    def _event_blocks(self) -> list[list[dict]]:
        """assistant와 뒤따르는 tool result를 한 블록으로 묶는다.

        압축 경계가 tool call/result 쌍을 찢으면 OpenAI 호환 서버가 요청을
        거부한다. 그래서 단순히 마지막 N개 message를 자르지 않는다.
        """
        blocks: list[list[dict]] = []
        for e in self.events:
            if e["kind"] == "user":
                blocks.append([{"role": "user", "content": e["content"]}])
            elif e["kind"] == "assistant":
                m = {"role": "assistant", "content": e.get("content") or ""}
                if e.get("tool_calls"):
                    m["tool_calls"] = e["tool_calls"]
                blocks.append([m])
            elif e["kind"] == "tool_result":
                # 원본 dict는 e["value"]에 남고, 모델에겐 렌더링된 텍스트만 보낸다
                message = {
                    "role": "tool", "tool_call_id": e["tool_call_id"],
                    "content": T.render(e["name"], e["value"], registry=self.registry),
                }
                if blocks and blocks[-1][0]["role"] == "assistant":
                    blocks[-1].append(message)
                else:  # 손상된/legacy 로그도 전송 가능한 형태로 보존
                    blocks.append([message])
        return blocks

    @staticmethod
    def _chars(messages: list[dict]) -> int:
        return len(json.dumps(messages, ensure_ascii=False, separators=(",", ":")))

    @staticmethod
    def _brief(value: str, limit: int = 240) -> str:
        value = " ".join(str(value).split())
        return value if len(value) <= limit else value[:limit] + "…"

    def _project_summary(self, blocks: list[list[dict]]) -> str:
        """이전 대화를 재생하지 않고 목표·결정·도구 결과만 압축한다."""
        lines: list[str] = []
        for block in blocks:
            for message in block:
                role = message["role"]
                content = self._brief(message.get("content") or "")
                if role == "user" and content:
                    lines.append(f"Objective/request: {content}")
                elif role == "assistant":
                    if content:
                        lines.append(f"Agent result/decision: {content}")
                    names = [
                        call.get("function", {}).get("name", "tool")
                        for call in message.get("tool_calls") or []
                    ]
                    if names:
                        lines.append("Tools called: " + ", ".join(names))
                elif role == "tool" and content:
                    lines.append(f"Tool result: {content}")
        summary = "\n".join(lines)
        if len(summary) > self.summary_max_chars:
            # 최초 objective와 최근 결정/결과를 함께 남긴다. 앞부분만 자르면
            # 현재 작업으로 이어지는 최신 상태가 사라진다.
            head = lines[0] if lines else ""
            remaining = self.summary_max_chars - len(head) - 2
            tail: list[str] = []
            for line in reversed(lines[1:]):
                if len(line) + 1 > remaining:
                    break
                tail.append(line)
                remaining -= len(line) + 1
            summary = head + "\n…\n" + "\n".join(reversed(tail))
        return summary or "Earlier session activity omitted; no durable result was recorded."

    def derive_messages(self, *, compact: bool = True) -> list[dict]:
        system = {"role": "system", "content": self.system_prompt}
        blocks = self._event_blocks()
        full = [system, *(message for block in blocks for message in block)]
        baseline_chars = self._chars(full)
        omitted: list[list[dict]] = []
        kept = blocks

        max_chars = self.context_max_chars if compact else None
        if max_chars is not None and baseline_chars > max_chars and len(blocks) > 1:
            # 압축이 필요하다고 판정했으면 최소 한 블록은 실제로 요약한다.
            split = max(1, len(blocks) - self.context_recent_blocks)
            omitted = blocks[:split]
            kept = blocks[split:]
            while True:
                summary = self._project_summary(omitted)
                summarized_system = {
                    "role": "system",
                    "content": (
                        self.system_prompt
                        + "\n\nProject/session summary (older context):\n"
                        + summary
                    ),
                }
                candidate = [
                    summarized_system,
                    *(message for block in kept for message in block),
                ]
                # Some OpenAI-compatible local servers require an actual user
                # message, not only a system summary followed by tool history.
                # A long tool loop can otherwise compact away the sole request.
                if not any(message["role"] == "user" for message in candidate):
                    latest_user = next(
                        (block[0] for block in reversed(blocks)
                         if block and block[0]["role"] == "user"),
                        None,
                    )
                    if latest_user is not None:
                        candidate.insert(1, latest_user)
                if self._chars(candidate) <= max_chars or len(kept) <= 1:
                    full = candidate
                    break
                omitted.append(kept.pop(0))

        summary_content = self._project_summary(omitted) if omitted else ""
        stable_prefix = self.system_prompt + "\n" + summary_content
        prefix_hash = hashlib.sha256(stable_prefix.encode("utf-8")).hexdigest()[:16]
        prefix_reused = self._last_prefix_hash == prefix_hash
        if compact:
            self._last_prefix_hash = prefix_hash
        sent_chars = self._chars(full)
        baseline_token_estimate = (baseline_chars + 3) // 4
        sent_token_estimate = (sent_chars + 3) // 4
        self.context_stats = {
            "compacted": bool(omitted),
            "baseline_messages": 1 + sum(len(block) for block in blocks),
            "sent_messages": len(full),
            "baseline_chars": baseline_chars,
            "sent_chars": sent_chars,
            "saved_chars": max(0, baseline_chars - sent_chars),
            "baseline_token_estimate": baseline_token_estimate,
            "sent_token_estimate": sent_token_estimate,
            "saved_token_estimate": max(
                0, baseline_token_estimate - sent_token_estimate
            ),
            "summary_chars": len(summary_content),
            "omitted_blocks": len(omitted),
            "prefix_hash": prefix_hash,
            "prefix_reused": prefix_reused,
            "cache_candidate_chars": len(stable_prefix),
        }
        return full


def build_system_prompt(base: str, tool_names: list[str],
                        registry: dict | None = None) -> str:
    """도구별 guidance를 시스템 프롬프트에 합친다 — 규칙을 도구 옆에 두는 패턴."""
    # 프롬프트가 전부 영어라 모델이 답까지 영어로 낸다. 답의 언어는 요청자가 정한다.
    language = (
        "Write your final answer in the same language as the request you were given."
    )
    if not tool_names:
        return f"{base}\n\nYou have no tools. Answer directly.\n\n{language}"
    return (
        f"{base}\n\n"
        "Work by calling tools. When the task is done, reply with plain text and no "
        "tool call — that is how you finish.\n\n"
        "Execution priority: follow the user's explicit tool or delegation instruction "
        "before doing your own preflight work. If the user explicitly asks you to "
        "create or spawn a worker, your first tool call MUST be create_worker. Do not "
        "read, search, or inspect files first; put any already supplied context into "
        "the worker task.\n\n"
        f"Tools:\n{T.guidance_for(tool_names, registry=registry)}\n\n"
        f"Be brief. Do not narrate what you are about to do; just do it.\n\n{language}"
    )


def _assemble(stream, emit, cancel=None) -> tuple[str, list[dict], dict | None]:
    """스트리밍 청크를 (텍스트, tool_calls, usage)로 조립.

    cancel이 켜지면 스트림을 닫고 즉시 나온다 — 생성 도중에도 멈출 수 있어야 한다.
    usage는 로컬에선 비용이 아니라 지연시간의 원인이다(prefill = prompt_tokens에 비례).
    """
    parts: list[str] = []
    calls: dict[int, dict] = {}
    usage: dict | None = None

    for chunk in stream:
        if cancel is not None and cancel.is_set():
            try:
                stream.close()
            except Exception:
                pass
            break
        # 마지막 청크(choices 비어있음)에 usage가 실려온다
        if getattr(chunk, "usage", None):
            u = chunk.usage
            usage = {"prompt_tokens": u.prompt_tokens,
                     "completion_tokens": u.completion_tokens,
                     "total_tokens": u.total_tokens}
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        # 사고 과정은 답이 아니다 — 화면에만 흘리고 대화 기록(parts)에는 넣지 않는다.
        if reasoning := getattr(delta, "reasoning_content", None):
            emit("reasoning_delta", text=reasoning)
        if delta.content:
            parts.append(delta.content)
            emit("text_delta", text=delta.content)
        for tc in delta.tool_calls or []:
            slot = calls.setdefault(
                tc.index, {"id": "", "type": "function",
                           "function": {"name": "", "arguments": ""}})
            if tc.id:
                slot["id"] = tc.id
            if tc.function and tc.function.name:
                slot["function"]["name"] = tc.function.name
            if tc.function and tc.function.arguments:
                slot["function"]["arguments"] += tc.function.arguments

    return "".join(parts), [calls[i] for i in sorted(calls)], usage


def run(
    *,
    client: OpenAI,
    model: str,
    system_prompt: str,
    task: str,
    tool_names: list[str],
    workspace_context: WorkspaceContext,
    approve: Callable[[str, dict], bool],
    emit: Callable[..., None],
    max_steps: int = DEFAULT_MAX_STEPS,
    cancel: threading.Event | None = None,
    extra_tools: list[dict] | None = None,
    session: Session | None = None,
    scheduler: scheduler_mod.ResourceScheduler | None = None,
    priority: int = 0,
    queue_timeout: float | None = scheduler_mod.DEFAULT_QUEUE_TIMEOUT,
    budget_trackers: list[budget_mod.BudgetTracker] | None = None,
) -> tuple[str, list[dict]]:
    """에이전트를 한 턴 돌린다.

    반환: (마지막 assistant 텍스트, 이벤트 로그)

    approve(name, args) -> bool : 승인 필요한 도구를 실행하기 전 호출 (블로킹)
    emit(kind, **data)          : 표시용. 로직에 영향을 주지 않는다
    extra_tools                 : _t() 모양 dict들 — 실행별 도구(create_worker) 주입
    session                     : 지속 Session을 넘기면 이어서 대화한다 (멀티턴)
    workspace_context           : 파일/셰 도구의 불변 소유권과 jail
    """
    reg = dict(T.REGISTRY)
    reg.update({t["name"]: t for t in (extra_tools or [])})
    tool_names = [n for n in tool_names if n in reg]
    allowed_tool_names = set(tool_names)
    if session is None:
        session = Session(build_system_prompt(system_prompt, tool_names, registry=reg),
                          registry=reg)
    session.append("user", content=task)
    emit("user", content=task)

    schemas = T.schemas_for(tool_names, registry=reg)
    fail_streak: dict[str, int] = {}
    empty_response_streak = 0
    last_text = ""
    tok_prompt = tok_completion = 0
    scheduler = scheduler or scheduler_mod.default_scheduler()
    trackers = list(budget_trackers or [])
    effective_cancel = budget_mod.BudgetCancel(cancel, trackers)
    emitted_budget_reasons: set[str] = set()

    def emit_budget_exhaustion() -> bool:
        exhausted = False
        for tracker in trackers:
            reason = tracker.exhausted_reason
            if reason is None:
                continue
            exhausted = True
            if reason not in emitted_budget_reasons:
                emitted_budget_reasons.add(reason)
                emit("budget_exhausted", reason=reason, budget=tracker.snapshot())
        return exhausted

    for step in range(max_steps):
        if cancel is not None and cancel.is_set():
            emit("done", reason="cancelled")
            return last_text, session.events
        if not budget_mod.claim_step_all(trackers):
            emit_budget_exhaustion()
            emit("done", reason="budget_exhausted")
            return last_text, session.events

        emit("step", n=step + 1)
        msgs = session.derive_messages()
        context_stats = dict(session.context_stats)
        emit("context_window", **context_stats)
        emit(
            "prompt_cache_probe",
            prefix_hash=context_stats["prefix_hash"],
            prefix_reused=context_stats["prefix_reused"],
            cache_candidate_chars=context_stats["cache_candidate_chars"],
            # 서버 cache hit을 주장하지 않는다. 안정 prefix 재사용 가능성만 계측한다.
            mode="stable_prefix_probe",
        )
        # 실제 전송분을 트레이스에. 매 step 전체를 실으면 스팬이 비대해지므로
        # 첫 step만 전체, 이후엔 직전 assistant/tool 응답 이후의 증분(마지막 2개)만.
        emit("llm_call", messages=msgs if step == 0 else msgs[-2:],
             total_messages=len(msgs))
        kwargs = {"model": model, "messages": msgs, "stream": True,
                  "max_tokens": DEFAULT_MAX_OUTPUT_TOKENS,
                  "stream_options": {"include_usage": True},
                  # Qwen's thinking mode can finish with reasoning_content only,
                  # losing the structured tool call it planned. Coding agents
                  # need reliable actions more than hidden chain-of-thought.
                  "extra_body": {"enable_thinking": False}}
        if schemas:
            kwargs["tools"] = schemas
        generation_id = uuid.uuid4().hex[:16]
        emit("resource_queue_enter", resource="model_generation",
             operation_id=generation_id, step=step + 1)
        try:
            generation_lease = scheduler.acquire(
                scheduler_mod.ResourceClass.MODEL_GENERATION,
                priority=priority,
                owner_id=workspace_context.dispatch_id,
                cancel=effective_cancel,
                timeout=queue_timeout,
                on_wait=lambda wait: emit(
                    "resource_queue_wait", operation_id=generation_id,
                    step=step + 1, **wait,
                ),
            )
        except scheduler_mod.LeaseCancelled:
            emit("resource_queue_end", resource="model_generation",
                 operation_id=generation_id, step=step + 1, status="cancelled")
            budget_exhausted = emit_budget_exhaustion()
            emit("done", reason="budget_exhausted" if budget_exhausted else "cancelled")
            return last_text, session.events
        except scheduler_mod.LeaseTimeout:
            emit("resource_queue_end", resource="model_generation",
                 operation_id=generation_id, step=step + 1, status="timeout")
            raise
        except scheduler_mod.SchedulerClosed:
            emit("resource_queue_end", resource="model_generation",
                 operation_id=generation_id, step=step + 1, status="shutdown")
            raise
        except Exception:
            emit("resource_queue_end", resource="model_generation",
                 operation_id=generation_id, step=step + 1, status="error")
            raise
        with generation_lease:
            emit("resource_lease_acquired", resource="model_generation",
                 operation_id=generation_id, lease_id=generation_lease.id,
                 step=step + 1)
            emit("model_generation_start", operation_id=generation_id, step=step + 1)
            try:
                text, calls, usage = _assemble(
                    client.chat.completions.create(**kwargs), emit, effective_cancel
                )
            except Exception as error:
                emit("model_generation_end", operation_id=generation_id,
                     step=step + 1, status="error",
                     error=f"{type(error).__name__}: {error}")
                raise
            else:
                emit("model_generation_end", operation_id=generation_id,
                     step=step + 1,
                     status=("cancelled" if cancel is not None and cancel.is_set()
                             else "budget_exhausted" if emit_budget_exhaustion()
                             else "success"))
        if usage:
            tok_prompt += usage["prompt_tokens"]
            tok_completion += usage["completion_tokens"]
            # step별 토큰 — 어느 step이 컨텍스트를 부풀려 느려지는지 보인다
            emit("usage", step=step + 1, **usage)
            for tracker in trackers:
                tracker.add_tokens(
                    usage["prompt_tokens"], usage["completion_tokens"]
                )

        if emit_budget_exhaustion():
            emit("done", reason="budget_exhausted")
            return last_text, session.events

        if cancel is not None and cancel.is_set():
            emit("done", reason="cancelled")
            return last_text, session.events

        if not text.strip() and not calls:
            empty_response_streak += 1
            emit("empty_response", attempt=empty_response_streak)
            # A direct-answer node has no action it can recover into. Retrying it
            # can turn a legitimate blank response into fabricated fallback text
            # from test doubles or compatibility servers. Tool-capable coding
            # nodes still receive the guarded recovery nudge below.
            if not schemas and text:
                emit("done", reason="empty_response")
                return last_text, session.events
            if empty_response_streak > EMPTY_RESPONSE_RETRIES:
                raise RuntimeError(
                    "model returned reasoning without assistant content or tool calls "
                    f"{empty_response_streak} times"
                )
            # Qwen-compatible servers can end after private reasoning without
            # materializing the intended action.  Give the next generation an
            # actual user query and do not persist an invalid empty assistant.
            session.append(
                "user",
                content=(
                    "Your previous generation hit the output limit before producing an "
                    "action. Do not write a placeholder, stub, partial file, or falsely "
                    "claim completion. Call an available tool only if it can make a complete "
                    "useful change. If the implementation is too large for one call, split "
                    "it into cohesive complete modules across subsequent tool calls. If the "
                    "available tools cannot do that safely, return an explicit failure."
                ),
            )
            continue

        empty_response_streak = 0
        session.append("assistant", content=text, tool_calls=calls or None)
        # 개행뿐인 답은 답이 아니다 — 화면에 빈 말풍선만 남는다.
        if text.strip():
            last_text = text
            emit("assistant", content=text)

        if not calls:
            emit("done", reason="no_tool_calls")
            return last_text, session.events

        def exec_call(call: dict) -> tuple[str, dict]:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError as e:
                return name, {"error": f"인자 JSON 파싱 실패: {e}"}
            if name not in allowed_tool_names:
                emit(
                    "tool_rejected", name=name, args=args, call_id=call["id"],
                    reason="tool_not_in_node_subset",
                )
                return name, {
                    "error": f"tool {name!r} is not available to this agent node",
                    "reason": "tool_not_in_node_subset",
                }
            operation_id = uuid.uuid4().hex[:16]
            resource_class = T.resource_class_for(name, registry=reg)
            emit("resource_queue_enter", resource=resource_class, operation_id=operation_id,
                 tool=name, call_id=call["id"])
            try:
                tool_lease = scheduler.acquire(
                    resource_class,
                    priority=priority,
                    owner_id=workspace_context.dispatch_id,
                    cancel=effective_cancel,
                    timeout=queue_timeout,
                    on_wait=lambda wait: emit(
                        "resource_queue_wait", operation_id=operation_id,
                        tool=name, call_id=call["id"], **wait,
                    ),
                )
            except scheduler_mod.LeaseCancelled:
                emit("resource_queue_end", resource=resource_class,
                     operation_id=operation_id, tool=name, call_id=call["id"],
                     status="cancelled")
                return name, {"error": "도구 resource lease 대기가 취소됨"}
            except scheduler_mod.LeaseTimeout:
                emit("resource_queue_end", resource=resource_class,
                     operation_id=operation_id, tool=name, call_id=call["id"],
                     status="timeout")
                return name, {"error": f"도구 resource lease timeout({queue_timeout:g}s)"}
            except scheduler_mod.SchedulerClosed:
                emit("resource_queue_end", resource=resource_class,
                     operation_id=operation_id, tool=name, call_id=call["id"],
                     status="shutdown")
                return name, {"error": "ResourceScheduler가 종료됨"}
            except Exception:
                emit("resource_queue_end", resource=resource_class,
                     operation_id=operation_id, tool=name, call_id=call["id"],
                     status="error")
                raise
            with tool_lease:
                emit("resource_lease_acquired", resource=resource_class,
                     operation_id=operation_id, lease_id=tool_lease.id,
                     tool=name, call_id=call["id"])
                emit("tool_run_start", operation_id=operation_id, name=name,
                     call_id=call["id"])
                emit("tool_start", name=name, args=args, call_id=call["id"])
                # 승인 강제는 모든 호출 경로가 공유하는 dispatch의 책임이다.
                # 여기서 미리 검사하면 다른 호출 경로가 또 뚫린다.
                try:
                    value = T.dispatch(
                        name, args, approve=approve, registry=reg,
                        context=workspace_context,
                    )
                except Exception:
                    emit("tool_run_end", operation_id=operation_id, name=name,
                         call_id=call["id"], status="error")
                    raise
                emit("tool_run_end", operation_id=operation_id, name=name,
                     call_id=call["id"],
                     status="error" if "error" in value else "success")
            return name, value

        if len(calls) > 1:
            # ponytail: 턴의 모든 도구 호출을 병렬화 — agent가 워커를 모르게 유지.
            # MLX 1대가 생성을 직렬화하므로 병렬은 도구 I/O만 겹친다. 충분.
            results: list[tuple[str, dict] | None] = [None] * len(calls)

            def _exec_into(i: int, c: dict) -> None:
                try:
                    results[i] = exec_call(c)
                except Exception as e:  # emit 등이 죽어도 턴은 계속
                    results[i] = (c["function"]["name"],
                                  {"error": f"{type(e).__name__}: {e}"})

            threads = [threading.Thread(target=_exec_into, args=(i, c), daemon=True)
                       for i, c in enumerate(calls)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()
        else:
            results = [exec_call(calls[0])]

        # 결과 반영은 호출 순서대로 — 병렬이어도 세션 로그는 결정적이다
        for call, (name, value) in zip(calls, results):
            session.append("tool_result", tool_call_id=call["id"], name=name, value=value)
            emit("tool_result", name=name, value=value, call_id=call["id"])

            # 서킷 브레이커 — 로컬 모델이 같은 실수를 반복하는 걸 끊는다
            if "error" in value:
                fail_streak[name] = fail_streak.get(name, 0) + 1
                if fail_streak[name] >= CIRCUIT_BREAK:
                    emit("done", reason=f"circuit_break:{name}")
                    return last_text, session.events
            else:
                fail_streak[name] = 0

        if any(not tracker.available() for tracker in trackers):
            emit_budget_exhaustion()
            emit("done", reason="budget_exhausted")
            return last_text, session.events

    for tracker in trackers:
        tracker.exhaust_if_step_limit_reached()
    if emit_budget_exhaustion():
        emit("done", reason="budget_exhausted")
    else:
        emit("done", reason="max_steps")
    return last_text, session.events
