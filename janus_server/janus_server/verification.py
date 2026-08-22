"""Acceptance command execution bound to an explicit WorkspaceContext."""

from __future__ import annotations

import subprocess
import threading
import time
import uuid
from collections.abc import Callable

from . import scheduler as scheduler_mod
from .workspace import WorkspaceContext


def run(
    command: str, context: WorkspaceContext, *, timeout: float = 120,
    output_limit: int = 8000,
    scheduler: scheduler_mod.ResourceScheduler | None = None,
    priority: int = 0,
    cancel: threading.Event | None = None,
    emit: Callable[..., None] | None = None,
    queue_timeout: float | None = scheduler_mod.DEFAULT_QUEUE_TIMEOUT,
) -> dict:
    if not str(command).strip():
        raise ValueError("verification command가 필요합니다")
    scheduler = scheduler or scheduler_mod.default_scheduler()
    emit = emit or (lambda *_args, **_kwargs: None)
    operation_id = uuid.uuid4().hex[:16]
    emit("resource_queue_enter", resource="verification", operation_id=operation_id,
         command=command)
    try:
        lease = scheduler.acquire(
            scheduler_mod.ResourceClass.VERIFICATION,
            priority=priority,
            owner_id=context.dispatch_id,
            cancel=cancel,
            timeout=queue_timeout,
            on_wait=lambda wait: emit(
                "resource_queue_wait", operation_id=operation_id,
                command=command, **wait,
            ),
        )
    except scheduler_mod.LeaseCancelled:
        emit("resource_queue_end", resource="verification", operation_id=operation_id,
             command=command, status="cancelled")
        return {
            "command": command, "workspace_root": str(context.root),
            **context.identifiers(), "exit_code": None, "stdout": "", "stderr": "",
            "duration_ms": 0.0, "error": "verification lease 대기가 취소됨",
        }
    except scheduler_mod.LeaseTimeout:
        emit("resource_queue_end", resource="verification", operation_id=operation_id,
             command=command, status="timeout")
        return {
            "command": command, "workspace_root": str(context.root),
            **context.identifiers(), "exit_code": None, "stdout": "", "stderr": "",
            "duration_ms": 0.0,
            "error": f"verification lease timeout({queue_timeout:g}s)",
        }
    except scheduler_mod.SchedulerClosed:
        emit("resource_queue_end", resource="verification", operation_id=operation_id,
             command=command, status="shutdown")
        return {
            "command": command, "workspace_root": str(context.root),
            **context.identifiers(), "exit_code": None, "stdout": "", "stderr": "",
            "duration_ms": 0.0, "error": "ResourceScheduler가 종료됨",
        }
    except Exception:
        emit("resource_queue_end", resource="verification", operation_id=operation_id,
             command=command, status="error")
        raise

    with lease:
        emit("resource_lease_acquired", resource="verification", operation_id=operation_id,
             lease_id=lease.id, command=command)
        emit("verification_start", operation_id=operation_id, command=command)
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
            result = {
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
            result = {
                "command": command,
                "workspace_root": str(context.root),
                **context.identifiers(),
                "exit_code": None,
                "stdout": (stdout or "")[-output_limit:],
                "stderr": (stderr or "")[-output_limit:],
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                "error": f"acceptance timeout({timeout:g}s)",
            }
        except Exception:
            emit("verification_end", operation_id=operation_id, status="error")
            raise
        emit("verification_end", operation_id=operation_id,
             status="success" if result["exit_code"] == 0 else "error")
        return result
