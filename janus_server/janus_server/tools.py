"""통합 도구 레지스트리.

`tool` 노드와 `agent` 노드가 같은 레지스트리를 쓴다. agent 노드는 이 중 일부만
골라 갖는다.

handler는 **구조화된 dict**를 반환하고 render()가 모델용 텍스트로 바꾼다. 이벤트
로그에 원본 dict가 남아야 UI가 diff 같은 걸 그릴 수 있다 — render 결과만 자르고
원본은 자르지 않는다.

파일 도구는 호출자가 넘긴 WorkspaceContext 안에 갇힌다(_resolve가 jail 검사).
run_bash는 cwd를 같은 컨텍스트에서 받는다. cd로 어디든 갈 수 있으므로 승인
게이트가 bash의 추가 방어선이다.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
import urllib.request
from collections.abc import Callable
from pathlib import Path

from .workspace import WorkspaceContext

MAX_RENDER_CHARS = 4000
BASH_TIMEOUT = 120

def _resolve(path: str, context: WorkspaceContext) -> Path:
    """상대경로는 실행 컨텍스트의 root 기준으로 해석하고 밖으로 나가면 거부한다.

    ValueError는 dispatch()가 {"error": ...}로 바꿔 모델에게 돌려준다.
    """
    ws = context.root.resolve()
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = ws / p
    rp = p.resolve()
    if rp.is_relative_to(ws):
        return rp

    # macOS can expose the workspace using decomposed Unicode (NFD) while a
    # model copies the visually identical path back as NFC.  Treat those
    # spellings as aliases, then resolve the reconstructed path again so the
    # symlink/``..`` jail check remains authoritative.
    normalized_ws = Path(unicodedata.normalize("NFC", str(ws)))
    normalized_rp = Path(unicodedata.normalize("NFC", str(rp)))
    try:
        relative = normalized_rp.relative_to(normalized_ws)
    except ValueError:
        pass
    else:
        candidate = (ws / relative).resolve()
        if candidate.is_relative_to(ws):
            return candidate
    raise ValueError(f"워크스페이스({ws}) 밖 경로: {path}")


def _clip(text: str, limit: int = MAX_RENDER_CHARS) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    omitted = len(text) - limit
    return f"{text[:half]}\n\n... [{omitted}자 생략] ...\n\n{text[-half:]}"


# ─────────────────────────── handlers ───────────────────────────
# handler는 예외를 던지지 않고 {"error": ...}를 반환한다. 모델이 스스로 복구하게 둔다.


def _read_file(
    path: str, offset: int | str = 0, limit: int | str | None = None, *,
    _context: WorkspaceContext,
):
    p = _resolve(path, _context)
    if not p.is_file():
        return {"error": f"파일 없음: {path}"}
    try:
        start = max(0, int(offset))
        count = 400 if limit is None else max(1, min(2_000, int(limit)))
    except (TypeError, ValueError):
        return {"error": "offset/limit은 줄 번호 정수여야 합니다"}
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    selected = lines[start:start + count]
    return {
        "path": str(p), "content": "".join(selected), "offset": start,
        "limit": count, "total_lines": len(lines),
        "has_more": start + len(selected) < len(lines),
    }


def _glob(pattern: str, *, _context: WorkspaceContext):
    ws = _context.root
    # 패턴에 ..가 있어도 결과를 jail로 거른다
    matches = sorted(str(p) for p in ws.glob(pattern)
                     if p.is_file() and p.resolve().is_relative_to(ws))
    return {"pattern": pattern, "matches": matches}


def _grep(pattern: str, path: str = ".", *, _context: WorkspaceContext):
    root = _resolve(path, _context)
    if shutil.which("rg"):
        r = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--color=never", pattern, str(root)],
            capture_output=True, text=True, timeout=60, cwd=_context.root,
        )
        # rg는 매치 없음에 exit 1을 쓴다. 그건 에러가 아니다.
        if r.returncode not in (0, 1):
            return {"error": r.stderr.strip() or f"rg 실패 (exit {r.returncode})"}
        lines = r.stdout.splitlines()
    else:
        rx = re.compile(pattern)
        lines = []
        for f in root.rglob("*"):
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        lines.append(f"{f}:{i}:{line}")
            except OSError:
                continue
    return {"pattern": pattern, "path": path, "matches": lines, "count": len(lines)}


def _write_file(path: str, content: str, *, _context: WorkspaceContext):
    p = _resolve(path, _context)
    existed = p.is_file()
    old = p.read_text(encoding="utf-8", errors="replace") if existed else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p), "created": not existed, "old": old, "new": content}


def _edit_file(
    path: str, old_string: str, new_string: str, *, _context: WorkspaceContext
):
    p = _resolve(path, _context)
    if not p.is_file():
        return {"error": f"파일 없음: {path}"}
    text = p.read_text(encoding="utf-8", errors="replace")
    n = text.count(old_string)
    if n == 0:
        return {"error": f"old_string이 {path}에 없음"}
    if n > 1:
        return {"error": f"old_string이 {n}번 나타남. 더 긴 문맥을 포함해 유일하게 만들 것"}
    new_text = text.replace(old_string, new_string)
    p.write_text(new_text, encoding="utf-8")
    return {"path": str(p), "old": text, "new": new_text,
            "old_string": old_string, "new_string": new_string}


def _run_bash(command: str, *, _context: WorkspaceContext):
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=BASH_TIMEOUT, cwd=_context.root)
    except subprocess.TimeoutExpired:
        return {"command": command, "error": f"{BASH_TIMEOUT}초 타임아웃"}
    return {"command": command, "exit_code": r.returncode,
            "stdout": r.stdout, "stderr": r.stderr}


def _http_get(url: str, timeout: float = 10.0, **_):
    if not str(url).startswith(("http://", "https://")):
        return {"error": f"http/https URL만 됩니다: {url}"}
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return {"url": url, "status": r.status,
                    "body": r.read(200_000).decode("utf-8", errors="replace")}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


# 데모 그래프가 네트워크 없이도 돌게 하려는 고정 데이터.
# 이름에 mock을 박아 진짜 연동으로 오해하지 않게 한다.
_MOCK_ORDERS = {
    "12345": {"status": "confirmed", "eta": "2026-05-24", "carrier": "CJ대한통운"},
    "99999": {"status": "delayed", "eta": "2026-06-02", "carrier": "우체국"},
}


def _mock_order_lookup(order_id: str, **_):
    o = _MOCK_ORDERS.get(str(order_id).lstrip("#"))
    if o is None:
        return {"error": f"주문 {order_id}을(를) 찾을 수 없습니다."}
    return {"order_id": order_id, **o}


_MOCK_DOCS = {
    "mlx": "MLX는 애플 실리콘용 배열 프레임워크다. 통합 메모리를 써서 CPU와 GPU가 "
           "같은 버퍼를 공유하므로 복사 비용이 없다.",
    "langgraph": "LangGraph는 상태를 가진 그래프로 LLM 워크플로를 짜는 라이브러리다. "
                 "노드는 상태를 읽고 갱신하며, 엣지는 실행 순서와 분기를 정한다.",
    "quantization": "양자화는 모델 가중치를 낮은 비트로 줄여 메모리와 대역폭을 아끼는 "
                    "기법이다. 4bit면 fp16 대비 약 4분의 1 크기가 된다.",
}


def _search_docs(query: str, **_):
    q = str(query).lower()
    hits = [{"topic": k, "text": v} for k, v in _MOCK_DOCS.items() if k in q or q in k]
    if not hits:  # 아주 단순한 부분일치 폴백
        hits = [{"topic": k, "text": v} for k, v in _MOCK_DOCS.items()
                if any(w in v.lower() for w in q.split())]
    return {"query": query, "hits": hits, "count": len(hits)}


def _echo(text: str, **_):
    return {"text": text}


# ─────────────────────────── renderers ───────────────────────────


def _r_read(v): return v["content"]
def _r_glob(v): return "\n".join(v["matches"]) or "(매치 없음)"
def _r_grep(v): return "\n".join(v["matches"]) or "(매치 없음)"
def _r_echo(v): return v["text"]
def _r_http(v): return f"[status {v['status']}]\n{v['body']}"
def _r_json(v): return json.dumps(v, ensure_ascii=False, indent=2)


def _r_write(v):
    verb = "생성" if v["created"] else "덮어씀"
    return f"<path>{v['path']}</path>\n<result>{verb}, {len(v['new'])}자</result>"


def _r_edit(v):
    return f"<path>{v['path']}</path>\n<result>치환 완료</result>"


def _r_bash(v):
    out = (v["stdout"] + v["stderr"]).strip()
    return f"{out}\n[exit code: {v['exit_code']}]" if out else f"[exit code: {v['exit_code']}]"


def _r_docs(v):
    if not v["hits"]:
        return "(매치 없음)"
    return "\n\n".join(f"## {h['topic']}\n{h['text']}" for h in v["hits"])


# ─────────────────────────── registry ───────────────────────────
# guidance는 도구 옆에 둔다 — agent 노드의 시스템 프롬프트에 이게 합쳐진다.

def _t(
    name, handler, render, schema, description, guidance="", needs_approval=False,
    requires_workspace=False, resource_class="io_tool", render_chars=MAX_RENDER_CHARS,
    terminal=False,
):
    return {"name": name, "handler": handler, "render": render, "schema": schema,
            "description": description, "guidance": guidance,
            "needs_approval": needs_approval,
            "requires_workspace": requires_workspace,
            "resource_class": resource_class,
            "render_chars": render_chars, "terminal": terminal}


def _obj(required, **props):
    return {"type": "object", "required": required, "properties": props}


_S = {"type": "string"}
_N = {"type": "number"}

TOOLS = [
    # ── 읽기 전용 ──
    _t("read_file", _read_file, _r_read,
       _obj(["path"], path={**_S, "description": "Path to the file."},
            offset={"type": "integer",
                    "description": "Zero-based starting line. Default 0."},
            limit={"type": "integer",
                   "description": "Number of lines, 1-2000. Default 400."}),
       "Read the full contents of a file.",
       "Read a file before editing it. For long files, page with offset and limit.",
       requires_workspace=True),
    _t("glob", _glob, _r_glob,
       _obj(["pattern"], pattern={**_S, "description": "Glob pattern, e.g. '**/*.py'."}),
       "Find files by glob pattern, relative to the working directory.",
       "Use glob to locate files by name.", requires_workspace=True),
    _t("grep", _grep, _r_grep,
       _obj(["pattern"], pattern={**_S, "description": "Regular expression."},
            path={**_S, "description": "Directory to search. Default '.'."}),
       "Search file contents by regex. Returns 'file:line:text' matches.",
       "Use grep to find where something is defined or used.", requires_workspace=True),
    _t("http_get", _http_get, _r_http,
       _obj(["url"], url={**_S, "description": "http/https URL."},
            timeout={**_N, "description": "Seconds. Default 10."}),
       "Fetch a URL and return the response body as text.",
       "Use http_get to read a public web page or API.", needs_approval=True),
    _t("search_docs", _search_docs, _r_docs,
       _obj(["query"], query={**_S, "description": "Search terms."}),
       "Search a small built-in document set. Demo data, not a real index.",
       "Use search_docs to look up a topic before answering.", resource_class="cpu_tool"),
    _t("mock_order_lookup", _mock_order_lookup, _r_json,
       _obj(["order_id"], order_id={**_S, "description": "Order id."}),
       "Look up an order in a fixed demo dataset. Not a real integration.",
       "Use mock_order_lookup when the user asks about an order.", resource_class="cpu_tool"),
    _t("echo", _echo, _r_echo,
       _obj(["text"], text={**_S, "description": "Text to return."}),
       "Return the input unchanged. Useful for testing a graph.",
       "Use echo only for testing.", resource_class="cpu_tool"),

    # ── 승인 필요 ──
    _t("write_file", _write_file, _r_write,
       _obj(["path", "content"], path={**_S, "description": "Path to the file."},
            content={**_S, "description": "Full file content."}),
       "Write content to a file, creating or overwriting it.",
       "Use write_file only for NEW files or full rewrites. "
       "To change part of an existing file use edit_file — it is far cheaper.",
       needs_approval=True, requires_workspace=True),
    _t("edit_file", _edit_file, _r_edit,
       _obj(["path", "old_string", "new_string"],
            path={**_S, "description": "Path to the file."},
            old_string={**_S, "description": "Exact text to replace. Must appear once."},
            new_string={**_S, "description": "Replacement text."}),
       "Replace an exact string in a file. old_string must be unique.",
       "Prefer edit_file over write_file for existing files. "
       "If old_string is not unique, include more surrounding lines.",
       needs_approval=True, requires_workspace=True),
    _t("run_bash", _run_bash, _r_bash,
       _obj(["command"], command={**_S, "description": "Shell command to run."}),
       "Run a shell command in the working directory.",
       "Check the [exit code: N] on every result. "
       "A non-zero code means it failed — investigate before continuing.",
       needs_approval=True, requires_workspace=True, resource_class="cpu_tool"),
]

REGISTRY: dict[str, dict] = {t["name"]: t for t in TOOLS}

#: 승인 없이 agent 노드에 줘도 되는 도구들
READ_ONLY = {t["name"] for t in TOOLS if not t["needs_approval"]}
#: 승인이 필요한 도구들 — agent 노드가 이걸 가지면 approval: auto 를 막는다
DANGEROUS = {t["name"] for t in TOOLS if t["needs_approval"]}


def dispatch(
    name: str,
    args: dict,
    *,
    approve: Callable[[str, dict], bool] | None = None,
    registry: dict | None = None,
    context: WorkspaceContext | None = None,
) -> dict:
    """도구 실행. 위험 도구는 dispatch 계층에서 승인을 강제한다.

    호출자가 승인 UI를 빼먹어도 쉘/쓰기가 실행되지 않도록 승인 콜백이
    없으면 기본 거부한다. 반환값은 구조화된 dict로 이벤트 로그에 그대로 저장된다.
    registry로 실행별 도구(create_worker 등)를 주입한다 — 전역 REGISTRY는 불변.
    파일/셰 도구는 명시적 WorkspaceContext가 없으면 절대 실행하지 않는다.
    """
    reg = registry or REGISTRY
    tool = reg.get(name)
    if tool is None:
        return {"error": f"알 수 없는 도구: {name} (가능: {sorted(reg)})"}
    if tool["needs_approval"]:
        try:
            approved = approve is not None and bool(approve(name, args))
        except Exception as e:
            return {"error": f"승인 처리 실패: {type(e).__name__}: {e}"}
        if not approved:
            return {"error": "사용자가 이 도구 실행을 승인하지 않음"}
    if tool.get("requires_workspace") and context is None:
        return {"error": "WorkspaceContext가 필요한 도구입니다"}
    try:
        if tool.get("requires_workspace"):
            return tool["handler"](_context=context, **args)
        return tool["handler"](**args)
    except TypeError as e:
        return {"error": f"인자 오류: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def resource_class_for(name: str, registry: dict | None = None) -> str:
    """Return the scheduler resource class declared by a tool."""
    reg = registry or REGISTRY
    tool = reg.get(name)
    return str((tool or {}).get("resource_class") or "io_tool")


def render(name: str, value: dict, registry: dict | None = None) -> str:
    """모델에게 보낼 텍스트. 여기서만 자른다 — 원본 dict는 온전히 남는다."""
    reg = registry or REGISTRY
    # 키 존재가 아니라 값으로 판정한다: worker view처럼 정상 결과에 error=None을
    # 싣는 dict가 "ERROR: None"으로 렌더링되면 모델이 결과 본문을 아예 못 본다.
    if isinstance(value, dict) and value.get("error"):
        return f"ERROR: {value['error']}"
    return _clip(reg[name]["render"](value), int(reg[name].get("render_chars", MAX_RENDER_CHARS)))


def schemas_for(names: list[str], registry: dict | None = None) -> list[dict]:
    """OpenAI tools 파라미터 — 에이전트가 가진 도구만."""
    reg = registry or REGISTRY
    return [{"type": "function", "function": {
        "name": n, "description": reg[n]["description"],
        "parameters": reg[n]["schema"]}}
        for n in names if n in reg]


def guidance_for(names: list[str], registry: dict | None = None) -> str:
    reg = registry or REGISTRY
    return "\n".join(f"- {n}: {reg[n]['guidance']}"
                     for n in names if n in reg and reg[n]["guidance"])


def listing() -> list[dict]:
    """UI 드롭다운용 — 함수 객체는 빼고 보낸다."""
    return [{"name": t["name"], "description": t["description"],
             "needs_approval": t["needs_approval"],
             "requires_workspace": t["requires_workspace"],
             "resource_class": t["resource_class"],
             "params": list(t["schema"]["properties"])}
            for t in sorted(TOOLS, key=lambda t: (t["needs_approval"], t["name"]))]


# ─────────────────────────── self-check ───────────────────────────


def demo():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        context = WorkspaceContext(
            root=Path(d), task_id="task_demo", workspace_id="workspace_demo"
        ).for_dispatch("dispatch_demo")
        def approved(*_args: object) -> bool:
            return True

        def invoke(name, args, **kwargs):
            return dispatch(name, args, context=context, **kwargs)
        # dispatch가 위험 도구를 기본 거부한다 — 모든 노드의 마지막 방어선.
        assert "승인하지 않음" in invoke(
            "run_bash", {"command": "echo should-not-run"})["error"]
        assert "승인하지 않음" in invoke(
            "write_file", {"path": "denied", "content": "x"},
            approve=lambda *_: False)["error"]
        assert not (Path(d) / "denied").exists()

        v = invoke("write_file", {"path": "hi.py", "content": "x = 1\ny = 2\n"},
                   approve=approved)
        assert v["created"] and v["old"] is None, v
        assert invoke("read_file", {"path": "hi.py"})["content"] == "x = 1\ny = 2\n"
        v = invoke(
            "edit_file", {"path": "hi.py", "old_string": "x = 1", "new_string": "x = 99"},
            approve=approved,
        )
        assert v["old"] == "x = 1\ny = 2\n" and v["new"] == "x = 99\ny = 2\n", v
        assert "error" in invoke(
            "edit_file", {"path": "hi.py", "old_string": "없음", "new_string": "z"},
            approve=approved,
        )
        invoke("write_file", {"path": "dup.py", "content": "a\na\n"}, approve=approved)
        v = invoke(
            "edit_file", {"path": "dup.py", "old_string": "a", "new_string": "b"},
            approve=approved,
        )
        assert "2번" in v["error"], v
        assert len(invoke("glob", {"pattern": "*.py"})["matches"]) == 2
        assert invoke("grep", {"pattern": "x = 99"})["count"] == 1
        assert invoke("grep", {"pattern": "절대없는패턴xyzzy"})["count"] == 0
        v = invoke("run_bash", {"command": "echo hello"}, approve=approved)
        assert v["exit_code"] == 0 and render("run_bash", v) == "hello\n[exit code: 0]"
        assert invoke("run_bash", {"command": "exit 3"}, approve=approved)["exit_code"] == 3
        assert render("read_file", invoke("read_file", {"path": "없음"})).startswith("ERROR:")
        invoke("write_file", {"path": "big.txt", "content": "z" * 20000}, approve=approved)
        assert len(render("read_file", invoke("read_file", {"path": "big.txt"}))) \
            < MAX_RENDER_CHARS + 100
        for esc in ("../escape.txt", "/etc/hosts", "a/../../escape.txt", "~/escape.txt"):
            v = invoke("read_file", {"path": esc})
            assert "error" in v and "밖 경로" in v["error"], (esc, v)
            v = invoke("write_file", {"path": esc, "content": "x"}, approve=approved)
            assert "error" in v and "밖 경로" in v["error"], (esc, v)
        assert "error" in invoke("grep", {"pattern": "x", "path": ".."})
        assert "content" in invoke("read_file", {"path": str(context.root / "hi.py")})
        assert invoke("glob", {"pattern": "../*"})["matches"] == []
        v = invoke("run_bash", {"command": "pwd"}, approve=approved)
        assert v["stdout"].strip() == str(context.root), v

    # 범용 도구
    assert dispatch("echo", {"text": "hi"})["text"] == "hi"
    assert "confirmed" in str(dispatch("mock_order_lookup", {"order_id": "#12345"}))
    assert "error" in dispatch("mock_order_lookup", {"order_id": "00000"})
    assert dispatch("search_docs", {"query": "mlx"})["count"] == 1
    assert "error" in dispatch("http_get", {"url": "file:///etc/passwd"})
    assert "error" in dispatch("nope", {})

    # 승인 분류가 보존됐는지 — agent 노드 안전 규칙이 여기 기댄다
    # http_get도 승인 대상이다 — 이 단언이 3개만 기대해 self-check가 실패하고 있었고,
    # 아무도 이 모듈을 돌리지 않아 드러나지 않았다.
    assert DANGEROUS == {"write_file", "edit_file", "run_bash", "http_get"}, DANGEROUS
    assert "run_bash" not in READ_ONLY and "grep" in READ_ONLY
    assert all("fn" not in d and "handler" not in d for d in listing())
    assert len(schemas_for(["grep", "nope"])) == 1

    print(f"OK — 도구 {len(TOOLS)}개 (읽기전용 {len(READ_ONLY)}, 승인필요 {len(DANGEROUS)})")


if __name__ == "__main__":
    demo()
