"""Privacy-bounded diagnostic bundles that never include the Janus database."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import re
import sqlite3
import tempfile
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from .recovery import database_integrity
from .version import __version__

MAX_LOG_BYTES = 1_000_000
MAX_LOG_FILES = 4
_SECRET_PATTERNS = (
    re.compile(r"(?i)(generated auth token\s*:\s*)\S+"),
    re.compile(r"(?i)(x-janus-token\s*[=:]\s*)\S+"),
    re.compile(r"(?i)(authorization\s*[=:]\s*bearer\s+)\S+"),
    re.compile(
        r"(?i)(['\"]?(?:api[_-]?key|token|secret|password)['\"]?\s*[=:]\s*)"
        r"['\"]?[^\s,'\"]+"
    ),
)


def redact(value: str) -> str:
    redacted = value.replace(str(Path.home()), "~")
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1[REDACTED]", redacted)
    return redacted


def _schema_version(database: Path) -> int | None:
    if not database.is_file():
        return None
    connection: sqlite3.Connection | None = None
    try:
        connection = sqlite3.connect(f"file:{database.resolve()}?mode=ro", uri=True)
        return int(connection.execute("PRAGMA user_version").fetchone()[0])
    except sqlite3.Error:
        return None
    finally:
        if connection is not None:
            connection.close()


def _log_tail(path: Path) -> tuple[str, bool]:
    size = path.stat().st_size
    with path.open("rb") as handle:
        if size > MAX_LOG_BYTES:
            handle.seek(-MAX_LOG_BYTES, os.SEEK_END)
        raw = handle.read(MAX_LOG_BYTES)
    return redact(raw.decode("utf-8", errors="replace")), size > MAX_LOG_BYTES


def create_diagnostic_bundle(
    *, database: str | Path, log_dir: str | Path, output_dir: str | Path,
) -> dict:
    database_path = Path(database).expanduser().resolve()
    logs = Path(log_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    destination = output / f"janus-diagnostics-{stamp}.zip"
    fd, temporary_name = tempfile.mkstemp(prefix=".janus-diagnostics-", suffix=".tmp", dir=output)
    os.close(fd)
    temporary = Path(temporary_name)
    included: list[str] = []
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            log_reports = []
            candidates = sorted(logs.glob("janus-*.log"))[:MAX_LOG_FILES] if logs.is_dir() else []
            for path in candidates:
                tail, truncated = _log_tail(path)
                archive_name = f"logs/{path.name}"
                archive.writestr(archive_name, tail)
                included.append(archive_name)
                log_reports.append({
                    "name": path.name, "source_size_bytes": path.stat().st_size,
                    "included_bytes": len(tail.encode("utf-8")), "truncated": truncated,
                })
            manifest = {
                "created_at": datetime.now(UTC).isoformat(),
                "janus_version": __version__,
                "schema_version": _schema_version(database_path),
                "database_integrity": database_integrity(database_path),
                "platform": {
                    "system": platform.system(), "release": platform.release(),
                    "machine": platform.machine(), "python": platform.python_version(),
                },
                "environment_presence": {
                    name: name in os.environ
                    for name in (
                        "JANUS_DB_FILE", "JANUS_WORKTREES_DIR", "JANUS_BACKUPS_DIR",
                        "JANUS_LOG_DIR", "JANUS_AUTH_TOKEN", "JANUS_ALLOWED_ORIGINS",
                    )
                },
                "logs": log_reports,
                "privacy": {
                    "database_included": False, "environment_values_included": False,
                    "home_path_redacted": True, "secret_patterns_redacted": True,
                },
            }
            archive.writestr(
                "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True)
            )
            included.append("manifest.json")
        os.chmod(temporary, 0o600)
        os.replace(temporary, destination)
    except Exception:
        try:
            temporary.unlink()
        except OSError:
            pass
        raise
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    return {
        "path": str(destination), "size_bytes": destination.stat().st_size,
        "sha256": digest, "files": included, "redacted": True,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Create a redacted Janus diagnostic bundle")
    home = Path.home() / ".janus"
    parser.add_argument("--database", type=Path, default=home / "janus.sqlite3")
    parser.add_argument(
        "--log-dir", type=Path,
        default=Path(os.environ.get("JANUS_LOG_DIR", str(home / "logs"))),
    )
    parser.add_argument("--output-dir", type=Path, default=home / "diagnostics")
    args = parser.parse_args()
    print(json.dumps(create_diagnostic_bundle(
        database=args.database, log_dir=args.log_dir, output_dir=args.output_dir,
    ), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
