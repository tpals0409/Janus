"""터미널 CLI. 표시와 승인만 담당하고 에이전트 로직은 loop.py에 있다."""

import os
import sys

import httpx
from openai import OpenAI

from . import tools
from .loop import BASE_URL, Session, run_turn

DIM, BOLD, YEL, RED, GRN, OFF = (
    "\033[2m", "\033[1m", "\033[33m", "\033[31m", "\033[32m", "\033[0m")

SERVE_CMD = (
    'uv run mlx_vlm.server --model "$(ls -d ~/.cache/huggingface/hub/'
    'models--orcarouter--Qwen3.8-27B-Uncensored-MLX/snapshots/*/4-bit)" --port 8080'
)


def server_up() -> bool:
    try:
        return httpx.get(f"{BASE_URL}/models", timeout=3).status_code == 200
    except Exception:
        return False


def summarize(name: str, args: dict) -> str:
    """승인 프롬프트에 보여줄 요약. bash는 전문, edit은 치환 내용."""
    if name == "run_bash":
        return args.get("command", "")
    if name == "edit_file":
        old, new = args.get("old_string", ""), args.get("new_string", "")
        return f"{args.get('path')}\n{RED}- {old[:200]}{OFF}\n{GRN}+ {new[:200]}{OFF}"
    if name == "write_file":
        content = args.get("content", "")
        return f"{args.get('path')} ({len(content)}자)"
    return str(args)


def approve(name: str, args: dict) -> bool:
    print(f"\n{YEL}⚠ {name}{OFF}\n{summarize(name, args)}")
    try:
        return input(f"{BOLD}실행할까요? [y/N] {OFF}").strip().lower() in ("y", "yes")
    except (EOFError, KeyboardInterrupt):
        print()
        return False


def make_emit():
    state = {"streaming": False}

    def emit(kind, **d):
        if kind == "text_delta":
            state["streaming"] = True
            print(d["text"], end="", flush=True)
            return
        if state["streaming"]:
            print()
            state["streaming"] = False
        if kind == "tool_start":
            print(f"{DIM}→ {d['name']}({summarize(d['name'], d['args'])[:120]}){OFF}")
        elif kind == "tool_result":
            v = d["value"]
            mark = f"{RED}✗ {v['error']}{OFF}" if "error" in v else f"{GRN}✓{OFF}"
            print(f"{DIM}  {mark}{OFF}")
        elif kind == "done" and d["reason"] != "no_tool_calls":
            print(f"{YEL}[중단: {d['reason']}]{OFF}")

    return emit


def main():
    if not server_up():
        print(f"{RED}MLX 서버가 떠 있지 않습니다.{OFF}\n\n다음을 별도 터미널에서 실행하세요:\n")
        print(f"  cd {os.path.dirname(os.path.dirname(os.path.abspath(__file__)))} && {SERVE_CMD}\n")
        sys.exit(1)

    session = Session()
    client = OpenAI(base_url=BASE_URL, api_key="none")
    emit = make_emit()

    print(f"{BOLD}Qwen3.8-27B 코딩 에이전트{OFF}  {DIM}cwd={os.getcwd()}{OFF}")
    print(f"{DIM}도구: {', '.join(t['name'] for t in tools.TOOLS)}  |  /exit /reset /dump{OFF}\n")

    while True:
        try:
            line = input(f"{BOLD}> {OFF}").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line == "/exit":
            break
        if line == "/reset":
            session = Session()
            print(f"{DIM}대화 초기화됨{OFF}")
            continue
        if line == "/dump":
            import json
            print(json.dumps(session.events, ensure_ascii=False, indent=2)[:4000])
            continue

        try:
            run_turn(session, line, approve, emit, client=client)
        except KeyboardInterrupt:
            print(f"\n{YEL}[중단됨]{OFF}")
        except Exception as e:
            print(f"{RED}오류: {type(e).__name__}: {e}{OFF}")
        print()


if __name__ == "__main__":
    main()
