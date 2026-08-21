"""실행 -> 스팬 + 세션 이벤트 스트림.

두 갈래를 하나로 합친다:
  1. LangGraph의 astream_events → 노드 경계(span_start/span_end)
  2. agent 노드가 sink로 밀어넣는 세션 이벤트 → 그 노드 안에서 벌어지는 일

에이전트 하나가 몇 분씩 돌 수 있으므로 (2)는 발생 즉시 나가야 한다. 끝나고 몰아서
주면 화면이 멈춘 것처럼 보인다.
"""

from __future__ import annotations

import asyncio
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
    node_ids: set[str],
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
    sessions: dict[str, list[dict]] = {}      # node_id -> 세션 이벤트 누적
    t0 = time.perf_counter()

    def ms() -> int:
        return round((time.perf_counter() - t0) * 1000)

    def sink(node_id: str, kind: str, data: dict) -> None:
        """agent 워커 스레드에서 호출된다 — 반드시 스레드 안전해야 한다."""
        ev = {"type": "agent_event", "node_id": node_id, "kind": kind,
              "at_ms": ms(), **_clip(data)}
        sessions.setdefault(node_id, []).append(ev)
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
                if name in node_ids and kind == "on_chain_start":
                    span = {
                        "id": uuid.uuid4().hex[:12],
                        "node_id": name,
                        "status": "running",
                        "started_ms": ms(),
                        "input": _clip((ev.get("data") or {}).get("input", {}).get("outputs", {})),
                    }
                    open_spans[run_id] = span
                    await q.put({"type": "span_start", "span": span})

                elif name in node_ids and kind == "on_chain_end":
                    span = open_spans.pop(run_id, None)
                    if span is None:
                        continue
                    out = (ev.get("data") or {}).get("output") or {}
                    own = (out.get("outputs") or {}).get(name, {})
                    span.update(status="success",
                                duration_ms=ms() - span["started_ms"],
                                output=_clip(own),
                                events=sessions.get(name, []))
                    await q.put({"type": "span_end", "span": span})

                elif kind == "on_chat_model_stream":
                    # llm 노드의 토큰. agent 노드는 자기 sink로 따로 보낸다.
                    chunk = (ev.get("data") or {}).get("chunk")
                    text = getattr(chunk, "content", "") or ""
                    node = next((s["node_id"] for s in open_spans.values()), None)
                    if text and node not in sessions:
                        await q.put({"type": "token", "node_id": node, "text": text})

        except asyncio.CancelledError:
            # Stop — 에러가 아니다. 열린 스팬을 cancelled로 닫는다.
            for span in open_spans.values():
                span.update(status="error", duration_ms=ms() - span["started_ms"],
                            output={"error": "사용자가 실행을 중단함"},
                            events=sessions.get(span["node_id"], []))
                await q.put({"type": "span_end", "span": span})
            await q.put({"type": "run_end", "duration_ms": ms(), "cancelled": True})
        except Exception as e:
            for span in open_spans.values():
                span.update(status="error", duration_ms=ms() - span["started_ms"],
                            output={"error": f"{type(e).__name__}: {e}"},
                            events=sessions.get(span["node_id"], []))
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
