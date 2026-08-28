"""turn/step 에이전트 루프 + 이벤트 로그 세션.

이 모듈은 UI를 모른다. 표시와 승인은 콜백(emit, approve)으로 빠진다.
나중에 TS UI를 붙일 때 이 파일은 건드리지 않는 게 목표다.
"""

import glob as _glob
import json
import os

from openai import OpenAI

from . import tools

BASE_URL = "http://localhost:8080/v1"
MAX_STEPS = 25
CIRCUIT_BREAK = 3  # 같은 도구가 연속 N회 실패하면 중단


def _hub_root() -> str:
    """HF 캐시 루트 — hf CLI와 같은 우선순위. 홈 경로를 하드코딩하지 않는다."""
    if os.environ.get("HF_HUB_CACHE"):
        return os.environ["HF_HUB_CACHE"]
    if os.environ.get("HF_HOME"):
        return os.path.join(os.environ["HF_HOME"], "hub")
    return os.path.expanduser("~/.cache/huggingface/hub")


def model_path() -> str:
    hits = _glob.glob(os.path.join(
        _hub_root(),
        "models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit",
    ))
    if not hits:
        raise RuntimeError("모델을 찾을 수 없음. hf download 로 4-bit를 먼저 받을 것.")
    return hits[0]


SYSTEM_PROMPT = f"""You are a coding agent working in the user's current directory.

Work by calling tools. When the task is done, reply with plain text and no tool call —
that is how you end your turn.

Tools:
{tools.GUIDANCE}

Rules:
- Read a file before you edit it.
- Prefer edit_file over write_file for existing files.
- After changing code, run it or its tests to verify.
- Be brief. Do not narrate what you are about to do; just do it.
"""


class Session:
    """append-only 이벤트 로그. 모델용 메시지는 여기서 파생된다.

    events가 단일 진실 원천이다. CLI는 이걸 읽어 출력하고, 나중에 UI는 같은 로그를
    읽어 diff를 그린다. tool_result 이벤트는 도구가 반환한 dict 원본을 그대로 보관한다.
    """

    def __init__(self):
        self.events: list[dict] = []

    def append(self, kind: str, **data):
        self.events.append({"kind": kind, **data})

    def derive_messages(self) -> list[dict]:
        msgs = [{"role": "system", "content": SYSTEM_PROMPT}]
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
                msgs.append({
                    "role": "tool",
                    "tool_call_id": e["tool_call_id"],
                    "content": tools.render(e["name"], e["value"]),
                })
        return msgs


def _assemble_stream(stream, emit) -> tuple[str, list[dict]]:
    """스트리밍 청크를 (텍스트, tool_calls)로 조립."""
    text_parts: list[str] = []
    calls: dict[int, dict] = {}

    for chunk in stream:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            text_parts.append(delta.content)
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

    return "".join(text_parts), [calls[i] for i in sorted(calls)]


def run_turn(session: Session, user_input: str, approve, emit,
             client: OpenAI | None = None, max_steps: int = MAX_STEPS) -> None:
    """사용자 요청 하나를 처리한다.

    approve(name, args) -> bool : 승인 필요한 도구를 실행하기 전 호출
    emit(kind, **data)          : 표시용. 로직에 영향을 주지 않는다
    """
    client = client or OpenAI(base_url=BASE_URL, api_key="none")
    model = model_path()
    session.append("user", content=user_input)

    fail_streak: dict[str, int] = {}

    for step in range(max_steps):
        emit("step", n=step + 1)
        stream = client.chat.completions.create(
            model=model,
            messages=session.derive_messages(),
            tools=tools.TOOL_SCHEMAS,
            stream=True,
        )
        text, calls = _assemble_stream(stream, emit)
        session.append("assistant", content=text, tool_calls=calls or None)

        if not calls:
            emit("done", reason="no_tool_calls")
            return

        for call in calls:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except json.JSONDecodeError as e:
                value = {"error": f"인자 JSON 파싱 실패: {e}"}
                session.append("tool_result", tool_call_id=call["id"],
                               name=name, value=value)
                emit("tool_result", name=name, value=value)
                continue

            emit("tool_start", name=name, args=args)

            spec = tools.BY_NAME.get(name)
            if spec and spec["needs_approval"] and not approve(name, args):
                value = {"error": "사용자가 이 도구 실행을 거부함"}
            else:
                value = tools.dispatch(name, args)

            session.append("tool_result", tool_call_id=call["id"],
                           name=name, value=value)
            emit("tool_result", name=name, value=value)

            # 서킷 브레이커 — 로컬 모델이 같은 실수를 반복하는 걸 끊는다
            if "error" in value:
                fail_streak[name] = fail_streak.get(name, 0) + 1
                if fail_streak[name] >= CIRCUIT_BREAK:
                    emit("done", reason=f"circuit_break:{name}")
                    return
            else:
                fail_streak[name] = 0

    emit("done", reason="max_steps")
