"""그래프 스펙 -> LangGraph StateGraph.

상태는 {"outputs": {node_id: {field: value}}} 하나뿐이다. 노드는 자기 몫만 쓰고,
리듀서가 병합한다 — 병렬 분기가 있어도 서로 덮어쓰지 않는다.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from typing import Annotated, Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from . import agent as agent_mod
from . import spec as spec_mod
from . import tools as tool_mod

RECURSION_LIMIT = 50  # 순환은 허용하되 무한루프는 여기서 끊는다

# provider -> OpenAI 호환 엔드포인트. 로컬 MLX 서버가 기본값이다.
PROVIDERS = {
    "local": {"base_url": "http://localhost:8080/v1", "api_key": "none"},
    "openai": {"base_url": None, "api_key_env": "OPENAI_API_KEY"},
}

# UI의 짧은 이름 -> 로컬에 실제로 존재하는 스냅샷 경로.
#
# 절대 repo ID("orcarouter/Qwen3.8-...")를 보내면 안 된다. mlx_vlm.server는 로드되지
# 않은 모델 id를 받으면 HuggingFace에서 **리포 전체를**(모든 quant, ~80GB) 내려받기
# 시작하고, 그동안 요청은 응답 없이 매달린다. 로컬 경로만 넘긴다.
LOCAL_MODELS = {
    "qwen3.8-27b": "~/.cache/huggingface/hub/"
                   "models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit",
}


def resolve_local_model(name: str) -> str:
    import glob as _g
    pattern = LOCAL_MODELS.get(name)
    if pattern is None:
        raise spec_mod.SpecError(
            f"provider=local 인데 모르는 모델 {name!r} (등록됨: {sorted(LOCAL_MODELS)})"
        )
    hits = _g.glob(os.path.expanduser(pattern))
    if not hits:
        raise spec_mod.SpecError(
            f"{name!r}의 로컬 파일을 찾을 수 없습니다: {pattern}\n"
            "  먼저 받으세요: hf download orcarouter/Qwen3.8-27B-Uncensored-MLX --include '4-bit/*'"
        )
    return hits[0]


def merge(a: dict, b: dict) -> dict:
    return {**a, **b}


class State(TypedDict):
    outputs: Annotated[dict, merge]


# ─────────────────────────── 노드 빌더 ───────────────────────────


def _resolve_inputs(node: dict, state: State) -> dict:
    outs = state.get("outputs", {})
    return {k: spec_mod.resolve(v, outs) for k, v in (node.get("inputs") or {}).items()}


def _make_start(node: dict):
    """start는 실행 시 주어진 입력을 자기 출력 필드로 옮긴다."""
    fields = list(node.get("outputs") or [])
    nid = node["id"]

    def run(state: State) -> dict:
        given = state.get("outputs", {}).get(nid, {})
        return {"outputs": {nid: {f: given.get(f) for f in fields}}}

    return run


def _model_conf(node: dict) -> dict:
    """provider -> {model, base_url, api_key}. local은 반드시 로컬 경로로 해석한다."""
    nid = node["id"]
    m = node.get("model") or {}
    provider = m.get("provider", "local")
    if provider not in PROVIDERS:
        raise spec_mod.SpecError(f"{nid}: 알 수 없는 provider {provider!r}")
    p = PROVIDERS[provider]

    if p.get("base_url"):
        # repo ID를 보내면 mlx_vlm.server가 리포 전체를 재다운로드한다. 로컬 경로만 넘긴다.
        return {"model": resolve_local_model(m.get("name")),
                "base_url": p["base_url"], "api_key": p["api_key"]}
    key = os.environ.get(p["api_key_env"])
    if not key:
        raise spec_mod.SpecError(
            f"{nid}: provider={provider} 인데 {p['api_key_env']} 환경변수가 없습니다")
    return {"model": m.get("name"), "base_url": None, "api_key": key}


def _make_llm(node: dict):
    from langchain_openai import ChatOpenAI

    nid = node["id"]
    out_field = node["output"]["name"]
    m = node.get("model") or {}
    conf = _model_conf(node)

    kwargs: dict[str, Any] = {"temperature": m.get("temperature", 0.0),
                              "model": conf["model"], "api_key": conf["api_key"]}
    if m.get("max_tokens"):
        kwargs["max_tokens"] = m["max_tokens"]
    if conf["base_url"]:
        kwargs["base_url"] = conf["base_url"]

    llm = ChatOpenAI(**kwargs)
    system = node.get("system_prompt")
    prompt_tpl = node.get("prompt")

    async def run(state: State) -> dict:
        values = _resolve_inputs(node, state)
        if prompt_tpl:
            user = spec_mod.resolve(prompt_tpl, state.get("outputs", {}))
        else:
            # prompt가 없으면 inputs를 그대로 보여준다
            user = "\n".join(f"{k}: {v}" for k, v in values.items() if v is not None)
        msgs = ([("system", system)] if system else []) + [("user", user or "")]
        resp = await llm.ainvoke(msgs)
        return {"outputs": {nid: {out_field: resp.content}}}

    return run


def _make_tool(node: dict):
    nid = node["id"]
    name = node["tool"]
    if name not in tool_mod.REGISTRY:
        raise spec_mod.SpecError(
            f"{nid}: 알 수 없는 도구 {name!r} (가능: {sorted(tool_mod.REGISTRY)})"
        )
    out_field = (node.get("output") or {}).get("name") or "result"

    def run(state: State) -> dict:
        args = {k: v for k, v in _resolve_inputs(node, state).items() if v is not None}
        try:
            value = tool_mod.dispatch(name, args)
        except Exception as e:  # 도구 실패로 그래프 전체를 죽이지 않는다
            value = f"ERROR: {type(e).__name__}: {e}"
        return {"outputs": {nid: {out_field: value}}}

    return run


def _make_end(node: dict):
    nid = node["id"]

    def run(state: State) -> dict:
        return {"outputs": {nid: _resolve_inputs(node, state)}}

    return run


def _make_agent(node: dict):
    """세션을 가진 노드. 몇 분씩 돌 수 있으므로 이벤트를 실행 중에 흘려보낸다.

    LangGraph는 노드 안에서 벌어지는 일을 모른다. 그래서 config로 받은 sink에
    직접 밀어넣는다 — 끝나고 몰아서 주면 화면이 멈춘 것처럼 보인다.
    """
    from openai import OpenAI

    nid = node["id"]
    out_field = node["output"]["name"]
    m = node.get("model") or {}
    conf = _model_conf(node)
    client = OpenAI(base_url=conf["base_url"] or None, api_key=conf["api_key"])

    tool_names = list(node.get("tools") or [])
    system = node.get("system_prompt") or "You are a helpful agent."
    approval = node.get("approval", "auto")
    max_steps = node.get("max_steps", agent_mod.DEFAULT_MAX_STEPS)

    async def run(state: State, config: RunnableConfig = None) -> dict:
        cfg = (config or {}).get("configurable", {}) or {}
        sink = cfg.get("event_sink")        # (node_id, kind, data) -> None, 스레드 안전
        approver = cfg.get("approver")      # (node_id, tool, args) -> bool, 블로킹

        def emit(kind, **d):
            if sink:
                sink(nid, kind, d)

        if approval == "auto":
            approve = lambda *_: True                      # noqa: E731
        elif approver is not None:
            approve = lambda name, args: approver(nid, name, args)   # noqa: E731
        else:
            # ask인데 물어볼 상대가 없다 — 거부가 안전한 기본값이다
            approve = lambda *_: False                     # noqa: E731

        values = _resolve_inputs(node, state)
        task = "\n".join(f"{k}: {v}" for k, v in values.items() if v is not None)

        text, _events = await asyncio.to_thread(
            agent_mod.run,
            client=client, model=conf["model"], system_prompt=system,
            task=task or "(no input)", tool_names=tool_names,
            approve=approve, emit=emit, max_steps=max_steps,
        )
        return {"outputs": {nid: {out_field: text}}}

    return run


BUILDERS = {"start": _make_start, "llm": _make_llm, "tool": _make_tool,
            "end": _make_end, "agent": _make_agent}


# ─────────────────────────── 컴파일 ───────────────────────────


def build(spec: dict):
    """검증된 스펙을 컴파일한다. 스펙 오류는 SpecError로 먼저 걸러진다."""
    spec_mod.validate(spec)

    nodes = {n["id"]: n for n in spec["nodes"]}
    edges = spec.get("edges", [])
    g = StateGraph(State)

    for nid, node in nodes.items():
        g.add_node(nid, BUILDERS[node["type"]](node))

    start_id = next(n["id"] for n in spec["nodes"] if n["type"] == "start")
    g.add_edge(START, start_id)

    for nid in nodes:
        out = [e for e in edges if e["from"] == nid]
        if not out:
            if nodes[nid]["type"] == "end":
                g.add_edge(nid, END)
            continue

        cond = [e for e in out if e.get("when") is not None]
        if not cond:
            for e in out:  # 여러 개면 병렬 분기 (리듀서가 병합)
                g.add_edge(nid, e["to"])
            continue

        # default는 항상 마지막에 평가한다 — 스펙에 적힌 순서와 무관하게
        ordered = ([e for e in cond if e["when"].strip() != "default"]
                   + [e for e in cond if e["when"].strip() == "default"])
        checks = [(spec_mod.parse_when(e["when"]), e["to"]) for e in ordered]

        def path(state: State, _nid=nid, _checks=checks) -> str:
            outs = state.get("outputs", {}).get(_nid, {})
            for check, target in _checks:
                if check(outs):
                    return target
            return END  # 어느 조건도 안 맞으면 종료

        g.add_conditional_edges(nid, path, [t for _, t in checks] + [END])

    return g.compile()


def initial_state(spec: dict, inputs: dict) -> State:
    """실행 입력을 start 노드의 출력 자리에 넣는다."""
    start_id = next(n["id"] for n in spec["nodes"] if n["type"] == "start")
    return {"outputs": {start_id: dict(inputs)}}


def final_output(spec: dict, state: State) -> dict:
    end_ids = [n["id"] for n in spec["nodes"] if n["type"] == "end"]
    outs = state.get("outputs", {})
    return {eid: outs.get(eid, {}) for eid in end_ids}


# ─────────────────────────── CLI ───────────────────────────


def main():
    ap = argparse.ArgumentParser(description="그래프 스펙 검증/컴파일")
    ap.add_argument("path")
    ap.add_argument("--check", action="store_true", help="검증과 컴파일만 하고 실행하지 않음")
    a = ap.parse_args()

    try:
        s = spec_mod.load(a.path)
    except spec_mod.SpecError as e:
        print(f"✗ {e}", file=sys.stderr)
        sys.exit(1)

    print(f"✓ 스펙 유효: {s.get('name')} — 노드 {len(s['nodes'])}, 엣지 {len(s.get('edges', []))}")
    try:
        build(s)
    except spec_mod.SpecError as e:
        print(f"✗ 컴파일 실패: {e}", file=sys.stderr)
        sys.exit(1)
    print("✓ LangGraph 컴파일 성공")


if __name__ == "__main__":
    main()
