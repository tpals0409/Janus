"""Codex/Claude/Agent Skills를 Janus 네이티브 Skill IR로 변환한다.

가져오기는 데이터 처리일 뿐이다. 원본의 스크립트, hook, 동적 명령은 절대 실행하지 않는다.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import re
import stat
import urllib.parse
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path

import yaml

MAX_SKILL_FILES = 200
MAX_SKILL_BYTES = 2_000_000
MAX_FILE_BYTES = 512_000
MAX_ARCHIVE_BYTES = 20_000_000
MAX_ARCHIVE_UNPACKED_BYTES = 40_000_000
MAX_ARCHIVE_ENTRIES = 2_000
MAX_DISCOVERY_DEPTH = 8
IGNORED_PARTS = {".git", "node_modules", ".venv", "__pycache__", "dist", "out"}

PORTABLE_FRONTMATTER = {
    "name", "description", "license", "compatibility", "metadata", "allowed-tools",
}
CLAUDE_ONLY_FRONTMATTER = {
    "when_to_use", "argument-hint", "arguments", "disable-model-invocation",
    "user-invocable", "effort", "context", "agent", "background", "hooks", "paths", "shell",
}
TOOL_MAP = {
    "read": "read_file", "read_file": "read_file",
    "glob": "glob", "grep": "grep",
    "write": "write_file", "write_file": "write_file",
    "edit": "edit_file", "edit_file": "edit_file",
    "bash": "run_bash", "run_bash": "run_bash",
    "webfetch": "http_get", "web_fetch": "http_get", "http_get": "http_get",
    "task": "create_worker", "agent": "create_worker", "create_worker": "create_worker",
}
RISKY_CAPABILITIES = {"write_file", "edit_file", "run_bash", "http_get", "create_worker"}
LICENSE_FILENAMES = {"license", "license.md", "license.txt", "copying", "copying.txt"}


class SkillImportError(ValueError):
    pass


def _safe_name(value: object, fallback: str) -> str:
    name = str(value or fallback).strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,99}", name):
        raise SkillImportError(f"지원하지 않는 스킬 이름: {name!r}")
    return name


def _frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].strip() != "---":
        return {}, text
    end = next((index for index, line in enumerate(lines[1:], 1) if line.strip() == "---"), None)
    if end is None:
        raise SkillImportError("SKILL.md YAML frontmatter가 닫히지 않았습니다")
    try:
        value = yaml.safe_load("".join(lines[1:end])) or {}
    except yaml.YAMLError as error:
        raise SkillImportError(f"SKILL.md YAML 오류: {error}") from error
    if not isinstance(value, dict):
        raise SkillImportError("SKILL.md frontmatter는 객체여야 합니다")
    return value, "".join(lines[end + 1:]).lstrip("\r\n")


def _allowed_tools(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        pattern = re.compile(r"[A-Za-z_][A-Za-z0-9_-]*(?:\([^)]*\))?")
        items = pattern.findall(value)
        remainder = pattern.sub("", value)
        if remainder.strip(" \t\r\n,"):
            raise SkillImportError("allowed-tools 문자열 형식을 해석할 수 없습니다")
        return items
    if isinstance(value, list) and all(isinstance(item, str) for item in value):
        return list(value)
    raise SkillImportError("allowed-tools는 문자열 또는 문자열 배열이어야 합니다")


def _map_tool(tool: str) -> str | None:
    """Map Claude permission patterns such as `Bash(git:*)` by their tool family."""
    match = re.match(r"^([A-Za-z_][A-Za-z0-9_-]*)\s*(?:\(|$)", tool.strip())
    family = match.group(1).lower() if match else tool.strip().lower()
    return TOOL_MAP.get(family)


def _read_skill_files(root: Path) -> tuple[list[dict], str]:
    root = root.resolve()
    if not root.is_dir():
        raise SkillImportError(f"스킬 디렉터리가 아닙니다: {root}")
    records: list[dict] = []
    digest = hashlib.sha256()
    total = 0
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root)
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise SkillImportError(f"심볼릭 링크는 가져올 수 없습니다: {relative}")
        if not path.is_file():
            continue
        if len(records) >= MAX_SKILL_FILES:
            raise SkillImportError(f"스킬 파일은 최대 {MAX_SKILL_FILES}개입니다")
        size = path.stat().st_size
        if size > MAX_FILE_BYTES:
            raise SkillImportError(f"스킬 파일이 너무 큽니다: {relative} ({size} bytes)")
        total += size
        if total > MAX_SKILL_BYTES:
            raise SkillImportError(f"스킬 전체 크기는 최대 {MAX_SKILL_BYTES} bytes입니다")
        raw = path.read_bytes()
        relative_text = relative.as_posix()
        digest.update(relative_text.encode("utf-8"))
        digest.update(b"\0")
        digest.update(raw)
        digest.update(b"\0")
        binary = b"\x00" in raw[:8192]
        decoded: str | None = None
        if not binary:
            try:
                decoded = raw.decode("utf-8")
            except UnicodeDecodeError:
                binary = True
        records.append({
            "path": relative_text,
            "size": size,
            "sha256": hashlib.sha256(raw).hexdigest(),
            "binary": binary,
            "content": None if binary else decoded,
            "content_base64": base64.b64encode(raw).decode("ascii") if binary else None,
        })
    if not any(item["path"] == "SKILL.md" for item in records):
        raise SkillImportError(f"SKILL.md가 없습니다: {root}")
    return records, digest.hexdigest()


def discover_skill_directories(root: str | Path) -> list[Path]:
    base = Path(root).expanduser().resolve()
    if not base.is_dir():
        raise SkillImportError(f"디렉터리가 아닙니다: {base}")
    found: list[Path] = []
    for entrypoint in sorted(base.rglob("SKILL.md")):
        relative = entrypoint.relative_to(base)
        if len(relative.parts) - 1 > MAX_DISCOVERY_DEPTH:
            continue
        if any(part in IGNORED_PARTS for part in relative.parts):
            continue
        if entrypoint.is_symlink() or any(parent.is_symlink() for parent in entrypoint.parents if parent != base.parent):
            continue
        found.append(entrypoint.parent)
        if len(found) > MAX_SKILL_FILES:
            raise SkillImportError(f"한 소스에서 스킬은 최대 {MAX_SKILL_FILES}개입니다")
    return found


def _convert_instructions(body: str) -> tuple[str, list[str]]:
    warnings: list[str] = []
    converted = body.replace("${CLAUDE_PROJECT_DIR}", "{{workspace_root}}")
    converted = converted.replace("${CLAUDE_SESSION_ID}", "{{session_id}}")
    converted = converted.replace("$ARGUMENTS", "{{input}}")

    dynamic = re.compile(r"^\s*!`([^`]+)`\s*$", re.MULTILINE)
    if dynamic.search(converted):
        warnings.append("Claude 동적 명령을 자동 실행하지 않고 승인형 run_bash 단계로 변환했습니다")
        converted = dynamic.sub(
            lambda match: f"[Janus 승인 필요: run_bash `{match.group(1)}` 결과를 먼저 확인하세요]",
            converted,
        )
    return converted, warnings


def compile_skill_directory(
    directory: str | Path, *, source_kind: str, source_locator: str,
    source_subpath: str = "", source_revision: str | None = None,
    namespace: str | None = None,
) -> dict:
    root = Path(directory).expanduser().resolve()
    files, content_hash = _read_skill_files(root)
    entrypoint = next(item for item in files if item["path"] == "SKILL.md")
    frontmatter, body = _frontmatter(str(entrypoint["content"] or ""))
    name = _safe_name(frontmatter.get("name"), root.name)
    description = str(frontmatter.get("description") or "").strip()
    if not description:
        first = next((line.strip("# ") for line in body.splitlines() if line.strip()), "")
        description = first[:500]
    if len(description) > 2_000:
        description = description[:2_000]

    requested = _allowed_tools(frontmatter.get("allowed-tools"))
    capabilities: list[str] = []
    unmapped: list[str] = []
    for tool in requested:
        mapped = _map_tool(tool)
        if mapped and mapped not in capabilities:
            capabilities.append(mapped)
        elif not mapped:
            unmapped.append(tool)

    instructions, warnings = _convert_instructions(body)
    unknown_fields = sorted(set(map(str, frontmatter)) - PORTABLE_FRONTMATTER - CLAUDE_ONLY_FRONTMATTER)
    if unknown_fields:
        warnings.append(f"알 수 없는 frontmatter 필드: {', '.join(unknown_fields)}")
    if unmapped:
        warnings.append(f"Janus capability로 변환할 수 없는 도구: {', '.join(unmapped)}")
    script_paths = [item["path"] for item in files if item["path"].startswith("scripts/")]
    license_path = next(
        (item["path"] for item in files if Path(item["path"]).name.lower() in LICENSE_FILENAMES),
        None,
    )
    declared_license = frontmatter.get("license")
    license_name = str(declared_license).strip() if declared_license else None
    if script_paths:
        warnings.append("포함된 scripts/ 파일은 리소스로만 보관하며 자동 실행하지 않습니다")
    has_mcp_dependency = bool(re.search(r"\bmcp__|\bMCP server\b", instructions, re.IGNORECASE))
    if has_mcp_dependency:
        warnings.append("MCP 의존성에는 별도의 Janus 어댑터가 필요합니다")

    compatibility = "native"
    if has_mcp_dependency:
        compatibility = "adapter_required"
    elif warnings or unmapped:
        compatibility = "partial"

    effective_namespace = _safe_name(namespace, source_kind)
    source_key = hashlib.sha256(
        f"{source_kind}\0{source_locator}\0{source_subpath}".encode()
    ).hexdigest()
    original = {
        "frontmatter": frontmatter,
        "entrypoint": str(entrypoint["content"] or ""),
        "files": files,
    }
    compiled = {
        "format": "janus.skill.v1",
        "name": name,
        "description": description,
        "instructions": instructions,
        "activation": {
            "model_invocable": not bool(frontmatter.get("disable-model-invocation", False)),
            "user_invocable": bool(frontmatter.get("user-invocable", True)),
            "paths": frontmatter.get("paths") or [],
        },
        "execution": {
            "context": "worker" if frontmatter.get("context") == "fork" else "inline",
            "agent": frontmatter.get("agent"),
        },
        "capabilities": {
            "required": capabilities,
            "approval_required": sorted(set(capabilities) & RISKY_CAPABILITIES),
            "unmapped": unmapped,
        },
        "resources": [
            {key: value for key, value in item.items() if key != "content_base64"}
            for item in files if item["path"] != "SKILL.md"
        ],
        "variables": ["input", "workspace_root", "session_id"],
    }
    report = {
        "compatibility": compatibility,
        "warnings": warnings,
        "blocked_features": script_paths,
        "source_fields": sorted(map(str, frontmatter)),
        "file_count": len(files),
        "total_bytes": sum(int(item["size"]) for item in files),
        "estimated_prompt_tokens": max(1, len(instructions) // 4),
        "license": license_name,
        "license_file": license_path,
    }
    return {
        "namespace": effective_namespace,
        "name": name,
        "description": description,
        "source_kind": source_kind,
        "source_locator": source_locator,
        "source_subpath": source_subpath,
        "source_key": source_key,
        "source_revision": source_revision,
        "content_hash": content_hash,
        "original": original,
        "compiled": compiled,
        "report": report,
        "compatibility": compatibility,
    }


def parse_github_url(url: str) -> dict:
    parsed = urllib.parse.urlparse(url.strip())
    if parsed.scheme != "https" or parsed.hostname not in {"github.com", "www.github.com"}:
        raise SkillImportError("https://github.com GitHub URL만 지원합니다")
    parts = [urllib.parse.unquote(item) for item in parsed.path.split("/") if item]
    if len(parts) < 2:
        raise SkillImportError("GitHub URL에는 owner/repository가 필요합니다")
    owner, repository = parts[:2]
    if repository.endswith(".git"):
        repository = repository[:-4]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", owner) or not re.fullmatch(r"[A-Za-z0-9_.-]+", repository):
        raise SkillImportError("지원하지 않는 GitHub owner/repository입니다")
    ref = None
    subpath = ""
    if len(parts) > 2:
        if len(parts) < 4 or parts[2] not in {"tree", "blob"}:
            raise SkillImportError(
                "저장소 루트, /tree/<ref>/<path>, 또는 SKILL.md /blob/ URL을 사용하세요"
            )
        ref = parts[3]
        subpath = "/".join(parts[4:])
        if parts[2] == "blob":
            if not subpath.lower().endswith("/skill.md") and subpath.lower() != "skill.md":
                raise SkillImportError("GitHub blob URL은 SKILL.md 파일을 가리켜야 합니다")
            subpath = str(Path(subpath).parent.as_posix())
            if subpath == ".":
                subpath = ""
    return {
        "owner": owner,
        "repository": repository,
        "ref": ref,
        "subpath": subpath,
        "canonical_url": f"https://github.com/{owner}/{repository}",
    }


def _request_json(url: str) -> dict:
    headers = {"Accept": "application/vnd.github+json", "User-Agent": "Janus/1.0"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=15) as response:
        raw = response.read(1_000_001)
    if len(raw) > 1_000_000:
        raise SkillImportError("GitHub metadata 응답이 너무 큽니다")
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise SkillImportError("GitHub metadata 응답이 올바르지 않습니다")
    return value


def _request_bytes(url: str) -> bytes:
    headers = {"User-Agent": "Janus/1.0"}
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        raw = response.read(MAX_ARCHIVE_BYTES + 1)
    if len(raw) > MAX_ARCHIVE_BYTES:
        raise SkillImportError(f"GitHub archive는 최대 {MAX_ARCHIVE_BYTES} bytes입니다")
    return raw


def download_github_skills(
    url: str, destination: str | Path, *,
    fetch_json: Callable[[str], dict] = _request_json,
    fetch_bytes: Callable[[str], bytes] = _request_bytes,
) -> dict:
    source = parse_github_url(url)
    api_root = f"https://api.github.com/repos/{source['owner']}/{source['repository']}"
    metadata = fetch_json(api_root)
    requested_ref = source["ref"] or str(metadata.get("default_branch") or "main")
    commit = fetch_json(f"{api_root}/commits/{urllib.parse.quote(requested_ref, safe='')}")
    revision = str(commit.get("sha") or "")
    if not re.fullmatch(r"[a-fA-F0-9]{40}", revision):
        raise SkillImportError("GitHub commit SHA를 확인할 수 없습니다")
    archive_url = (
        f"https://codeload.github.com/{source['owner']}/{source['repository']}/zip/{revision}"
    )
    raw = fetch_bytes(archive_url)
    target = Path(destination).expanduser().resolve()
    target.mkdir(parents=True, exist_ok=True)
    try:
        archive_file = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile as error:
        raise SkillImportError("GitHub archive가 올바른 ZIP이 아닙니다") from error
    with archive_file as archive:
        entries = archive.infolist()
        if len(entries) > MAX_ARCHIVE_ENTRIES:
            raise SkillImportError(f"GitHub archive 항목은 최대 {MAX_ARCHIVE_ENTRIES}개입니다")
        unpacked_bytes = sum(entry.file_size for entry in entries if not entry.is_dir())
        if unpacked_bytes > MAX_ARCHIVE_UNPACKED_BYTES:
            raise SkillImportError(
                f"GitHub archive 압축 해제 크기는 최대 {MAX_ARCHIVE_UNPACKED_BYTES} bytes입니다"
            )
        seen_paths: set[str] = set()
        actual_unpacked_bytes = 0
        for entry in entries:
            name = entry.filename
            path = Path(name)
            if path.is_absolute() or ".." in path.parts:
                raise SkillImportError(f"GitHub archive 경로 탈출이 감지됐습니다: {name}")
            mode = entry.external_attr >> 16
            if stat.S_ISLNK(mode):
                raise SkillImportError(f"GitHub archive 심볼릭 링크는 허용하지 않습니다: {name}")
            if entry.flag_bits & 0x1:
                raise SkillImportError(f"GitHub archive 암호화 파일은 허용하지 않습니다: {name}")
            output = (target / path).resolve()
            if not output.is_relative_to(target):
                raise SkillImportError(f"GitHub archive 경로 탈출이 감지됐습니다: {name}")
            normalized = output.relative_to(target).as_posix()
            if normalized in seen_paths:
                raise SkillImportError(f"GitHub archive에 중복 경로가 있습니다: {name}")
            seen_paths.add(normalized)
            if entry.is_dir():
                output.mkdir(parents=True, exist_ok=True)
                continue
            if entry.file_size > MAX_FILE_BYTES:
                raise SkillImportError(f"GitHub archive 파일이 너무 큽니다: {name}")
            try:
                with archive.open(entry) as source_file:
                    content = source_file.read(MAX_FILE_BYTES + 1)
            except (RuntimeError, NotImplementedError, zipfile.BadZipFile) as error:
                raise SkillImportError(f"GitHub archive 파일을 안전하게 풀 수 없습니다: {name}") from error
            if len(content) > MAX_FILE_BYTES:
                raise SkillImportError(f"GitHub archive 파일이 너무 큽니다: {name}")
            actual_unpacked_bytes += len(content)
            if actual_unpacked_bytes > MAX_ARCHIVE_UNPACKED_BYTES:
                raise SkillImportError(
                    f"GitHub archive 압축 해제 크기는 최대 {MAX_ARCHIVE_UNPACKED_BYTES} bytes입니다"
                )
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)

    roots = [item for item in target.iterdir() if item.is_dir()]
    if len(roots) != 1:
        raise SkillImportError("GitHub archive의 루트 디렉터리를 확인할 수 없습니다")
    selected = roots[0]
    if source["subpath"]:
        selected = (selected / source["subpath"]).resolve()
        if not selected.is_relative_to(roots[0].resolve()):
            raise SkillImportError("GitHub 하위 경로가 저장소 밖을 가리킵니다")
    directories = discover_skill_directories(selected)
    if not directories:
        raise SkillImportError("선택한 GitHub 경로에서 SKILL.md를 찾지 못했습니다")
    return {
        **source,
        "requested_ref": requested_ref,
        "revision": revision.lower(),
        "license": (metadata.get("license") or {}).get("spdx_id") if isinstance(metadata.get("license"), dict) else None,
        "root": selected,
        "skill_directories": directories,
    }
