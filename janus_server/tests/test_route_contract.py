"""렌더러-서버 라우트 계약 테스트.

렌더러의 모든 HTTP/WS 호출 경로가 FastAPI에 실제 등록된 라우트와 매칭되는지
검증한다. 라우트 리네임을 렌더러가 안 따라갔거나 경로 오타가 나면 여기가
먼저 빨개진다. 서버 쪽 원천은 문서가 아니라 `server.app.routes` 그 자체다.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from fastapi.routing import APIRoute, APIWebSocketRoute

from janus_server import server

RENDERER = Path(__file__).resolve().parents[2] / "janus" / "src" / "renderer" / "src"

# 호출 추출 패턴 — api.ts의 헬퍼(apiJson/janusApi/websocketUrl) 사용처를 잡는다.
CALL_PATTERNS = (
    (re.compile(r"`\$\{(?:BASE|JANUS_BASE)\}(/[^`]*)`"), False),
    (re.compile(r"janusApi(?:<[^>]*>)?\(\s*[\"'](/[^\"']+)[\"']"), False),
    (re.compile(r"janusApi(?:<[^>]*>)?\(\s*`(/[^`]+)`"), False),
    (re.compile(r"websocketUrl\(\s*[\"'](/[^\"']+)[\"']"), True),
    (re.compile(r"websocketUrl\(\s*`(/[^`]+)`"), True),
)
METHOD_RE = re.compile(r"\b(GET|POST|PUT|PATCH|DELETE)\b")
# `${cond ? 'a' : 'b'}` — 문자열 리터럴 삼항은 양쪽 분기로 전개한다.
TERNARY_RE = re.compile(r"\$\{[^{}]*\?\s*'([^']+)'\s*:\s*'([^']+)'\s*\}")


def _normalize(path: str) -> str:
    """경로 파라미터({task_id}, ${task.id})를 자리표시자로 통일한다.

    ${...}를 먼저 지워야 한다 — {...}부터 지우면 ${id}의 속만 갉혀 `${}`가 남는다.
    """
    path = path.split("?")[0]
    path = re.sub(r"\$\{[^}]+\}", "{}", path)
    path = re.sub(r"\{[^}]+\}", "{}", path)
    return path.rstrip("/") or "/"


def _iter_routes(routes):
    """app.routes를 재귀로 걷는다 — include_router가 _IncludedRouter로 지연
    장착되는 FastAPI 버전에선 최상위 순회만으로 라우트가 다 보이지 않는다."""
    for route in routes:
        if isinstance(route, (APIRoute, APIWebSocketRoute)):
            yield route
            continue
        nested = getattr(route, "original_router", None) or route
        sub = getattr(nested, "routes", None)
        if sub:
            yield from _iter_routes(sub)


def server_route_index() -> set[tuple[str, str]]:
    index: set[tuple[str, str]] = set()
    for route in _iter_routes(server.app.routes):
        if isinstance(route, APIWebSocketRoute):
            index.add(("WS", _normalize(route.path)))
        else:
            for method in route.methods - {"HEAD", "OPTIONS"}:
                index.add((method, _normalize(route.path)))
    return index


def renderer_calls() -> list[tuple[str, str, set[str], set[str]]]:
    """(파일:행, 원본 경로, 후보 메서드들, 후보 정규화 경로들) 목록."""
    calls = []
    for source in sorted(RENDERER.rglob("*.ts")) + sorted(RENDERER.rglob("*.tsx")):
        if ".test." in source.name:
            continue
        lines = source.read_text(encoding="utf-8").splitlines()
        for lineno, line in enumerate(lines, 1):
            for pattern, is_ws in CALL_PATTERNS:
                for match in pattern.finditer(line):
                    raw = match.group(1)
                    # 삼항 분기를 전개해 실제 도달 가능한 경로 후보를 만든다
                    candidates = {raw}
                    while any(TERNARY_RE.search(c) for c in candidates):
                        candidates = {
                            TERNARY_RE.sub(branch, c, count=1)
                            for c in candidates
                            for branch in (r"\1", r"\2")
                        }
                    paths = {_normalize(c) for c in candidates}
                    if is_ws:
                        methods = {"WS"}
                    else:
                        # 같은 호출식(다음 3줄)의 method: 표현에서 후보를 수집.
                        # 삼항 메서드(force ? 'DELETE' : 'POST')는 둘 다 후보다.
                        window = "\n".join(lines[lineno - 1:lineno + 3])
                        method_part = window.split("method:", 1)
                        found = (
                            set(METHOD_RE.findall(method_part[1][:80]))
                            if len(method_part) > 1 else set()
                        )
                        methods = found or {"GET"}
                    where = f"{source.relative_to(RENDERER)}:{lineno}"
                    calls.append((where, raw, methods, paths))
    return calls


@pytest.mark.skipif(not RENDERER.is_dir(), reason="renderer 소스가 없는 배포 형태")
def test_every_renderer_call_hits_a_registered_route():
    index = server_route_index()
    calls = renderer_calls()
    # 추출이 조용히 0건이 되면 계약 검증 자체가 사라진다 — api.ts 헬퍼를
    # 리네임했다면 CALL_PATTERNS를 같이 갱신하라는 신호다.
    assert len(calls) >= 50, (
        f"렌더러 호출이 {len(calls)}건만 추출됨 — CALL_PATTERNS가 현재 API "
        "헬퍼 사용 방식을 못 따라가고 있다"
    )
    unmatched = [
        f"{where}: {sorted(methods)} {raw}"
        for where, raw, methods, paths in calls
        if not any((m, p) in index for m in methods for p in paths)
    ]
    assert not unmatched, (
        "서버에 등록되지 않은 라우트를 호출하는 렌더러 코드:\n"
        + "\n".join(sorted(unmatched))
    )
