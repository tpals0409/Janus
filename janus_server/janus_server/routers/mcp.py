"""구독형 CLI가 Janus 도구를 부르는 MCP 엔드포인트.

세션마다 경로가 갈린다. CLI는 자기 세션의 URL만 받으므로, 다른 Task의 워크스페이스에
쓰려면 그 세션의 경로를 알아야 하는데 알 방법이 없다. 인증은 전역 미들웨어의
x-janus-token 그대로 — MCP 설정의 headers로 넘긴다.
"""

from __future__ import annotations

from fastapi import APIRouter, Response

from .. import mcp_bridge
from ..shared import _TASK_RUNTIMES, _TASK_RUNTIMES_LOCK

router = APIRouter()


@router.post("/mcp/{session_id}", response_model=None)
def mcp_endpoint(session_id: str, body: dict) -> Response | dict:
    with _TASK_RUNTIMES_LOCK:
        orch = _TASK_RUNTIMES.get(session_id)
    # 세션이 없으면 프로토콜 오류로 답한다. 404를 주면 CLI가 서버 자체를 죽은 것으로
    # 보고 남은 도구 호출까지 포기한다.
    if orch is None or not hasattr(orch, "mcp_tool_names"):
        return {"jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32001, "message": f"실행 중인 세션이 아닙니다: {session_id}"}}

    payload = mcp_bridge.handle(
        body,
        names=orch.mcp_tool_names(),
        invoke=mcp_bridge.invoker(
            approve=orch.mcp_approve, context=orch.workspace_context
        ),
    )
    if payload is None:
        return Response(status_code=202)
    return payload
