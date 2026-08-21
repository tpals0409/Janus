"""그래프 스펙 — 캔버스·YAML 뷰·실행 엔진이 전부 바라보는 단일 데이터 모델.

상태 모양: {"outputs": {node_id: {field: value}}}
템플릿 `{{ node.field }}`는 그 상태에서 해석된다.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

NODE_TYPES = {"start", "llm", "tool", "end", "agent"}
APPROVAL_MODES = {"auto", "ask"}

# {{ node.field }} — 공백 허용
TEMPLATE_RE = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)\.([A-Za-z_]\w*)\s*\}\}")

# when: "<field> == 'literal'" / "!=" / "default"
WHEN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*(==|!=)\s*(.+?)\s*$")


class SpecError(ValueError):
    """스펙이 유효하지 않음. 메시지에 모든 문제를 담는다."""


# ─────────────────────────── 로드 / 저장 ───────────────────────────


def load(path: str | Path) -> dict:
    spec = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    validate(spec)
    return spec


def dumps(spec: dict) -> str:
    return yaml.safe_dump(spec, allow_unicode=True, sort_keys=False, width=100)


# ─────────────────────────── 템플릿 ───────────────────────────


def refs(template: Any) -> list[tuple[str, str]]:
    """템플릿 문자열이 참조하는 (node_id, field) 목록."""
    if not isinstance(template, str):
        return []
    return TEMPLATE_RE.findall(template)


def resolve(template: Any, outputs: dict) -> Any:
    """{{ node.field }}를 outputs에서 치환.

    문자열 전체가 하나의 참조면 원래 타입을 보존한다 (dict/list도 통과).
    섞여 있으면 문자열로 보간한다.
    """
    if not isinstance(template, str):
        return template

    whole = TEMPLATE_RE.fullmatch(template.strip())
    if whole:
        node_id, field = whole.groups()
        return outputs.get(node_id, {}).get(field)

    def sub(m):
        node_id, field = m.groups()
        v = outputs.get(node_id, {}).get(field)
        return "" if v is None else str(v)

    return TEMPLATE_RE.sub(sub, template)


# ─────────────────────────── when 조건 ───────────────────────────


def parse_when(expr: str):
    """when 문자열 -> (source 노드의 outputs) -> bool 인 함수.

    파이썬 eval을 쓰지 않는다. `<field> ==|!= <literal>` 과 `default`만 허용한다 —
    스펙 파일이 임의 코드 실행 경로가 되면 안 된다.
    """
    if expr.strip() == "default":
        return lambda _outputs: True

    m = WHEN_RE.match(expr)
    if not m:
        raise SpecError(
            f"when 파싱 실패: {expr!r} "
            "(허용: \"field == 'value'\", \"field != 'value'\", \"default\")"
        )
    field, op, raw = m.groups()
    literal = yaml.safe_load(raw)  # 따옴표/숫자/불리언을 파이썬 값으로

    def check(outputs: dict) -> bool:
        actual = outputs.get(field)
        return actual == literal if op == "==" else actual != literal

    return check


# ─────────────────────────── 검증 ───────────────────────────


def validate(spec: Any) -> None:
    """문제를 전부 모아 한 번에 던진다. 하나씩 고치게 만들지 않는다."""
    errs: list[str] = []

    if not isinstance(spec, dict):
        raise SpecError("스펙 최상위는 매핑이어야 합니다")

    nodes = spec.get("nodes")
    edges = spec.get("edges", [])
    if not isinstance(nodes, list) or not nodes:
        raise SpecError("nodes가 비어 있거나 리스트가 아닙니다")
    if not isinstance(edges, list):
        raise SpecError("edges가 리스트가 아닙니다")

    ids: list[str] = []
    for i, n in enumerate(nodes):
        if not isinstance(n, dict) or "id" not in n:
            errs.append(f"nodes[{i}]: id 없음")
            continue
        ids.append(n["id"])
        t = n.get("type")
        if t not in NODE_TYPES:
            errs.append(f"{n['id']}: 알 수 없는 type {t!r} (허용: {sorted(NODE_TYPES)})")
        if t == "llm" and not n.get("output", {}).get("name"):
            errs.append(f"{n['id']}: llm 노드에 output.name이 필요합니다")
        if t == "tool" and not n.get("tool"):
            errs.append(f"{n['id']}: tool 노드에 tool 이름이 필요합니다")
        if t == "agent":
            errs.extend(_check_agent(n))

    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        errs.append(f"중복된 노드 id: {sorted(dupes)}")

    idset = set(ids)
    starts = [n["id"] for n in nodes if isinstance(n, dict) and n.get("type") == "start"]
    if len(starts) != 1:
        errs.append(f"start 노드는 정확히 1개여야 합니다 (현재 {len(starts)}개)")
    if not any(n.get("type") == "end" for n in nodes if isinstance(n, dict)):
        errs.append("end 노드가 없습니다")

    # 엣지 참조 + when 문법
    for i, e in enumerate(edges):
        if not isinstance(e, dict) or "from" not in e or "to" not in e:
            errs.append(f"edges[{i}]: from/to 필요")
            continue
        for side in ("from", "to"):
            if e[side] not in idset:
                errs.append(f"edges[{i}]: {side}={e[side]!r} 는 없는 노드입니다")
        if e.get("when") is not None:
            try:
                parse_when(e["when"])
            except SpecError as ex:
                errs.append(f"edges[{i}]: {ex}")

    # 한 노드에서 조건부와 무조건 엣지가 섞이면 분기 의미가 모호하다
    for nid in idset:
        out = [e for e in edges if isinstance(e, dict) and e.get("from") == nid]
        cond = [e for e in out if e.get("when") is not None]
        if cond and len(cond) != len(out):
            errs.append(
                f"{nid}: 나가는 엣지에 when이 있는 것과 없는 것이 섞였습니다 "
                "(전부 when을 주거나 전부 빼세요)"
            )

    # 템플릿 참조가 존재하는 노드/필드를 가리키는지
    produced = {n["id"]: _outputs_of(n) for n in nodes if isinstance(n, dict) and "id" in n}
    for n in nodes:
        if not isinstance(n, dict) or "id" not in n:
            continue
        for key, val in (n.get("inputs") or {}).items():
            for ref_node, ref_field in refs(val):
                if ref_node not in idset:
                    errs.append(f"{n['id']}.inputs.{key}: 없는 노드 {ref_node!r} 참조")
                elif ref_field not in produced.get(ref_node, set()):
                    errs.append(
                        f"{n['id']}.inputs.{key}: {ref_node!r}가 "
                        f"{ref_field!r}를 만들지 않습니다 (가능: {sorted(produced.get(ref_node, []))})"
                    )

    # 도달 불가 노드 — 순환은 막지 않는다 (LangGraph의 핵심 기능이고
    # ReAct 루프가 곧 순환이다). 무한루프는 recursion_limit이 막는다.
    if starts and not errs:
        reach, frontier = {starts[0]}, [starts[0]]
        while frontier:
            cur = frontier.pop()
            for e in edges:
                if e.get("from") == cur and e["to"] not in reach:
                    reach.add(e["to"])
                    frontier.append(e["to"])
        orphans = idset - reach
        if orphans:
            errs.append(f"start에서 도달할 수 없는 노드: {sorted(orphans)}")

    if errs:
        raise SpecError("스펙 오류 %d건:\n  - %s" % (len(errs), "\n  - ".join(errs)))


def _check_agent(n: dict) -> list[str]:
    """agent 노드 검증. 승인 규칙이 여기 있다 — 셸을 무인 실행시키지 않는다."""
    from . import tools as T

    errs: list[str] = []
    nid = n["id"]

    if not (n.get("output") or {}).get("name"):
        errs.append(f"{nid}: agent 노드에 output.name이 필요합니다")

    names = n.get("tools") or []
    if not isinstance(names, list):
        return errs + [f"{nid}: tools는 도구 이름 리스트여야 합니다"]
    unknown = [x for x in names if x not in T.REGISTRY]
    if unknown:
        errs.append(f"{nid}: 알 수 없는 도구 {unknown} (가능: {sorted(T.REGISTRY)})")

    approval = n.get("approval", "auto")
    if approval not in APPROVAL_MODES:
        errs.append(f"{nid}: approval은 {sorted(APPROVAL_MODES)} 중 하나여야 합니다")

    # 거부 메커니즘이 제거된 모델에 셸/쓰기를 쥐여주는 것이므로 승인이 실제 안전장치다.
    risky = sorted(set(names) & T.DANGEROUS)
    if risky and approval == "auto":
        errs.append(
            f"{nid}: {risky} 를 가진 에이전트는 approval: auto 를 쓸 수 없습니다 "
            "(approval: ask 로 바꾸거나 해당 도구를 빼세요)")

    steps = n.get("max_steps", 15)
    if not isinstance(steps, int) or not (1 <= steps <= 100):
        errs.append(f"{nid}: max_steps는 1~100 사이 정수여야 합니다")

    return errs


def _outputs_of(node: dict) -> set[str]:
    """이 노드가 상태에 넣는 필드 이름들."""
    if node.get("type") == "start":
        return set(node.get("outputs") or [])
    name = (node.get("output") or {}).get("name")
    return {name} if name else set()


# ─────────────────────────── self-check ───────────────────────────


def demo():
    good = yaml.safe_load("""
name: T
nodes:
  - {id: start, type: start, outputs: [user_message]}
  - {id: classify, type: llm, output: {name: intent}, inputs: {q: "{{ start.user_message }}"}}
  - {id: reply, type: llm, output: {name: reply}, inputs: {i: "{{ classify.intent }}"}}
  - {id: end, type: end, inputs: {reply: "{{ reply.reply }}"}}
edges:
  - {from: start, to: classify}
  - {from: classify, to: reply, when: "intent == 'order_status'"}
  - {from: classify, to: end, when: default}
  - {from: reply, to: end}
""")
    validate(good)  # 통과해야 함

    def fails(mutate, needle):
        import copy
        s = copy.deepcopy(good)
        mutate(s)
        try:
            validate(s)
        except SpecError as e:
            assert needle in str(e), f"기대 {needle!r}, 실제: {e}"
            return
        raise AssertionError(f"거부됐어야 함: {needle}")

    fails(lambda s: s["edges"].append({"from": "start", "to": "nope"}), "없는 노드")
    fails(lambda s: s["nodes"][1].update(inputs={"q": "{{ start.nothere }}"}), "만들지 않습니다")
    fails(lambda s: s["nodes"][1].update(type="wat"), "알 수 없는 type")
    fails(lambda s: s["edges"].__setitem__(1, {"from": "classify", "to": "reply",
                                               "when": "intent = bad"}), "when 파싱 실패")
    fails(lambda s: s["nodes"].append({"id": "island", "type": "llm",
                                       "output": {"name": "x"}}), "도달할 수 없는")
    fails(lambda s: s["nodes"][1].pop("output"), "output.name이 필요")
    fails(lambda s: s["edges"].append({"from": "classify", "to": "end"}), "섞였습니다")

    # ── agent 노드 ──
    import copy

    def agent_spec(**over):
        node = {"id": "worker", "type": "agent", "output": {"name": "result"},
                "tools": ["grep"], "approval": "auto", "inputs": {"task": "go"}}
        node.update(over)
        return {"name": "A", "nodes": [
            {"id": "start", "type": "start", "outputs": ["q"]},
            node,
            {"id": "end", "type": "end", "inputs": {"r": "{{ worker.result }}"}}],
            "edges": [{"from": "start", "to": "worker"}, {"from": "worker", "to": "end"}]}

    validate(agent_spec())                                   # 읽기전용 + auto = OK
    validate(agent_spec(tools=["run_bash"], approval="ask"))  # 위험 도구 + ask = OK
    validate(agent_spec(tools=[]))                            # 도구 없는 에이전트도 OK

    def agent_fails(needle, **over):
        try:
            validate(agent_spec(**over))
        except SpecError as e:
            assert needle in str(e), f"기대 {needle!r}, 실제: {e}"
            return
        raise AssertionError(f"거부됐어야 함: {needle}")

    # 핵심 안전 규칙 — 셸을 무인 실행시키지 않는다
    agent_fails("approval: auto 를 쓸 수 없습니다", tools=["run_bash"])
    agent_fails("approval: auto 를 쓸 수 없습니다", tools=["grep", "write_file"])
    agent_fails("알 수 없는 도구", tools=["nope"])
    agent_fails("output.name이 필요", output={})
    agent_fails("approval은", approval="whatever")
    agent_fails("max_steps는", max_steps=0)
    agent_fails("max_steps는", max_steps=999)

    # 순환은 허용된다 (ReAct 루프)
    cyc = copy.deepcopy(good)
    cyc["edges"].append({"from": "reply", "to": "classify"})
    validate(cyc)

    # 템플릿 해석
    out = {"start": {"user_message": "hi"}, "classify": {"intent": "order_status"}}
    assert resolve("{{ start.user_message }}", out) == "hi"
    assert resolve("intent is {{ classify.intent }}!", out) == "intent is order_status!"
    assert resolve("{{ nope.x }}", out) is None
    assert resolve(42, out) == 42
    # 통째 참조는 타입 보존
    assert resolve("{{ a.b }}", {"a": {"b": [1, 2]}}) == [1, 2]

    # when
    assert parse_when("intent == 'order_status'")({"intent": "order_status"})
    assert not parse_when("intent == 'order_status'")({"intent": "other"})
    assert parse_when("intent != 'x'")({"intent": "y"})
    assert parse_when("default")({})
    assert parse_when("n == 3")({"n": 3})

    print("OK — spec 검증/템플릿/when 통과")


if __name__ == "__main__":
    demo()
