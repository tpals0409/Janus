"""통합 도구 레지스트리.

`tool` 노드와 `agent` 노드가 같은 레지스트리를 쓴다. agent 노드는 이 중 일부만
골라 갖는다.

handler는 **구조화된 dict**를 반환하고 render()가 모델용 텍스트로 바꾼다. 이벤트
로그에 원본 dict가 남아야 UI가 diff 같은 걸 그릴 수 있다 — render 결과만 자르고
원본은 자르지 않는다.

주의: 파일 도구는 서버 프로세스의 cwd 기준으로 경로를 해석한다. 워크스페이스 격리는
아직 없다 — 승인 프롬프트가 경로를 그대로 보여주는 것이 현재의 유일한 방어선이다.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import urllib.request
from pathlib import Path

MAX_RENDER_CHARS = 4000
BASH_TIMEOUT = 120


def _clip(text: str) -> str:
    if len(text) <= MAX_RENDER_CHARS:
        return text
    half = MAX_RENDER_CHARS // 2
    omitted = len(text) - MAX_RENDER_CHARS
    return f"{text[:half]}\n\n... [{omitted}자 생략] ...\n\n{text[-half:]}"


# ─────────────────────────── handlers ───────────────────────────
# handler는 예외를 던지지 않고 {"error": ...}를 반환한다. 모델이 스스로 복구하게 둔다.


def _read_file(path: str, **_):
    p = Path(path).expanduser()
    if not p.is_file():
        return {"error": f"파일 없음: {path}"}
    return {"path": str(p), "content": p.read_text(encoding="utf-8", errors="replace")}


def _glob(pattern: str, **_):
    matches = sorted(str(p) for p in Path.cwd().glob(pattern) if p.is_file())
    return {"pattern": pattern, "matches": matches}


def _grep(pattern: str, path: str = ".", **_):
    if shutil.which("rg"):
        r = subprocess.run(
            ["rg", "--line-number", "--no-heading", "--color=never", pattern, path],
            capture_output=True, text=True, timeout=60,
        )
        # rg는 매치 없음에 exit 1을 쓴다. 그건 에러가 아니다.
        if r.returncode not in (0, 1):
            return {"error": r.stderr.strip() or f"rg 실패 (exit {r.returncode})"}
        lines = r.stdout.splitlines()
    else:
        rx = re.compile(pattern)
        lines = []
        for f in Path(path).rglob("*"):
            if not f.is_file():
                continue
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    if rx.search(line):
                        lines.append(f"{f}:{i}:{line}")
            except OSError:
                continue
    return {"pattern": pattern, "path": path, "matches": lines, "count": len(lines)}


def _write_file(path: str, content: str, **_):
    p = Path(path).expanduser()
    existed = p.is_file()
    old = p.read_text(encoding="utf-8", errors="replace") if existed else None
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return {"path": str(p), "created": not existed, "old": old, "new": content}


def _edit_file(path: str, old_string: str, new_string: str, **_):
    p = Path(path).expanduser()
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


def _run_bash(command: str, **_):
    try:
        r = subprocess.run(command, shell=True, capture_output=True, text=True,
                           timeout=BASH_TIMEOUT, cwd=os.getcwd())
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

def _t(name, handler, render, schema, description, guidance="", needs_approval=False):
    return {"name": name, "handler": handler, "render": render, "schema": schema,
            "description": description, "guidance": guidance,
            "needs_approval": needs_approval}


def _obj(required, **props):
    return {"type": "object", "required": required, "properties": props}


_S = {"type": "string"}
_N = {"type": "number"}

TOOLS = [
    # ── 읽기 전용 ──
    _t("read_file", _read_file, _r_read,
       _obj(["path"], path={**_S, "description": "Path to the file."}),
       "Read the full contents of a file.",
       "Read a file before editing it."),
    _t("glob", _glob, _r_glob,
       _obj(["pattern"], pattern={**_S, "description": "Glob pattern, e.g. '**/*.py'."}),
       "Find files by glob pattern, relative to the working directory.",
       "Use glob to locate files by name."),
    _t("grep", _grep, _r_grep,
       _obj(["pattern"], pattern={**_S, "description": "Regular expression."},
            path={**_S, "description": "Directory to search. Default '.'."}),
       "Search file contents by regex. Returns 'file:line:text' matches.",
       "Use grep to find where something is defined or used."),
    _t("http_get", _http_get, _r_http,
       _obj(["url"], url={**_S, "description": "http/https URL."},
            timeout={**_N, "description": "Seconds. Default 10."}),
       "Fetch a URL and return the response body as text.",
       "Use http_get to read a public web page or API."),
    _t("search_docs", _search_docs, _r_docs,
       _obj(["query"], query={**_S, "description": "Search terms."}),
       "Search a small built-in document set. Demo data, not a real index.",
       "Use search_docs to look up a topic before answering."),
    _t("mock_order_lookup", _mock_order_lookup, _r_json,
       _obj(["order_id"], order_id={**_S, "description": "Order id."}),
       "Look up an order in a fixed demo dataset. Not a real integration.",
       "Use mock_order_lookup when the user asks about an order."),
    _t("echo", _echo, _r_echo,
       _obj(["text"], text={**_S, "description": "Text to return."}),
       "Return the input unchanged. Useful for testing a graph.",
       "Use echo only for testing."),

    # ── 승인 필요 ──
    _t("write_file", _write_file, _r_write,
       _obj(["path", "content"], path={**_S, "description": "Path to the file."},
            content={**_S, "description": "Full file content."}),
       "Write content to a file, creating or overwriting it.",
       "Use write_file only for NEW files or full rewrites. "
       "To change part of an existing file use edit_file — it is far cheaper.",
       needs_approval=True),
    _t("edit_file", _edit_file, _r_edit,
       _obj(["path", "old_string", "new_string"],
            path={**_S, "description": "Path to the file."},
            old_string={**_S, "description": "Exact text to replace. Must appear once."},
            new_string={**_S, "description": "Replacement text."}),
       "Replace an exact string in a file. old_string must be unique.",
       "Prefer edit_file over write_file for existing files. "
       "If old_string is not unique, include more surrounding lines.",
       needs_approval=True),
    _t("run_bash", _run_bash, _r_bash,
       _obj(["command"], command={**_S, "description": "Shell command to run."}),
       "Run a shell command in the working directory.",
       "Check the [exit code: N] on every result. "
       "A non-zero code means it failed — investigate before continuing.",
       needs_approval=True),
]

REGISTRY: dict[str, dict] = {t["name"]: t for t in TOOLS}

#: 승인 없이 agent 노드에 줘도 되는 도구들
READ_ONLY = {t["name"] for t in TOOLS if not t["needs_approval"]}
#: 승인이 필요한 도구들 — agent 노드가 이걸 가지면 approval: auto 를 막는다
DANGEROUS = {t["name"] for t in TOOLS if t["needs_approval"]}


def dispatch(name: str, args: dict) -> dict:
    """도구 실행. 반환값은 구조화된 dict (이벤트 로그에 그대로 저장)."""
    tool = REGISTRY.get(name)
    if tool is None:
        return {"error": f"알 수 없는 도구: {name} (가능: {sorted(REGISTRY)})"}
    try:
        return tool["handler"](**args)
    except TypeError as e:
        return {"error": f"인자 오류: {e}"}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {e}"}


def render(name: str, value: dict) -> str:
    """모델에게 보낼 텍스트. 여기서만 자른다 — 원본 dict는 온전히 남는다."""
    if "error" in value:
        return f"ERROR: {value['error']}"
    return _clip(REGISTRY[name]["render"](value))


def schemas_for(names: list[str]) -> list[dict]:
    """OpenAI tools 파라미터 — agent 노드가 가진 도구만."""
    return [{"type": "function", "function": {
        "name": n, "description": REGISTRY[n]["description"],
        "parameters": REGISTRY[n]["schema"]}}
        for n in names if n in REGISTRY]


def guidance_for(names: list[str]) -> str:
    return "\n".join(f"- {n}: {REGISTRY[n]['guidance']}"
                     for n in names if n in REGISTRY and REGISTRY[n]["guidance"])


def listing() -> list[dict]:
    """UI 드롭다운용 — 함수 객체는 빼고 보낸다."""
    return [{"name": t["name"], "description": t["description"],
             "needs_approval": t["needs_approval"],
             "params": list(t["schema"]["properties"])}
            for t in sorted(TOOLS, key=lambda t: (t["needs_approval"], t["name"]))]


# ─────────────────────────── self-check ───────────────────────────


def demo():
    import tempfile

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            v = dispatch("write_file", {"path": "hi.py", "content": "x = 1\ny = 2\n"})
            assert v["created"] and v["old"] is None, v
            v = dispatch("read_file", {"path": "hi.py"})
            assert v["content"] == "x = 1\ny = 2\n", v

            v = dispatch("edit_file",
                         {"path": "hi.py", "old_string": "x = 1", "new_string": "x = 99"})
            # UI diff에 필요한 old/new가 둘 다 남아야 한다
            assert v["old"] == "x = 1\ny = 2\n" and v["new"] == "x = 99\ny = 2\n", v

            assert "error" in dispatch("edit_file", {"path": "hi.py",
                                                     "old_string": "없음", "new_string": "z"})
            dispatch("write_file", {"path": "dup.py", "content": "a\na\n"})
            v = dispatch("edit_file", {"path": "dup.py", "old_string": "a", "new_string": "b"})
            assert "2번" in v["error"], v

            assert len(dispatch("glob", {"pattern": "*.py"})["matches"]) == 2
            assert dispatch("grep", {"pattern": "x = 99"})["count"] == 1
            assert dispatch("grep", {"pattern": "절대없는패턴xyzzy"})["count"] == 0

            v = dispatch("run_bash", {"command": "echo hello"})
            assert v["exit_code"] == 0 and render("run_bash", v) == "hello\n[exit code: 0]"
            assert dispatch("run_bash", {"command": "exit 3"})["exit_code"] == 3

            assert render("read_file", dispatch("read_file", {"path": "없음"})).startswith("ERROR:")
            dispatch("write_file", {"path": "big.txt", "content": "z" * 20000})
            assert len(render("read_file", dispatch("read_file", {"path": "big.txt"}))) \
                < MAX_RENDER_CHARS + 100
        finally:
            os.chdir(cwd)

    # 범용 도구
    assert dispatch("echo", {"text": "hi"})["text"] == "hi"
    assert "confirmed" in str(dispatch("mock_order_lookup", {"order_id": "#12345"}))
    assert "error" in dispatch("mock_order_lookup", {"order_id": "00000"})
    assert dispatch("search_docs", {"query": "mlx"})["count"] == 1
    assert "error" in dispatch("http_get", {"url": "file:///etc/passwd"})
    assert "error" in dispatch("nope", {})

    # 승인 분류가 보존됐는지 — agent 노드 안전 규칙이 여기 기댄다
    assert DANGEROUS == {"write_file", "edit_file", "run_bash"}, DANGEROUS
    assert "run_bash" not in READ_ONLY and "grep" in READ_ONLY
    assert all("fn" not in d and "handler" not in d for d in listing())
    assert len(schemas_for(["grep", "nope"])) == 1

    print(f"OK — 도구 {len(TOOLS)}개 (읽기전용 {len(READ_ONLY)}, 승인필요 {len(DANGEROUS)})")


if __name__ == "__main__":
    demo()
