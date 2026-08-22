"""Acceptance command execution bound to an explicit WorkspaceContext."""

from __future__ import annotations

import subprocess
import time

from .workspace import WorkspaceContext


def run(
    command: str, context: WorkspaceContext, *, timeout: float = 120,
    output_limit: int = 8000,
) -> dict:
    if not str(command).strip():
        raise ValueError("verification command가 필요합니다")
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=context.root,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        return {
            "command": command,
            "workspace_root": str(context.root),
            **context.identifiers(),
            "exit_code": completed.returncode,
            "stdout": completed.stdout[-output_limit:],
            "stderr": completed.stderr[-output_limit:],
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout.decode(errors="replace") if isinstance(exc.stdout, bytes) else exc.stdout
        stderr = exc.stderr.decode(errors="replace") if isinstance(exc.stderr, bytes) else exc.stderr
        return {
            "command": command,
            "workspace_root": str(context.root),
            **context.identifiers(),
            "exit_code": None,
            "stdout": (stdout or "")[-output_limit:],
            "stderr": (stderr or "")[-output_limit:],
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
            "error": f"acceptance timeout({timeout:g}s)",
        }
