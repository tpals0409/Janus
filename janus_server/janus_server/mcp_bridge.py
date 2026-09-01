"""구독형 CLI에 Janus 도구를 MCP로 내준다 — 건별 승인이 붙는 유일한 지점.

플래그 범위 제한(`--restricted`, `--tools`)은 CLI가 **무엇을** 만질 수 있는지만
정한다. "이 파일을 고쳐도 됩니까?"는 묻지 못한다. headless CLI에 승인 UI가 없어서가
아니라, 승인을 물을 상대가 Janus인데 CLI가 Janus에게 물을 통로가 없어서였다.

MCP가 그 통로다. 내장 Write/Edit/Bash를 아예 빼고 같은 일을 하는 Janus 도구를
MCP로 주면, CLI가 쓰기를 하려면 반드시 이 서버를 거쳐야 한다. 그러면 로컬 경로와
똑같이 tools.dispatch가 승인 콜백을 강제한다.

실측으로 확인한 전제(claude 2.x):
  * `--restricted --strict-mcp-config --mcp-config`로 이 서버에만 붙는다.
  * `--tools`에서 Write/Edit/Bash를 빼면 내장 도구가 세션에 없다 — 우회로가 없다.
  * MCP 도구 호출은 사람이 답할 때까지 기다린다(75초 지연으로 확인). CLI 쪽
    기본 타임아웃은 MCP_TOOL_TIMEOUT으로 올려 APPROVAL_TIMEOUT보다 길게 잡는다.

프로토콜은 손으로 구현한다. 필요한 메서드가 셋뿐이라 SDK 의존성을 새로 들이지
않는다. 전송은 streamable HTTP의 JSON 응답 형태 — SSE는 쓰지 않는다.
"""

from __future__ import annotations

from typing import Any

from . import tools as T

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME = "janus"
#: MCP로 내주는 도구 = 승인이 필요한 도구. 읽기 도구는 CLI 내장이 더 빠르고
#: --restricted가 이미 작업 디렉터리에 가둔다 — 굳이 왕복시키지 않는다.
BRIDGED = T.DANGEROUS


def bridged_tools(janus_tools: object) -> list[str]:
    """프로필이 준 도구 중 MCP로 넘길 것들. 선언이 없으면 아무것도 넘기지 않는다."""
    names = [str(item) for item in janus_tools] if isinstance(janus_tools, list) else []
    return [name for name in names if name in BRIDGED and name in T.REGISTRY]


def descriptor(name: str) -> dict:
    """MCP tools/list 항목. 스키마와 설명은 Janus 레지스트리가 단일 출처다."""
    tool = T.REGISTRY[name]
    description = str(tool["description"])
    if guidance := str(tool.get("guidance") or ""):
        description = f"{description}\n\n{guidance}"
    return {"name": name, "description": description, "inputSchema": tool["schema"]}


def _ok(rpc_id: Any, payload: dict) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "result": payload}


def _fail(rpc_id: Any, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": rpc_id, "error": {"code": code, "message": message}}


def handle(body: dict, *, names: list[str], invoke) -> dict | None:
    """JSON-RPC 한 건을 처리한다. 알림(id 없음)이면 None — 호출자가 202로 답한다.

    `invoke(name, args) -> str`은 도구를 실제로 돌리고 모델에게 보일 텍스트를 준다.
    이 모듈은 전송과 프로토콜만 알고, 승인·워크스페이스는 호출자가 묶는다.
    """
    method = str(body.get("method") or "")
    rpc_id = body.get("id")
    if rpc_id is None:
        return None  # notifications/initialized 등 — 응답 본문이 없어야 한다

    if method == "initialize":
        requested = str((body.get("params") or {}).get("protocolVersion") or "")
        return _ok(rpc_id, {
            # 클라이언트가 부른 판을 그대로 되돌려준다. 우리가 쓰는 세 메서드는
            # 판 사이에 바뀌지 않아서, 판을 깎으면 붙지 못할 이유만 늘어난다.
            "protocolVersion": requested or PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": SERVER_NAME, "version": "1"},
        })
    if method == "tools/list":
        return _ok(rpc_id, {"tools": [descriptor(name) for name in names]})
    if method == "tools/call":
        params = body.get("params") or {}
        name = str(params.get("name") or "")
        if name not in names:
            # 프로필이 주지 않은 도구다. 프로토콜 오류가 아니라 도구 오류로 돌려줘야
            # 모델이 턴을 죽이지 않고 다른 방법을 찾는다.
            return _ok(rpc_id, {
                "content": [{"type": "text", "text": f"ERROR: 이 세션에 없는 도구: {name}"}],
                "isError": True,
            })
        arguments = params.get("arguments")
        text = invoke(name, arguments if isinstance(arguments, dict) else {})
        return _ok(rpc_id, {
            "content": [{"type": "text", "text": text}],
            "isError": text.startswith("ERROR: "),
        })
    return _fail(rpc_id, -32601, f"지원하지 않는 메서드: {method}")


def invoker(*, approve, context, emit=None):
    """dispatch를 MCP 응답용 텍스트로 감싼다. 승인 거부도 텍스트 오류로 나간다.

    `emit(kind, **data)`를 주면 로컬 경로와 같은 tool_start/tool_result 이벤트를
    낸다. 없으면 CLI 세션이 실행한 쓰기·셸이 승인만 받고 이벤트 로그 어디에도
    남지 않아, 무엇이 워크스페이스를 바꿨는지 사후에 알 수 없다.
    """
    def invoke(name: str, args: dict) -> str:
        if emit is not None:
            emit("tool_start", name=name, args=args)
        value = T.dispatch(name, args, approve=approve, context=context)
        rendered = T.render(name, value)
        if emit is not None:
            emit(
                "tool_result", name=name,
                value={"content": rendered[:4000]},
                status=("error" if isinstance(value, dict) and value.get("error")
                        else "success"),
            )
        return rendered
    return invoke


def demo() -> None:
    """프레임워크 없는 자기검증 — `python -m janus_server.mcp_bridge`."""
    calls: list[tuple[str, dict]] = []

    def invoke(name: str, args: dict) -> str:
        calls.append((name, args))
        return "done"

    names = ["write_file", "run_bash"]
    assert handle({"method": "notifications/initialized"}, names=names, invoke=invoke) is None

    init = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05"}},
        names=names, invoke=invoke,
    )
    assert init["result"]["protocolVersion"] == "2024-11-05", init
    assert init["result"]["capabilities"] == {"tools": {}}, init

    listed = handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
                    names=names, invoke=invoke)
    assert [t["name"] for t in listed["result"]["tools"]] == names, listed
    assert listed["result"]["tools"][0]["inputSchema"] is T.REGISTRY["write_file"]["schema"]

    called = handle(
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
         "params": {"name": "write_file", "arguments": {"path": "a", "content": "b"}}},
        names=names, invoke=invoke,
    )
    assert calls == [("write_file", {"path": "a", "content": "b"})], calls
    assert called["result"]["content"] == [{"type": "text", "text": "done"}], called
    assert called["result"]["isError"] is False, called

    # 프로필이 주지 않은 도구는 도구 오류로 — 프로토콜 오류로 돌리면 턴이 죽는다.
    withheld = handle(
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
         "params": {"name": "edit_file", "arguments": {}}},
        names=names, invoke=invoke,
    )
    assert withheld["result"]["isError"] is True, withheld
    assert len(calls) == 1, "없는 도구가 실행됐다"

    assert handle({"jsonrpc": "2.0", "id": 5, "method": "resources/list"},
                  names=names, invoke=invoke)["error"]["code"] == -32601

    # 승인이 없으면 dispatch가 막고, 그 거부가 텍스트로 모델에게 간다.
    denied = invoker(approve=lambda *_: False, context=None)("run_bash", {"command": "ls"})
    assert denied.startswith("ERROR: "), denied

    assert bridged_tools(["read_file", "write_file", "glob", "run_bash"]) == [
        "write_file", "run_bash"]
    assert bridged_tools(None) == []
    assert bridged_tools(["read_file"]) == []
    print("mcp_bridge self-check ok")


if __name__ == "__main__":
    demo()
