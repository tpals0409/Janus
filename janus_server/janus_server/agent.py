"""에이전트 런타임 — 세션을 가진 노드 하나.

`qwen3.8mlx/agent/loop.py`에서 이식했다. 바뀐 점:
  - 도구를 통합 레지스트리에서 가져오고, 에이전트마다 **부분집합**만 갖는다
  - 모델 클라이언트는 호출자(compile.py)가 만들어 넘긴다 (순환 임포트 회피)
  - emit이 UI로 흘러갈 것을 전제로 이벤트 이름을 고정한다

이 모듈은 UI도 그래프도 모른다. 표시와 승인은 콜백으로 빠진다.
"""

from __future__ import annotations

import json
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

    def __init__(self, system_prompt: str):
        self.system_prompt = system_prompt
        self.events: list[dict] = []

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
                             "content": T.render(e["name"], e["value"])})
        return msgs


def build_system_prompt(base: str, tool_names: list[str]) -> str:
    """도구별 guidance를 시스템 프롬프트에 합친다 — 규칙을 도구 옆에 두는 패턴."""
    if not tool_names:
        return base + "\n\nYou have no tools. Answer directly."
    return (
        f"{base}\n\n"
        "Work by calling tools. When the task is done, reply with plain text and no "
        "tool call — that is how you finish.\n\n"
        f"Tools:\n{T.guidance_for(tool_names)}\n\n"
        "Be brief. Do not narrate what you are about to do; just do it."
    )


def _assemble(stream, emit) -> tuple[str, list[dict]]:
    """스트리밍 청크를 (텍스트, tool_calls)로 조립."""
    parts: list[str] = []
    calls: dict[int, dict] = {}

    for chunk in stream:
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

    return "".join(parts), [calls[i] for i in sorted(calls)]


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
) -> tuple[str, list[dict]]:
    """에이전트를 한 번 돌린다.

    반환: (마지막 assistant 텍스트, 이벤트 로그)

    approve(name, args) -> bool : 승인 필요한 도구를 실행하기 전 호출 (블로킹)
    emit(kind, **data)          : 표시용. 로직에 영향을 주지 않는다
    """
    tool_names = [n for n in tool_names if n in T.REGISTRY]
    session = Session(build_system_prompt(system_prompt, tool_names))
    session.append("user", content=task)
    emit("user", content=task)

    schemas = T.schemas_for(tool_names)
    fail_streak: dict[str, int] = {}
    last_text = ""

    for step in range(max_steps):
        emit("step", n=step + 1)
        kwargs = {"model": model, "messages": session.derive_messages(), "stream": True}
        if schemas:
            kwargs["tools"] = schemas
        text, calls = _assemble(client.chat.completions.create(**kwargs), emit)

        session.append("assistant", content=text, tool_calls=calls or None)
        if text:
            last_text = text
            emit("assistant", content=text)

        if not calls:
            emit("done", reason="no_tool_calls")
            return last_text, session.events

        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError as e:
                value = {"error": f"인자 JSON 파싱 실패: {e}"}
                session.append("tool_result", tool_call_id=call["id"], name=name, value=value)
                emit("tool_result", name=name, value=value)
                continue

            emit("tool_start", name=name, args=args)

            spec = T.REGISTRY.get(name)
            if spec and spec["needs_approval"] and not approve(name, args):
                value = {"error": "사용자가 이 도구 실행을 거부함"}
            else:
                value = T.dispatch(name, args)

            session.append("tool_result", tool_call_id=call["id"], name=name, value=value)
            emit("tool_result", name=name, value=value)

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
