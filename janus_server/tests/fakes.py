"""테스트용 가짜 OpenAI 클라이언트 — 실제 MLX 없이 에이전트 루프를 돌린다."""

from __future__ import annotations

import threading
from types import SimpleNamespace


def text_chunk(text: str):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(
        delta=SimpleNamespace(content=text, tool_calls=None))])


def call_chunk(index: int, call_id: str, name: str, arguments: str):
    return SimpleNamespace(usage=None, choices=[SimpleNamespace(
        delta=SimpleNamespace(content=None, tool_calls=[SimpleNamespace(
            index=index, id=call_id,
            function=SimpleNamespace(name=name, arguments=arguments))]))])


def usage_chunk(cached_tokens: int | None = None):
    """cached_tokens=None이면 APC 미지원 서버(details 없음)를 흉내낸다."""
    usage = SimpleNamespace(
        prompt_tokens=1, completion_tokens=1, total_tokens=2)
    if cached_tokens is not None:
        usage.prompt_tokens_details = SimpleNamespace(
            cached_tokens=cached_tokens)
    return SimpleNamespace(usage=usage, choices=[])


class FakeStream:
    def __init__(self, chunks):
        self._it = iter(chunks)

    def __iter__(self):
        return self._it

    def close(self):
        pass


class FakeClient:
    """턴 스크립트를 호출 순서대로 소비한다.

    turn 항목:
      {"text": "..."}                                   — 도구 없이 답
      {"calls": [(name, args_json), ...], "text": ...}  — 도구 호출
      callable() -> turn dict 또는 chunks 이터러블       — 동기화 훅 (Barrier·무한 스트림)
    """

    def __init__(self, turns: list):
        self._turns = list(turns)
        self._lock = threading.Lock()
        self.captured: list[dict] = []  # 호출별 kwargs (messages, tools, ...)
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        with self._lock:
            self.captured.append(kw)
            n = len(self.captured)
            turn = self._turns.pop(0) if self._turns else {"text": "(script exhausted)"}
        if callable(turn):
            turn = turn()
        if not isinstance(turn, dict):  # chunks 이터러블을 그대로 스트림으로
            return FakeStream(turn)
        chunks = []
        for i, (name, args) in enumerate(turn.get("calls") or []):
            chunks.append(call_chunk(i, f"c{n}_{i}", name, args))
        if turn.get("text"):
            chunks.append(text_chunk(turn["text"]))
        chunks.append(usage_chunk())
        return FakeStream(chunks)
