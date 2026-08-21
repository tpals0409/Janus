"""실행 -> 스팬 + 세션 이벤트 스트림.

두 갈래를 하나로 합친다:
  1. LangGraph의 astream_events → 노드 경계(span_start/span_end)
  2. agent 노드가 sink로 밀어넣는 세션 이벤트 → 그 노드 안에서 벌어지는 일

에이전트 하나가 몇 분씩 돌 수 있으므로 (2)는 발생 즉시 나가야 한다. 끝나고 몰아서
주면 화면이 멈춘 것처럼 보인다.
"""

from __future__ import annotations

import asyncio
import threading
import time
import uuid
from typing import AsyncIterator, Callable

MAX_FIELD_CHARS = 4000


def _clip(v):
    """UI로 보내는 값만 자른다."""
    if isinstance(v, str) and len(v) > MAX_FIELD_CHARS:
        return v[:MAX_FIELD_CHARS] + f"\n... [{len(v) - MAX_FIELD_CHARS}자 생략]"
    if isinstance(v, dict):
        return {k: _clip(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_clip(x) for x in v[:50]]
    return v


async def run(
    graph,
    state: dict,
    node_types: dict[str, str],
    recursion_limit: int = 50,
    approver: Callable[[str, str, dict], bool] | None = None,
    cancel_event=None,
) -> AsyncIterator[dict]:
    """그래프를 돌리며 이벤트를 낸다.

    낼 수 있는 것: span_start, span_end, token, agent_event, run_end, run_error

    approver(node_id, tool_name, args) -> bool 은 워커 스레드에서 **블로킹으로**
    호출된다. 승인 UI가 응답할 때까지 그 에이전트만 멈춘다.
    """
    q: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    open_spans: dict[str, dict] = {}          # langgraph run_id -> span
    active_runs: dict[str, list[str]] = {}    # node_id -> 현재 열린 run_id들
    span_sessions: dict[str, list[dict]] = {} # run_id -> 해당 실행 1회의 세션 이벤트
    span_usage: dict[str, dict] = {}          # run_id -> {prompt, completion}
    trace_lock = threading.RLock()            # agent sink는 워커 스레드에서 호출된다
    t0 = time.perf_counter()

    def ms() -> int:
        return round((time.perf_counter() - t0) * 1000)

    def event_span(ev: dict) -> tuple[str, str] | None:
        """LangGraph v2 이벤트를 실제 부모 노드에 귀속시킨다.

        병렬 노드가 두 개 이상 열려 있을 때 임의의 첫 스팬을 고르지 않는다.
        parent_ids의 가장 가까운 열린 실행을 우선하고, 없으면 metadata의
        노드에서 가장 최근에 열린 실행을 찾는다.
        """
        metadata = ev.get("metadata") or {}
        with trace_lock:
            for parent_id in reversed(ev.get("parent_ids") or []):
                span = open_spans.get(parent_id)
                if span is not None:
                    return span["node_id"], parent_id
            node = metadata.get("langgraph_node")
            runs = active_runs.get(node, [])
            if node in node_types and runs:
                return node, runs[-1]
            if len(open_spans) == 1:  # 순차 그래프의 예전 이벤트 형식 호환
                run_id, span = next(iter(open_spans.items()))
                return span["node_id"], run_id
        return None

    def sink(node_id: str, kind: str, data: dict) -> None:
        """agent 워커 스레드에서 호출된다 — 반드시 스레드 안전해야 한다."""
        ev = {"type": "agent_event", "node_id": node_id, "kind": kind,
              "at_ms": ms(), **_clip(data)}
        with trace_lock:
            runs = active_runs.get(node_id, [])
            run_id = runs[-1] if runs else None
            if run_id:
                if kind == "usage":
                    u = span_usage.setdefault(
                        run_id, {"prompt_tokens": 0, "completion_tokens": 0}
                    )
                    u["prompt_tokens"] += data.get("prompt_tokens", 0)
                    u["completion_tokens"] += data.get("completion_tokens", 0)
                span_sessions.setdefault(run_id, []).append(ev)
        loop.call_soon_threadsafe(q.put_nowait, ev)

    async def pump():
        try:
            async for ev in graph.astream_events(
                state, version="v2",
                config={"recursion_limit": recursion_limit,
                        "configurable": {"event_sink": sink, "approver": approver,
                                         "cancel_event": cancel_event}},
            ):
                name, kind, run_id = ev.get("name"), ev.get("event"), ev.get("run_id")

                # 우리가 정의한 노드만 스팬으로 만든다 (LangGraph 내부 러너블 제외)
                if name in node_types and kind == "on_chain_start":
                    span = {
                        "id": uuid.uuid4().hex[:12],
                        "node_id": name,
                        "status": "running",
                        "started_ms": ms(),
                        "input": _clip((ev.get("data") or {}).get("input", {}).get("outputs", {})),
                    }
                    with trace_lock:
                        open_spans[run_id] = span
                        active_runs.setdefault(name, []).append(run_id)
                        span_sessions[run_id] = []
                    await q.put({"type": "span_start", "span": span})

                elif name in node_types and kind == "on_chain_end":
                    with trace_lock:
                        span = open_spans.pop(run_id, None)
                        events = span_sessions.pop(run_id, [])
                        own_usage = span_usage.pop(run_id, None)
                        runs = active_runs.get(name, [])
                        if run_id in runs:
                            runs.remove(run_id)
                        if not runs:
                            active_runs.pop(name, None)
                    if span is None:
                        continue
                    out = (ev.get("data") or {}).get("output") or {}
                    own = (out.get("outputs") or {}).get(name, {})
                    # tool handler는 실패를 예외 대신 {"error": ...}로 돌려준다.
                    # LangGraph 자체는 정상 종료로 보므로 여기서 tool 스팬만
                    # 명시적으로 실패로 바꾸어야 UI가 거짓 성공을 표시하지 않는다.
                    tool_failed = node_types.get(name) == "tool" and any(
                        isinstance(value, dict) and bool(value.get("error"))
                        for value in own.values()
                    )
                    span.update(status="error" if tool_failed else "success",
                                duration_ms=ms() - span["started_ms"],
                                output=_clip(own),
                                events=events,
                                usage=own_usage)
                    await q.put({"type": "span_end", "span": span})

                elif kind == "on_chat_model_end":
                    target = event_span(ev)
                    out = (ev.get("data") or {}).get("output")
                    um = getattr(out, "usage_metadata", None)
                    if target and um:
                        _, target_run = target
                        with trace_lock:
                            u = span_usage.setdefault(
                                target_run, {"prompt_tokens": 0, "completion_tokens": 0}
                            )
                            u["prompt_tokens"] += um.get("input_tokens", 0)
                            u["completion_tokens"] += um.get("output_tokens", 0)

                elif kind == "on_chat_model_stream":
                    # llm 노드의 토큰. astream_events는 langchain Runnable(=llm 노드)만
                    # 본다 — agent 노드는 raw openai 클라이언트라 여기 안 잡힌다.
                    # 단, agent 노드가 자기 sink로 text_delta를 넣었다면 그건 스킵(중복 방지).
                    chunk = (ev.get("data") or {}).get("chunk")
                    text = getattr(chunk, "content", "") or ""
                    target = event_span(ev)
                    node, target_run = target if target else (None, None)
                    with trace_lock:
                        has_own = any(
                            e["kind"] == "text_delta"
                            for e in span_sessions.get(target_run, [])
                        )
                    if node and text and not has_own:
                        await q.put({"type": "token", "node_id": node, "text": text})

        except asyncio.CancelledError:
            # Stop — 에러가 아니다. 열린 스팬을 cancelled로 닫는다.
            for run_id, span in open_spans.items():
                span.update(status="error", duration_ms=ms() - span["started_ms"],
                            output={"error": "사용자가 실행을 중단함"},
                            events=span_sessions.get(run_id, []),
                            usage=span_usage.get(run_id))
                await q.put({"type": "span_end", "span": span})
            await q.put({"type": "run_end", "duration_ms": ms(), "cancelled": True})
        except Exception as e:
            for run_id, span in open_spans.items():
                span.update(status="error", duration_ms=ms() - span["started_ms"],
                            output={"error": f"{type(e).__name__}: {e}"},
                            events=span_sessions.get(run_id, []),
                            usage=span_usage.get(run_id))
                await q.put({"type": "span_end", "span": span})
            await q.put({"type": "run_error", "error": f"{type(e).__name__}: {e}"})
        else:
            await q.put({"type": "run_end", "duration_ms": ms()})
        finally:
            await q.put(None)   # 종료 신호

    task = asyncio.create_task(pump())
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            yield item
    finally:
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)
