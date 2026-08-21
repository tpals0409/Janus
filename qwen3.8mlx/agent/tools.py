"""코딩 에이전트 도구 6개.

각 도구의 handler는 **구조화된 dict**를 반환하고, render()가 그걸 모델용 텍스트로
바꾼다. 이 분리가 중요한 이유: 이벤트 로그에 원본 dict가 남아야 나중에 UI가 diff를
그릴 수 있다. render() 결과만 잘라 보내고 원본은 자르지 않는다.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

MAX_RENDER_CHARS = 4000  # 모델에게 보내는 텍스트 상한 (원본 dict는 자르지 않음)
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
    # old를 남겨야 UI가 덮어쓰기도 diff로 보여줄 수 있다.
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


# ─────────────────────────── renderers ───────────────────────────


def _render_read(v):
    return v["content"]


def _render_glob(v):
    return "\n".join(v["matches"]) if v["matches"] else "(매치 없음)"


def _render_grep(v):
    return "\n".join(v["matches"]) if v["matches"] else "(매치 없음)"


def _render_write(v):
    verb = "생성" if v["created"] else "덮어씀"
    return f"<path>{v['path']}</path>\n<result>{verb}, {len(v['new'])}자</result>"


def _render_edit(v):
    return f"<path>{v['path']}</path>\n<result>치환 완료</result>"


def _render_bash(v):
    out = (v["stdout"] + v["stderr"]).strip()
    return f"{out}\n[exit code: {v['exit_code']}]" if out else f"[exit code: {v['exit_code']}]"


# ─────────────────────────── registry ───────────────────────────
# guidance는 각 도구 옆에 둔다 (deepseek-harness 패턴). loop.py가 모아 시스템 프롬프트로.

TOOLS = [
    {
        "name": "read_file", "handler": _read_file, "render": _render_read,
        "needs_approval": False,
        "description": "Read the full contents of a file.",
        "guidance": "Read a file before editing it.",
        "schema": {"type": "object", "required": ["path"], "properties": {
            "path": {"type": "string", "description": "Path to the file."}}},
    },
    {
        "name": "glob", "handler": _glob, "render": _render_glob,
        "needs_approval": False,
        "description": "Find files by glob pattern, relative to the working directory.",
        "guidance": "Use glob to locate files by name, e.g. '**/*.py'.",
        "schema": {"type": "object", "required": ["pattern"], "properties": {
            "pattern": {"type": "string", "description": "Glob pattern, e.g. '**/*.py'."}}},
    },
    {
        "name": "grep", "handler": _grep, "render": _render_grep,
        "needs_approval": False,
        "description": "Search file contents by regex. Returns 'file:line:text' matches.",
        "guidance": "Use grep to find where something is defined or used.",
        "schema": {"type": "object", "required": ["pattern"], "properties": {
            "pattern": {"type": "string", "description": "Regular expression."},
            "path": {"type": "string", "description": "Directory to search. Default '.'."}}},
    },
    {
        "name": "write_file", "handler": _write_file, "render": _render_write,
        "needs_approval": True,
        "description": "Write content to a file, creating or overwriting it.",
        # 규칙은 예시 옆에 붙인다 — 모델은 예시에 앵커하고 뒤따르는 산문은 흘려 읽는다 (orca).
        "guidance": "Use write_file only for NEW files or full rewrites. "
                    "To change part of an existing file use edit_file — it is far cheaper.",
        "schema": {"type": "object", "required": ["path", "content"], "properties": {
            "path": {"type": "string", "description": "Path to the file."},
            "content": {"type": "string", "description": "Full file content."}}},
    },
    {
        "name": "edit_file", "handler": _edit_file, "render": _render_edit,
        "needs_approval": True,
        "description": "Replace an exact string in a file. old_string must be unique.",
        "guidance": "Prefer edit_file over write_file for existing files. "
                    "If old_string is not unique, include more surrounding lines.",
        "schema": {"type": "object",
                   "required": ["path", "old_string", "new_string"], "properties": {
                       "path": {"type": "string", "description": "Path to the file."},
                       "old_string": {"type": "string",
                                      "description": "Exact text to replace. Must appear once."},
                       "new_string": {"type": "string", "description": "Replacement text."}}},
    },
    {
        "name": "run_bash", "handler": _run_bash, "render": _render_bash,
        "needs_approval": True,
        "description": "Run a shell command in the working directory.",
        "guidance": "Check the [exit code: N] on every result. "
                    "A non-zero code means it failed — investigate before continuing.",
        "schema": {"type": "object", "required": ["command"], "properties": {
            "command": {"type": "string", "description": "Shell command to run."}}},
    },
]

BY_NAME = {t["name"]: t for t in TOOLS}

TOOL_SCHEMAS = [
    {"type": "function", "function": {
        "name": t["name"], "description": t["description"], "parameters": t["schema"]}}
    for t in TOOLS
]

GUIDANCE = "\n".join(f"- {t['name']}: {t['guidance']}" for t in TOOLS)


def dispatch(name: str, args: dict) -> dict:
    """도구 실행. 반환값은 구조화된 dict (이벤트 로그에 그대로 저장)."""
    tool = BY_NAME.get(name)
    if tool is None:
        return {"error": f"알 수 없는 도구: {name}"}
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
    return _clip(BY_NAME[name]["render"](value))


# ─────────────────────────── self-check ───────────────────────────


def demo():
    """모델 없이 도구만 검증."""
    import tempfile

    cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as d:
        os.chdir(d)
        try:
            v = dispatch("write_file", {"path": "hi.py", "content": "x = 1\ny = 2\n"})
            assert v["created"] and v["old"] is None, v
            assert Path("hi.py").read_text() == "x = 1\ny = 2\n"

            v = dispatch("read_file", {"path": "hi.py"})
            assert v["content"] == "x = 1\ny = 2\n", v

            v = dispatch("edit_file",
                         {"path": "hi.py", "old_string": "x = 1", "new_string": "x = 99"})
            # UI diff에 필요한 old/new가 둘 다 남아야 한다
            assert v["old"] == "x = 1\ny = 2\n" and v["new"] == "x = 99\ny = 2\n", v

            v = dispatch("edit_file",
                         {"path": "hi.py", "old_string": "없는문자열", "new_string": "z"})
            assert "error" in v, v

            v = dispatch("write_file", {"path": "dup.py", "content": "a\na\n"})
            v = dispatch("edit_file",
                         {"path": "dup.py", "old_string": "a", "new_string": "b"})
            assert "error" in v and "2번" in v["error"], v

            v = dispatch("glob", {"pattern": "*.py"})
            assert len(v["matches"]) == 2, v

            v = dispatch("grep", {"pattern": "x = 99"})
            assert v["count"] == 1 and "hi.py" in v["matches"][0], v

            v = dispatch("grep", {"pattern": "절대없는패턴xyzzy"})
            assert v["count"] == 0 and "error" not in v, v

            v = dispatch("run_bash", {"command": "echo hello"})
            assert v["exit_code"] == 0 and v["stdout"].strip() == "hello", v
            assert render("run_bash", v) == "hello\n[exit code: 0]"

            v = dispatch("run_bash", {"command": "exit 3"})
            assert v["exit_code"] == 3, v

            v = dispatch("read_file", {"path": "없는파일.py"})
            assert "error" in v and render("read_file", v).startswith("ERROR:"), v

            long = dispatch("write_file", {"path": "big.txt", "content": "z" * 20000})
            assert len(render("read_file", dispatch("read_file", {"path": "big.txt"}))) \
                < MAX_RENDER_CHARS + 100
        finally:
            os.chdir(cwd)

    print(f"OK — 도구 {len(TOOLS)}개 통과")


if __name__ == "__main__":
    demo()
