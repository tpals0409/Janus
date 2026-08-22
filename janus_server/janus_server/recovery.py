"""Failure classification and recoverable SQLite backups for local operation."""

from __future__ import annotations

import hashlib
import os
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path


MAX_FAILURE_CHARS = 4_000


def _bounded(value: object, limit: int = MAX_FAILURE_CHARS) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}… (+{len(text) - limit} chars)"


def classify_failure(error: BaseException | str) -> dict:
    """Return a stable, user-actionable recovery category without hiding detail."""
    detail = _bounded(f"{type(error).__name__}: {error}" if isinstance(error, BaseException) else error)
    lowered = detail.lower()
    if any(marker in lowered for marker in (
        "out of memory", "memoryerror", "metal command buffer", "killed: 9",
        "exit=137", "exit 137", "resource exhausted", "insufficient memory",
    )):
        kind, retryable = "model_oom", True
        action = "모델 서버가 완전히 종료된 뒤 재시작하고 동시 worker 수 또는 context를 줄여 재시도하세요."
    elif any(marker in lowered for marker in (
        "no space left", "disk full", "database or disk is full", "enospc",
        "disk i/o error", "readonly database",
    )):
        kind, retryable = "storage_write", True
        action = "디스크 여유 공간과 권한을 확보한 뒤 다시 시도하세요. 기존 데이터는 마지막 성공 transaction으로 유지됩니다."
    elif "worktree" in lowered and any(marker in lowered for marker in (
        "already checked out", "already exists", "사용 중", "충돌", "conflict",
        "등록되지 않은", "branch가 다른",
    )):
        kind, retryable = "worktree_conflict", False
        action = "충돌한 branch/worktree 소유권을 확인하고 기존 workspace를 복구하거나 별도 branch로 준비하세요."
    elif any(marker in lowered for marker in ("timed out", "timeout", "deadline exceeded")):
        kind, retryable = "timeout", True
        action = "현재 작업 상태와 로그를 확인한 뒤 더 작은 범위 또는 조정된 timeout으로 재시도하세요."
    else:
        kind, retryable = "runtime_error", True
        action = "저장된 Task 상태와 마지막 오류를 확인한 뒤 재개하거나 새 시도를 시작하세요."
    return {"kind": kind, "retryable": retryable, "detail": detail, "action": action}


def database_integrity(path: str | Path) -> dict:
    target = Path(path).expanduser().resolve()
    if not target.is_file():
        return {"ok": False, "result": "missing", "size_bytes": 0}
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{target}?mode=ro", uri=True, timeout=10)
        rows = [str(row[0]) for row in connection.execute("PRAGMA integrity_check")]
    except sqlite3.Error as error:
        return {
            "ok": False, "result": _bounded(f"{type(error).__name__}: {error}"),
            "size_bytes": target.stat().st_size,
        }
    finally:
        if connection is not None:
            connection.close()
    return {
        "ok": rows == ["ok"], "result": "\n".join(rows[:20]),
        "size_bytes": target.stat().st_size,
    }


def create_database_backup(
    source: str | Path, backup_root: str | Path, *, retain: int = 5,
) -> dict:
    """Create an online SQLite backup, verify it, atomically publish it, and prune."""
    source_path = Path(source).expanduser().resolve()
    root = Path(backup_root).expanduser().resolve()
    if not source_path.is_file():
        raise FileNotFoundError(f"database가 없습니다: {source_path}")
    if not 1 <= int(retain) <= 50:
        raise ValueError("backup retain은 1~50이어야 합니다")
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = root / f"janus-{stamp}.sqlite3"
    fd, temporary_name = tempfile.mkstemp(prefix=".janus-backup-", suffix=".tmp", dir=root)
    os.close(fd)
    temporary = Path(temporary_name)
    try:
        source_connection = sqlite3.connect(source_path, timeout=30)
        backup_connection = sqlite3.connect(temporary, timeout=30)
        try:
            source_connection.backup(backup_connection)
        finally:
            backup_connection.close()
            source_connection.close()
        integrity = database_integrity(temporary)
        if not integrity["ok"]:
            raise sqlite3.DatabaseError(f"backup integrity check 실패: {integrity['result']}")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise

    backups = sorted(root.glob("janus-*.sqlite3"), reverse=True)
    for expired in backups[int(retain):]:
        expired.unlink()
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "path": str(destination), "created_at": datetime.now(timezone.utc).isoformat(),
        "size_bytes": destination.stat().st_size, "sha256": digest,
        "integrity": integrity, "retained": min(len(backups), int(retain)),
    }


def list_database_backups(backup_root: str | Path) -> list[dict]:
    root = Path(backup_root).expanduser().resolve()
    if not root.is_dir():
        return []
    return [
        {
            "name": item.name, "size_bytes": item.stat().st_size,
            "modified_at": datetime.fromtimestamp(
                item.stat().st_mtime, tz=timezone.utc
            ).isoformat(),
        }
        for item in sorted(root.glob("janus-*.sqlite3"), reverse=True)
    ]
