"""에이전트 런타임 — 세션을 가진 노드 하나.

`qwen3.8mlx/agent/loop.py`에서 이식했다. 바뀐 점:
  - 도구를 통합 레지스트리에서 가져오고, 에이전트마다 **부분집합**만 갖는다
  - 모델 클라이언트는 호출자(runtime.py)가 만들어 넘긴다 (순환 임포트 회피)
  - emit이 UI로 흘러갈 것을 전제로 이벤트 이름을 고정한다

이 모듈은 UI도 그래프도 모른다. 표시와 승인은 콜백으로 빠진다.
"""

from __future__ import annotations

import json
import threading
from typing import Callable

from openai import OpenAI

from . import tools as T

DEFAULT_MAX_STEPS = 15
CIRCUIT_BREAK = 3  # 같은 도구가 연속 N회 실패하면 중단


class Session:
    """append-only 이벤트 로그. 모델용 메시지는 여기서 파생된다.

    events가 단일 진실 원천이다. UI는 이 로그를 읽어 트랜스크립트를 그린다.
    tool_result 이벤트는 도구가 반환한 dict 원본을 그대로 보관한다.
    """

    def __init__(self, system_prompt: str, registry: dict | None = None):
        self.system_prompt = system_prompt
        self.events: list[dict] = []
        self.registry = registry  # 실행별 도구(create_worker 등)의 렌더러를 찾기 위해

    def append(self, kind: str, **data):
        self.events.append({"kind": kind, **data})

    def derive_messages(self) -> list[dict]:
        msgs = [{"role": "system", "content": self.system_prompt}]
        for e in self.events:
            if e["kind"] == "user":
                msgs.append({"role": "user", "content": e["content"]})
            elif e["kind"] == "assistant":
                m = {"role": "assistant", "content": e.get("content") or ""}
                if e.get("tool_calls"):
                    m["tool_calls"] = e["tool_calls"]
                msgs.append(m)
            elif e["kind"] == "tool_result":
                # 원본 dict는 e["value"]에 남고, 모델에겐 렌더링된 텍스트만 보낸다
                msgs.append({"role": "tool", "tool_call_id": e["tool_call_id"],
                             "content": T.render(e["name"], e["value"],
                                                 registry=self.registry)})
        return msgs


def build_system_prompt(base: str, tool_names: list[str],
                        registry: dict | None = None) -> str:
    """도구별 guidance를 시스템 프롬프트에 합친다 — 규칙을 도구 옆에 두는 패턴."""
    if not tool_names:
        return base + "\n\nYou have no tools. Answer directly."
    return (
        f"{base}\n\n"
        "Work by calling tools. When the task is done, reply with plain text and no "
        "tool call — that is how you finish.\n\n"
        f"Tools:\n{T.guidance_for(tool_names, registry=registry)}\n\n"
        "Be brief. Do not narrate what you are about to do; just do it."
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
    approve: Callable[[str, dict], bool],
    emit: Callable[..., None],
    max_steps: int = DEFAULT_MAX_STEPS,
    cancel: threading.Event | None = None,
    extra_tools: list[dict] | None = None,
    session: Session | None = None,
) -> tuple[str, list[dict]]:
    """에이전트를 한 턴 돌린다.

    반환: (마지막 assistant 텍스트, 이벤트 로그)

    approve(name, args) -> bool : 승인 필요한 도구를 실행하기 전 호출 (블로킹)
    emit(kind, **data)          : 표시용. 로직에 영향을 주지 않는다
    extra_tools                 : _t() 모양 dict들 — 실행별 도구(create_worker) 주입
    session                     : 지속 Session을 넘기면 이어서 대화한다 (멀티턴)
    """
    reg = dict(T.REGISTRY)
    reg.update({t["name"]: t for t in (extra_tools or [])})
    tool_names = [n for n in tool_names if n in reg]
    if session is None:
        session = Session(build_system_prompt(system_prompt, tool_names, registry=reg),
                          registry=reg)
    session.append("user", content=task)
    emit("user", content=task)

    schemas = T.schemas_for(tool_names, registry=reg)
    fail_streak: dict[str, int] = {}
    last_text = ""
    tok_prompt = tok_completion = 0

    for step in range(max_steps):
        if cancel is not None and cancel.is_set():
            emit("done", reason="cancelled")
            return last_text, session.events

        emit("step", n=step + 1)
        msgs = session.derive_messages()
        # 실제 전송분을 트레이스에. 매 step 전체를 실으면 스팬이 비대해지므로
        # 첫 step만 전체, 이후엔 직전 assistant/tool 응답 이후의 증분(마지막 2개)만.
        emit("llm_call", messages=msgs if step == 0 else msgs[-2:],
             total_messages=len(msgs))
        kwargs = {"model": model, "messages": msgs, "stream": True,
                  "stream_options": {"include_usage": True}}
        if schemas:
            kwargs["tools"] = schemas
        text, calls, usage = _assemble(client.chat.completions.create(**kwargs), emit, cancel)
        if usage:
            tok_prompt += usage["prompt_tokens"]
            tok_completion += usage["completion_tokens"]
            # step별 토큰 — 어느 step이 컨텍스트를 부풀려 느려지는지 보인다
            emit("usage", step=step + 1, **usage)

        if cancel is not None and cancel.is_set():
            emit("done", reason="cancelled")
            return last_text, session.events

        session.append("assistant", content=text, tool_calls=calls or None)
        if text:
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
            emit("tool_start", name=name, args=args, call_id=call["id"])
            # 승인 강제는 모든 호출 경로가 공유하는 dispatch의 책임이다.
            # 여기서 미리 검사하면 다른 호출 경로가 또 뚫린다.
            return name, T.dispatch(name, args, approve=approve, registry=reg)

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

    emit("done", reason="max_steps")
    return last_text, session.events
