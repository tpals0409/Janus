"""오케스트레이터-워커 실행 엔진.

에이전트 = 오케스트레이터 1개. 오케스트레이터는 `create_worker` 스킬로 런타임에
워커를 만들고, 워커는 그 실행의 트레이스에만 존재한다(저장·재사용 없음).

LangGraph 없이 실행을 직접 제어하므로 스팬을 명시적으로 열고 닫는다 — 이벤트
귀속 추측(구 trace.py)이 필요 없다. 이벤트는 워커 스레드에서 `send` 콜백으로
바로 나간다.
"""

from __future__ import annotations

import glob
import os
import re
import threading
import time
import uuid
from typing import Callable

from openai import OpenAI

from . import agent as agent_mod
from . import spec as spec_mod
from . import tools as T

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


def _now_ms() -> int:
    return int(time.time() * 1000)


ORCH_ID = "orchestrator"  # 실행 간 고정 — A/B 비교가 node_id로 매칭된다


class Orchestration:
    """WS 연결 하나 = 오케스트레이터 대화 하나.

    send(dict)                     : 스레드 안전 WS 송신 (서버가 제공)
    approver(node_id, tool, args)  : 블로킹 승인 브리지 (서버가 제공)
    """

    def __init__(self, spec: dict, *, send: Callable[[dict], None],
                 approver: Callable[[str, str, dict], bool] | None):
        self.spec = spec
        self.send = send
        self.client = make_client()
        self.model = resolve_local_model(spec["model"])
        self.tools = list(spec.get("tools") or [])
        self.max_steps = spec.get("max_steps", 15)

        self.cancel = threading.Event()
        self.worker_cancels: dict[str, threading.Event] = {}
        self.lock = threading.Lock()
        self.node_events: dict[str, list] = {}
        self.node_usage: dict[str, dict] = {}
        self.spans: list[dict] = []          # [0]=오케스트레이터, 이후 워커 스폰 순
        self.worker_seq = 0
        self.t0 = _now_ms()
        self.first_message: str | None = None
        self.last_text = ""
        self.cancelled_turn = False
        self.turn_failed = False  # 턴이 예외로 죽음 — 저장본이 success로 거짓말하지 않게

        # 승인 매핑: auto → 전부 허용, ask → 브리지, 브리지 없음 → 거부.
        # 위험 도구의 실제 게이트는 tools.dispatch다 — 여기는 정책 선택일 뿐.
        approval = spec.get("approval", "auto")
        if approval == "auto":
            self._approve_for = lambda nid: (lambda name, args: True)
        elif approver is not None:
            self._approve_for = lambda nid: (lambda name, args: approver(nid, name, args))
        else:
            self._approve_for = lambda nid: (lambda name, args: False)

        self.create_worker = self._make_create_worker()
        registry = dict(T.REGISTRY)
        registry[self.create_worker["name"]] = self.create_worker
        self.session = agent_mod.Session(
            agent_mod.build_system_prompt(
                spec.get("system_prompt") or "You are an orchestrator.",
                self.tools + ["create_worker"], registry=registry),
            registry=registry)

    # ── 스팬/이벤트 ──

    def _sink(self, node_id: str, kind: str, data: dict) -> None:
        ev = {"type": "agent_event", "node_id": node_id, "kind": kind,
              "at_ms": _now_ms() - self.t0, **{k: _clip(v) for k, v in data.items()}}
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
                "started_ms": _now_ms() - self.t0, "input": _clip(input),
                "parent_id": parent_id, "label": label}
        with self.lock:
            self.spans.append(span)
        self.send({"type": "span_start", "span": dict(span)})
        return span

    def _close_span(self, span: dict, status: str, output: dict) -> None:
        with self.lock:
            span["status"] = status
            span["duration_ms"] = _now_ms() - self.t0 - span["started_ms"]
            span["output"] = _clip(output)
            span["events"] = list(self.node_events.get(span["node_id"], []))
            span["usage"] = self.node_usage.get(span["node_id"])
        self.send({"type": "span_end", "span": dict(span)})

    # ── create_worker 스킬 ──

    def _make_create_worker(self) -> dict:
        def handler(name: str = "", system_prompt: str = "", task: str = "",
                    tools: list | None = None, max_steps: int = 8) -> dict:
            with self.lock:
                self.worker_seq += 1
                seq = self.worker_seq
            slug = re.sub(r"[^a-z0-9]+", "-", str(name).lower()).strip("-") or "worker"
            wid = f"w{seq}-{slug}"
            # 부분집합 규칙: 워커 도구 ⊆ 오케스트레이터의 spec.tools.
            # extra_tools를 안 넘기므로 워커는 create_worker를 절대 못 받는다 (깊이 1).
            allowed = [t for t in (tools or []) if t in self.tools]
            try:
                steps = max(1, min(int(max_steps), 50))
            except (TypeError, ValueError):
                steps = 8

            span = self._open_span(wid, label=str(name) or wid,
                                   parent_id=self.spans[0]["id"] if self.spans else None,
                                   input={"task": task, "tools": allowed})
            cancel = threading.Event()
            self.worker_cancels[wid] = cancel
            try:
                text, _ = agent_mod.run(
                    client=self.client, model=self.model,
                    system_prompt=str(system_prompt) or "You are a focused worker agent.",
                    task=str(task) or "(no task)",
                    tool_names=allowed,
                    approve=self._approve_for(wid),
                    emit=lambda kind, **d: self._sink(wid, kind, d),
                    max_steps=steps,
                    cancel=cancel,
                )
            except Exception as e:
                self._close_span(span, "error", {"error": f"{type(e).__name__}: {e}"})
                return {"error": f"worker {wid} failed: {type(e).__name__}: {e}"}
            finally:
                self.worker_cancels.pop(wid, None)

            if cancel.is_set():
                self._close_span(span, "error", {"error": "사용자가 워커를 중단함"})
                return {"worker": wid, "result": text,
                        "cancelled": "worker was stopped by the user before finishing"}
            self._close_span(span, "success", {"result": text})
            return {"worker": wid, "result": text}

        return T._t(
            "create_worker", handler,
            lambda v: str(v.get("result") or ""),
            T._obj(["name", "system_prompt", "task"],
                   name={"type": "string", "description": "Short worker name."},
                   system_prompt={"type": "string",
                                  "description": "Role and rules for the worker."},
                   task={"type": "string", "description": "The concrete subtask."},
                   tools={"type": "array", "items": {"type": "string"},
                          "description": "Tool names for the worker — subset of your own."},
                   max_steps={"type": "number", "description": "Step budget (default 8)."}),
            "Spawn a worker agent for a separable subtask and get its result.",
            "Spawn a worker per separable subtask. Request several in one reply to run "
            "them in parallel. tools must be a subset of your own tools.",
        )

    # ── 턴 실행 ──

    def turn(self, text: str) -> None:
        """블로킹 — asyncio.to_thread로 호출된다. ReAct 한 턴."""
        self.cancel.clear()
        self.cancelled_turn = False
        self.turn_failed = False
        if self.first_message is None:
            self.first_message = text
            self._open_span(ORCH_ID, label=self.spec.get("name"), parent_id=None,
                            input={"task": text})
        last, _ = agent_mod.run(
            client=self.client, model=self.model,
            system_prompt=self.spec.get("system_prompt") or "",  # session이 이미 보유
            task=text,
            tool_names=self.tools + ["create_worker"],
            approve=self._approve_for(ORCH_ID),
            emit=lambda kind, **d: self._sink(ORCH_ID, kind, d),
            max_steps=self.max_steps,
            cancel=self.cancel,
            extra_tools=[self.create_worker],
            session=self.session,
        )
        if last:
            self.last_text = last
        if self.cancel.is_set():
            self.cancelled_turn = True
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
                    s["duration_ms"] = _now_ms() - self.t0 - s["started_ms"]
                    s["output"] = _clip({"reply": self.last_text})
                    s["events"] = list(self.node_events.get(ORCH_ID, []))
                    s["usage"] = self.node_usage.get(ORCH_ID)
                elif s["status"] == "running":
                    # 저장 시점에 아직 도는 워커 — 이벤트만이라도 남긴다
                    s["events"] = list(self.node_events.get(s["node_id"], []))
                    s["usage"] = self.node_usage.get(s["node_id"])
        return spans
