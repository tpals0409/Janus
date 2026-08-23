"""Deterministic workflow state machine with durable stage checkpoints.

The engine owns ordering and state.  Stage executors only receive an immutable
stage description and return JSON-compatible output; they do not choose the
next stage.
"""

from __future__ import annotations

import json
import hashlib
import multiprocessing
import os
import tempfile
import time
import traceback
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .ownership import (
    FileOwnershipTable,
    InvalidPartition,
    OwnershipConflict,
    OwnershipViolation,
    normalize_partition,
    owns_path,
)
from . import scheduler as scheduler_mod


TERMINAL_STATES = {"completed", "failed", "skipped", "needs_human"}
STAGE_STATES = {"pending", "running", *TERMINAL_STATES}
ABSOLUTE_MAX_WORKER_SPAWNS = 32
MAX_FANOUT = 3
MAX_SUMMARY_CHARS = 8_000
DEFAULT_CONTEXT_TOKEN_THRESHOLD = 8_192


class WorkflowDefinitionError(ValueError):
    """The declared DAG is invalid."""


class CheckpointError(ValueError):
    """A checkpoint is corrupt or belongs to a different workflow."""


class WorkerExecutionError(RuntimeError):
    """An isolated worker attempt failed."""


class WorkerTimeout(WorkerExecutionError):
    """The engine terminated a worker after its absolute deadline."""


class ToolCallLimitExceeded(WorkerExecutionError):
    """The engine terminated a worker that exceeded its tool-call allowance."""


class SpawnLimitExceeded(WorkerExecutionError):
    """The engine-wide absolute worker spawn cap was exhausted."""


class HumanInterventionRequired(WorkerExecutionError):
    """The workflow reached a declared human handoff boundary."""


class IntegrationError(WorkerExecutionError):
    """Sequential merge or the single integration verification failed."""


@dataclass(frozen=True)
class ExecutionLimits:
    timeout_seconds: float
    max_tool_calls: int
    retries: int

    def __post_init__(self) -> None:
        if self.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        if self.max_tool_calls < 0:
            raise ValueError("max_tool_calls must not be negative")
        if self.retries < 0:
            raise ValueError("retries must not be negative")


class WorkerContext:
    """The only tool gateway exposed to an isolated stage worker."""

    def __init__(
        self, connection, tool_dispatcher, workspace_root: str | None,
        model_config: dict[str, str] | None,
        fanout_index: int,
        fanout_total: int,
    ):
        self._connection = connection
        self._tool_dispatcher = tool_dispatcher
        self.workspace_root = workspace_root
        self.model = dict(model_config) if model_config else None
        self.fanout_index = fanout_index
        self.fanout_total = fanout_total

    @property
    def model_key(self) -> str:
        if self.model is None:
            raise WorkerExecutionError("no model was routed to this worker")
        return str(self.model["key"])

    def call_tool(self, name: str, arguments: Mapping[str, Any]) -> Any:
        self._connection.send({
            "kind": "tool_call", "name": str(name), "arguments": dict(arguments)
        })
        response = self._connection.recv()
        if response.get("kind") == "tool_error":
            raise WorkerExecutionError(str(response.get("error") or "tool failed"))
        if self._tool_dispatcher is None:
            raise WorkerExecutionError("tools disabled")
        return self._tool_dispatcher(str(name), dict(arguments))

    def record_tokens(self, input_tokens: int, output_tokens: int) -> bool:
        for name, value in (("input_tokens", input_tokens), ("output_tokens", output_tokens)):
            if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                raise ValueError(f"{name} must be a non-negative integer")
        self._connection.send({
            "kind": "token_usage",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        })
        response = self._connection.recv()
        if response.get("kind") != "token_usage_recorded":
            raise WorkerExecutionError(
                str(response.get("error") or "engine did not record token usage")
            )
        return bool(response.get("compression_required"))

    def checkpoint_context(self, summary: str) -> None:
        text = str(summary).strip()
        if not text or len(text) > MAX_SUMMARY_CHARS:
            raise ValueError(
                f"compressed context must be 1..{MAX_SUMMARY_CHARS} characters"
            )
        self._connection.send({"kind": "context_checkpoint", "summary": text})
        response = self._connection.recv()
        if response.get("kind") != "context_checkpointed":
            raise WorkerExecutionError(
                str(response.get("error") or "engine rejected context checkpoint")
            )

    @contextmanager
    def model_slot(self):
        """Lease physical model generation capacity from the parent engine."""
        self._connection.send({"kind": "model_slot_acquire"})
        response = self._connection.recv()
        if response.get("kind") != "model_slot_acquired":
            raise WorkerExecutionError(
                str(response.get("error") or "model slot acquisition failed")
            )
        try:
            yield
        finally:
            try:
                self._connection.send({"kind": "model_slot_release"})
                self._connection.recv()
            except (BrokenPipeError, EOFError, OSError):
                pass


def _isolated_worker_entry(
    execute, stage: "Stage", connection, tool_dispatcher, workspace_root: str | None,
    model_config: dict[str, str] | None,
    fanout_index: int,
    fanout_total: int,
) -> None:
    try:
        from .airgap import local_network_only

        with local_network_only():
            result = execute(
                stage, WorkerContext(
                    connection, tool_dispatcher, workspace_root, model_config,
                    fanout_index, fanout_total,
                )
            )
        json.dumps(result, ensure_ascii=False)
        connection.send({"kind": "result", "result": result})
    except BaseException as exc:
        connection.send({
            "kind": "error",
            "type": type(exc).__name__,
            "message": str(exc),
            "traceback": traceback.format_exc(),
        })
    finally:
        connection.close()


@dataclass(frozen=True)
class Stage:
    id: str
    needs: tuple[str, ...] = ()
    on_fail: str | None = None
    write: str = "none"
    owns: tuple[str, ...] = ()
    role: str = "coder"
    fanout: int = 1
    check: str | None = None


def _validate_stages(stages: Sequence[Stage]) -> tuple[Stage, ...]:
    declared = tuple(stages)
    ids = [stage.id for stage in declared]
    if not ids:
        raise WorkflowDefinitionError("workflow requires at least one stage")
    if any(not stage_id.strip() for stage_id in ids):
        raise WorkflowDefinitionError("stage id must not be empty")
    duplicates = sorted({stage_id for stage_id in ids if ids.count(stage_id) > 1})
    if duplicates:
        raise WorkflowDefinitionError(f"duplicate stage ids: {duplicates}")

    known = set(ids)
    for stage in declared:
        missing = sorted(set(stage.needs) - known)
        if missing:
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} needs unknown stages: {missing}"
            )
        if stage.id in stage.needs:
            raise WorkflowDefinitionError(f"stage {stage.id!r} cannot need itself")
        if stage.on_fail not in (None, "human") and stage.on_fail not in known:
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} has unknown on_fail target: {stage.on_fail!r}"
            )
        if stage.on_fail == stage.id:
            raise WorkflowDefinitionError(f"stage {stage.id!r} cannot fallback to itself")
        if stage.write not in {"none", "worktree"}:
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} has invalid write mode: {stage.write!r}"
            )
        if stage.write == "worktree" and not stage.owns:
            raise WorkflowDefinitionError(
                f"write stage {stage.id!r} requires an ownership partition"
            )
        if stage.write == "none" and stage.owns:
            raise WorkflowDefinitionError(
                f"read-only stage {stage.id!r} cannot own write paths"
            )
        if stage.check is not None and stage.write != "worktree":
            raise WorkflowDefinitionError(
                f"read-only stage {stage.id!r} cannot declare a worker check"
            )
        if stage.check is not None and not str(stage.check).strip():
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} check must not be empty"
            )
        if stage.role not in {"coder", "reviewer", "summarizer"}:
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} has invalid role: {stage.role!r}"
            )
        if (
            not isinstance(stage.fanout, int)
            or isinstance(stage.fanout, bool)
            or not 1 <= stage.fanout <= MAX_FANOUT
        ):
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} fanout must be 1..{MAX_FANOUT}"
            )
        if stage.fanout > 1 and stage.write != "none":
            raise WorkflowDefinitionError(
                f"fanout stage {stage.id!r} must be read-only"
            )
        if stage.fanout > 1 and stage.role != "summarizer":
            raise WorkflowDefinitionError(
                f"fanout stage {stage.id!r} must use summarizer role"
            )
        try:
            normalized_owns = tuple(normalize_partition(path) for path in stage.owns)
        except InvalidPartition as exc:
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} has invalid ownership partition: {exc}"
            ) from exc
        if len(set(normalized_owns)) != len(normalized_owns):
            raise WorkflowDefinitionError(
                f"stage {stage.id!r} has duplicate ownership partitions"
            )

    visiting: set[str] = set()
    visited: set[str] = set()
    by_id = {stage.id: stage for stage in declared}

    def visit(stage_id: str) -> None:
        if stage_id in visiting:
            raise WorkflowDefinitionError(f"cycle detected at stage {stage_id!r}")
        if stage_id in visited:
            return
        visiting.add(stage_id)
        for dependency in by_id[stage_id].needs:
            visit(dependency)
        visiting.remove(stage_id)
        visited.add(stage_id)

    for stage_id in ids:
        visit(stage_id)
    return declared


class CheckpointStore:
    """Persist one complete workflow snapshot with atomic replacement."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def save(self, snapshot: Mapping[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(snapshot, handle, ensure_ascii=False, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, self.path)
            directory = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise

    def load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8"))


class WorkflowEngine:
    """Run ready stages in declaration order and checkpoint every boundary."""

    CHECKPOINT_VERSION = 1

    def __init__(
        self,
        stages: Sequence[Stage],
        checkpoint: CheckpointStore,
        *,
        max_worker_spawns: int = ABSOLUTE_MAX_WORKER_SPAWNS,
    ):
        if not isinstance(max_worker_spawns, int) or isinstance(max_worker_spawns, bool):
            raise ValueError("max_worker_spawns must be an integer")
        if max_worker_spawns <= 0:
            raise ValueError("max_worker_spawns must be positive")
        self.stages = _validate_stages(stages)
        self.checkpoint = checkpoint
        self.max_worker_spawns = min(max_worker_spawns, ABSOLUTE_MAX_WORKER_SPAWNS)
        self._worker_spawns = 0
        self._fallback_targets = {
            stage.on_fail for stage in self.stages
            if stage.on_fail not in (None, "human")
        }
        self._activated_fallbacks: set[str] = set()
        self._states = {stage.id: "pending" for stage in self.stages}
        self._outputs: dict[str, Any] = {}
        self._error: dict[str, str] | None = None
        self._attempts = {stage.id: 0 for stage in self.stages}
        self._attempt_errors: dict[str, list[dict[str, str]]] = {
            stage.id: [] for stage in self.stages
        }
        self._sequence = 0
        self._trace: list[dict[str, Any]] = []
        self._worktrees: dict[str, list[dict[str, Any]]] = {
            stage.id: [] for stage in self.stages
        }
        self._integration: dict[str, Any] | None = None
        self._scheduler_events: list[dict[str, Any]] = []
        self._model_routes: list[dict[str, str]] = []
        self._context_metrics: list[dict[str, Any]] = []

    def snapshot(self) -> dict[str, Any]:
        return {
            "version": self.CHECKPOINT_VERSION,
            "sequence": self._sequence,
            "workflow": [
                {
                    "id": stage.id,
                    "needs": list(stage.needs),
                    "on_fail": stage.on_fail,
                    "write": stage.write,
                    "owns": list(stage.owns),
                    "role": stage.role,
                    "fanout": stage.fanout,
                    "check": stage.check,
                }
                for stage in self.stages
            ],
            "stages": dict(self._states),
            "outputs": dict(self._outputs),
            "error": self._error,
            "attempts": dict(self._attempts),
            "attempt_errors": {
                stage_id: list(errors)
                for stage_id, errors in self._attempt_errors.items()
            },
            "engine_limits": {"max_worker_spawns": self.max_worker_spawns},
            "worker_spawns": self._worker_spawns,
            "activated_fallbacks": sorted(self._activated_fallbacks),
            "trace": [dict(event) for event in self._trace],
            "worktrees": {
                stage_id: [dict(record) for record in records]
                for stage_id, records in self._worktrees.items()
            },
            "integration": dict(self._integration) if self._integration else None,
            "scheduler_events": [dict(event) for event in self._scheduler_events],
            "model_routes": [dict(route) for route in self._model_routes],
            "context_metrics": [dict(metric) for metric in self._context_metrics],
        }

    @classmethod
    def resume(
        cls,
        stages: Sequence[Stage],
        checkpoint: CheckpointStore,
        *,
        max_worker_spawns: int = ABSOLUTE_MAX_WORKER_SPAWNS,
    ) -> "WorkflowEngine":
        """Restore durable state, retrying only a stage interrupted while running."""
        engine = cls(stages, checkpoint, max_worker_spawns=max_worker_spawns)
        try:
            saved = checkpoint.load()
        except (OSError, json.JSONDecodeError) as exc:
            raise CheckpointError(f"cannot load checkpoint: {exc}") from exc

        expected_workflow = engine.snapshot()["workflow"]
        if saved.get("version") != cls.CHECKPOINT_VERSION:
            raise CheckpointError(f"unsupported checkpoint version: {saved.get('version')!r}")
        if saved.get("workflow") != expected_workflow:
            raise CheckpointError("checkpoint workflow definition does not match")
        saved_limit = (saved.get("engine_limits") or {}).get("max_worker_spawns")
        if saved_limit != engine.max_worker_spawns:
            raise CheckpointError("checkpoint engine spawn limit does not match")
        states = saved.get("stages")
        if not isinstance(states, dict) or set(states) != set(engine._states):
            raise CheckpointError("checkpoint stage set does not match")
        invalid_states = sorted(set(states.values()) - STAGE_STATES)
        if invalid_states:
            raise CheckpointError(f"invalid checkpoint stage states: {invalid_states}")
        sequence = saved.get("sequence")
        if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
            raise CheckpointError("checkpoint sequence must be a non-negative integer")
        outputs = saved.get("outputs")
        if not isinstance(outputs, dict):
            raise CheckpointError("checkpoint outputs must be an object")
        completed = {stage_id for stage_id, state in states.items() if state == "completed"}
        if not set(outputs).issubset(completed):
            raise CheckpointError("checkpoint has output for an incomplete stage")

        engine._states = {
            stage_id: "pending" if state == "running" else state
            for stage_id, state in states.items()
        }
        engine._outputs = outputs
        engine._error = saved.get("error")
        attempts = saved.get("attempts", {stage.id: 0 for stage in engine.stages})
        attempt_errors = saved.get(
            "attempt_errors", {stage.id: [] for stage in engine.stages}
        )
        if (
            not isinstance(attempts, dict)
            or set(attempts) != set(engine._states)
            or any(
                not isinstance(value, int) or isinstance(value, bool) or value < 0
                for value in attempts.values()
            )
        ):
            raise CheckpointError("checkpoint attempts are invalid")
        if not isinstance(attempt_errors, dict) or set(attempt_errors) != set(engine._states):
            raise CheckpointError("checkpoint attempt_errors are invalid")
        engine._attempts = attempts
        engine._attempt_errors = attempt_errors
        worker_spawns = saved.get("worker_spawns", 0)
        if (
            not isinstance(worker_spawns, int)
            or isinstance(worker_spawns, bool)
            or not 0 <= worker_spawns <= engine.max_worker_spawns
        ):
            raise CheckpointError("checkpoint worker_spawns is invalid")
        engine._worker_spawns = worker_spawns
        activated = saved.get("activated_fallbacks", [])
        if (
            not isinstance(activated, list)
            or not all(isinstance(value, str) for value in activated)
            or not set(activated).issubset(engine._fallback_targets)
        ):
            raise CheckpointError("checkpoint activated_fallbacks is invalid")
        engine._activated_fallbacks = set(activated)
        trace = saved.get("trace", [])
        if (
            not isinstance(trace, list)
            or any(not isinstance(event, dict) for event in trace)
            or [event.get("sequence") for event in trace] != list(range(1, sequence + 1))
        ):
            raise CheckpointError("checkpoint trace sequence is invalid")
        engine._trace = trace
        worktrees = saved.get("worktrees", {stage.id: [] for stage in engine.stages})
        if not isinstance(worktrees, dict) or set(worktrees) != set(engine._states):
            raise CheckpointError("checkpoint worktrees are invalid")
        if any(
            not isinstance(records, list)
            or any(not isinstance(record, dict) for record in records)
            for records in worktrees.values()
        ):
            raise CheckpointError("checkpoint worktree records are invalid")
        engine._worktrees = worktrees
        integration = saved.get("integration")
        if integration is not None and not isinstance(integration, dict):
            raise CheckpointError("checkpoint integration is invalid")
        engine._integration = integration
        scheduler_events = saved.get("scheduler_events", [])
        if (
            not isinstance(scheduler_events, list)
            or any(not isinstance(event, dict) for event in scheduler_events)
        ):
            raise CheckpointError("checkpoint scheduler_events are invalid")
        engine._scheduler_events = scheduler_events
        model_routes = saved.get("model_routes", [])
        if (
            not isinstance(model_routes, list)
            or any(not isinstance(route, dict) for route in model_routes)
        ):
            raise CheckpointError("checkpoint model_routes are invalid")
        engine._model_routes = model_routes
        context_metrics = saved.get("context_metrics", [])
        if (
            not isinstance(context_metrics, list)
            or any(not isinstance(metric, dict) for metric in context_metrics)
        ):
            raise CheckpointError("checkpoint context_metrics are invalid")
        engine._context_metrics = context_metrics
        engine._sequence = sequence
        return engine

    def _save_boundary(self) -> None:
        self._sequence += 1
        self._trace.append({
            "sequence": self._sequence,
            "stages": dict(self._states),
            "attempts": dict(self._attempts),
            "worker_spawns": self._worker_spawns,
            "activated_fallbacks": sorted(self._activated_fallbacks),
            "error": dict(self._error) if self._error else None,
        })
        self.checkpoint.save(self.snapshot())

    def _persist_fanout_summaries(self, stage: Stage, results: list[Any]) -> dict:
        stage_dir = hashlib.sha256(stage.id.encode("utf-8")).hexdigest()[:16]
        output_root = self.checkpoint.path.parent / "outputs" / stage_dir
        artifacts: list[dict[str, Any]] = []
        for index, result in enumerate(results):
            if not isinstance(result, dict) or set(result) != {"summary"}:
                raise WorkerExecutionError(
                    f"fanout worker {stage.id}[{index}] must return only summary"
                )
            summary = result["summary"]
            if not isinstance(summary, str) or not summary.strip():
                raise WorkerExecutionError(
                    f"fanout worker {stage.id}[{index}] returned empty summary"
                )
            if len(summary) > MAX_SUMMARY_CHARS:
                raise WorkerExecutionError(
                    f"fanout worker {stage.id}[{index}] summary exceeds {MAX_SUMMARY_CHARS} chars"
                )
            path = output_root / f"{index}.json"
            CheckpointStore(path).save({"summary": summary})
            artifacts.append({
                "index": index,
                "path": str(path.relative_to(self.checkpoint.path.parent)),
            })
        return {"summaries": artifacts}

    def run(self, execute: Callable[[Stage], Any]) -> dict[str, Any]:
        self._save_boundary()
        while True:
            ready = next(
                (
                    stage
                    for stage in self.stages
                    if self._states[stage.id] == "pending"
                    and all(self._states[need] == "completed" for need in stage.needs)
                ),
                None,
            )
            if ready is None:
                break

            self._states[ready.id] = "running"
            self._save_boundary()
            try:
                output = execute(ready)
                # Reject an invalid output while the stage is still inside its
                # execution boundary.  Otherwise checkpoint serialization would
                # fail after marking it completed and leave disk at "running".
                json.dumps(output, ensure_ascii=False)
                self._outputs[ready.id] = output
            except BaseException as exc:
                self._states[ready.id] = "failed"
                self._error = {
                    "stage": ready.id,
                    "type": type(exc).__name__,
                    "message": str(exc),
                }
                self._save_boundary()
                raise
            self._states[ready.id] = "completed"
            self._save_boundary()

        return self.snapshot()

    def run_isolated(
        self,
        execute: Callable[[Stage, WorkerContext], Any],
        limits: ExecutionLimits | Mapping[str, ExecutionLimits],
        *,
        tool_dispatcher: Callable[[str, Mapping[str, Any]], Any] | None = None,
        workspace_manager: Any | None = None,
        ownership_table: FileOwnershipTable | None = None,
        integration_command: str | None = None,
        integration_timeout: float = 120,
        conflict_fixer: Callable[[Stage, WorkerContext], Any] | None = None,
        conflict_fixer_limits: ExecutionLimits | None = None,
        scheduler: scheduler_mod.ResourceScheduler | None = None,
        model_router: Any | None = None,
        output_contracts: Mapping[str, Any] | None = None,
        output_root: Path | None = None,
        context_token_threshold: int = DEFAULT_CONTEXT_TOKEN_THRESHOLD,
        start_method: str = "spawn",
    ) -> dict[str, Any]:
        """Run stages in killable processes with engine-brokered tool calls."""
        if (
            not isinstance(context_token_threshold, int)
            or isinstance(context_token_threshold, bool)
            or context_token_threshold < 1
        ):
            raise ValueError("context_token_threshold must be a positive integer")
        human_stages = [
            stage_id for stage_id, state in self._states.items()
            if state == "needs_human"
        ]
        if human_stages:
            raise HumanInterventionRequired(
                f"workflow is waiting for human intervention at {human_stages}"
            )
        if self._integration and self._integration.get("status") == "needs_human":
            raise HumanInterventionRequired(
                "workflow integration is waiting for human intervention"
            )
        for stage in self.stages:
            if stage.write == "worktree" and workspace_manager is None:
                raise WorkflowDefinitionError(
                    f"stage {stage.id!r} requires a workspace_manager"
                )
        if workspace_manager is not None:
            known_roots = {
                str(record.get("root_path"))
                for records in self._worktrees.values()
                for record in records
                if record.get("root_path")
            }
            if self._integration and self._integration.get("root_path"):
                known_roots.add(str(self._integration["root_path"]))
            workspace_manager.reconcile(known_roots)
            for records in self._worktrees.values():
                for record in records:
                    if record.get("status") == "prepared":
                        try:
                            cleaned = workspace_manager.fail(record)
                        except Exception as exc:
                            raise WorkerExecutionError(
                                f"cannot clean interrupted worktree: {exc}"
                            ) from exc
                        record.update(cleaned)
                        record["status"] = "interrupted_cleaned"
                    elif record.get("status") == "finalizing":
                        try:
                            recovered = workspace_manager.recover_complete(
                                record,
                                stage_id=str(record["stage_id"]),
                                attempt=int(record["attempt"]),
                            )
                        except Exception as exc:
                            raise WorkerExecutionError(
                                f"cannot recover finalizing worktree: {exc}"
                            ) from exc
                        record.update(recovered)
        ownership_table = ownership_table or FileOwnershipTable()
        scheduler = scheduler or scheduler_mod.default_scheduler()
        self._save_boundary()
        while True:
            ready = next(
                (
                    stage for stage in self.stages
                    if self._states[stage.id] == "pending"
                    and (
                        stage.id not in self._fallback_targets
                        or stage.id in self._activated_fallbacks
                    )
                    and all(
                        self._states[need] == "completed"
                        or (
                            stage.id in self._activated_fallbacks
                            and self._states[need] == "failed"
                        )
                        for need in stage.needs
                    )
                ),
                None,
            )
            if ready is None:
                unused = [
                    stage_id for stage_id in self._fallback_targets
                    if self._states[stage_id] == "pending"
                    and stage_id not in self._activated_fallbacks
                ]
                if unused:
                    for stage_id in unused:
                        self._states[stage_id] = "skipped"
                    self._save_boundary()
                break
            stage_limits = limits[ready.id] if isinstance(limits, Mapping) else limits
            last_error: WorkerExecutionError | None = None
            for _ in range(stage_limits.retries + 1):
                if self._worker_spawns + ready.fanout > self.max_worker_spawns:
                    last_error = SpawnLimitExceeded(
                        f"engine exhausted absolute worker spawn cap "
                        f"{self.max_worker_spawns}"
                    )
                    self._attempt_errors[ready.id].append({
                        "type": type(last_error).__name__, "message": str(last_error)
                    })
                    self._save_boundary()
                    continue
                self._worker_spawns += ready.fanout
                self._states[ready.id] = "running"
                self._attempts[ready.id] += 1
                self._save_boundary()
                model_config = None
                if model_router is not None:
                    try:
                        model_config = model_router.resolve(ready.role)
                    except Exception as exc:
                        last_error = WorkerExecutionError(
                            f"model routing failed: {type(exc).__name__}: {exc}"
                        )
                        self._attempt_errors[ready.id].append({
                            "type": type(last_error).__name__, "message": str(last_error)
                        })
                        self._save_boundary()
                        continue
                    for fanout_index in range(ready.fanout):
                        self._model_routes.append({
                            "stage": ready.id,
                            "role": ready.role,
                            "fanout_index": fanout_index,
                            **model_config,
                        })
                ownership_lease = None
                if ready.write == "worktree":
                    try:
                        ownership_lease = ownership_table.acquire(
                            f"{ready.id}:attempt:{self._attempts[ready.id]}",
                            ready.owns,
                        )
                    except (InvalidPartition, OwnershipConflict) as exc:
                        last_error = WorkerExecutionError(
                            f"ownership lease failed: {type(exc).__name__}: {exc}"
                        )
                        self._attempt_errors[ready.id].append({
                            "type": type(last_error).__name__, "message": str(last_error)
                        })
                        self._save_boundary()
                        continue
                workspace: dict[str, Any] | None = None
                if ready.write == "worktree":
                    try:
                        workspace = workspace_manager.provision(
                            ready.id, self._attempts[ready.id]
                        )
                    except Exception as exc:
                        last_error = WorkerExecutionError(
                            f"worktree provision failed: {type(exc).__name__}: {exc}"
                        )
                        self._attempt_errors[ready.id].append({
                            "type": type(last_error).__name__,
                            "message": str(last_error),
                        })
                        self._save_boundary()
                        if ownership_lease is not None:
                            ownership_lease.release()
                        continue
                    workspace = {
                        **workspace,
                        "status": "prepared",
                        "stage_id": ready.id,
                        "attempt": self._attempts[ready.id],
                    }
                    self._worktrees[ready.id].append(workspace)
                    self._save_boundary()
                try:
                    if ready.fanout == 1:
                        output = self._run_isolated_attempt(
                            execute, ready, stage_limits,
                            tool_dispatcher=tool_dispatcher,
                            workspace_root=(workspace or {}).get("root_path"),
                            owned_paths=ready.owns,
                            scheduler=scheduler,
                            scheduler_events=self._scheduler_events,
                            context_metrics=self._context_metrics,
                            context_token_threshold=context_token_threshold,
                            model_config=model_config,
                            fanout_index=0,
                            fanout_total=1,
                            start_method=start_method,
                        )
                    else:
                        with ThreadPoolExecutor(max_workers=ready.fanout) as pool:
                            futures = [
                                pool.submit(
                                    self._run_isolated_attempt,
                                    execute,
                                    ready,
                                    stage_limits,
                                    tool_dispatcher=tool_dispatcher,
                                    workspace_root=None,
                                    owned_paths=(),
                                    scheduler=scheduler,
                                    scheduler_events=self._scheduler_events,
                                    context_metrics=self._context_metrics,
                                    context_token_threshold=context_token_threshold,
                                    model_config=model_config,
                                    fanout_index=index,
                                    fanout_total=ready.fanout,
                                    start_method=start_method,
                                )
                                for index in range(ready.fanout)
                            ]
                            fanout_results = [future.result() for future in futures]
                        output = self._persist_fanout_summaries(
                            ready, fanout_results
                        )
                    contract = (output_contracts or {}).get(ready.id)
                    if contract is not None:
                        from .workflow_template import validate_output

                        try:
                            if ready.fanout == 1:
                                validate_output(contract, output)
                                if output_root is None:
                                    raise ValueError(
                                        "output_root is required for output contracts"
                                    )
                                CheckpointStore(Path(output_root) / contract.path).save(
                                    output
                                )
                                output = {"path": contract.path}
                            else:
                                for summary in output["summaries"]:
                                    payload = CheckpointStore(
                                        self.checkpoint.path.parent / summary["path"]
                                    ).load()
                                    validate_output(contract, payload)
                        except Exception as exc:
                            raise WorkerExecutionError(
                                f"stage {ready.id!r} output contract failed: "
                                f"{type(exc).__name__}: {exc}"
                            ) from exc
                except (WorkerExecutionError, OwnershipViolation) as exc:
                    last_error = exc
                    if workspace is not None:
                        try:
                            workspace.update(workspace_manager.fail(workspace))
                        except Exception as cleanup_exc:
                            workspace["status"] = "cleanup_failed"
                            workspace["cleanup_error"] = (
                                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                            )
                    self._attempt_errors[ready.id].append({
                        "type": type(exc).__name__, "message": str(exc)
                    })
                    self._save_boundary()
                    if ownership_lease is not None:
                        ownership_lease.release()
                    continue
                if workspace is not None:
                    workspace["status"] = "finalizing"
                    self._save_boundary()
                    try:
                        changed_paths = workspace_manager.validate_ownership(
                            workspace, ready.owns
                        )
                        workspace["changed_paths"] = changed_paths
                    except OwnershipViolation as exc:
                        try:
                            workspace.update(workspace_manager.fail(workspace))
                        except Exception as cleanup_exc:
                            workspace["status"] = "cleanup_failed"
                            workspace["cleanup_error"] = (
                                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                            )
                        last_error = exc
                        self._attempt_errors[ready.id].append({
                            "type": type(exc).__name__, "message": str(exc)
                        })
                        self._save_boundary()
                        if ownership_lease is not None:
                            ownership_lease.release()
                        continue
                    if ready.check is not None:
                        worker_verification = workspace_manager.verify(
                            workspace,
                            stage_id=ready.id,
                            attempt=self._attempts[ready.id],
                            command=ready.check,
                        )
                        workspace["worker_verification"] = worker_verification
                        if worker_verification.get("exit_code") != 0:
                            last_error = WorkerExecutionError(
                                f"stage {ready.id!r} worker check failed"
                            )
                            try:
                                workspace.update(workspace_manager.fail(workspace))
                            except Exception as cleanup_exc:
                                workspace["status"] = "cleanup_failed"
                                workspace["cleanup_error"] = (
                                    f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                                )
                            self._attempt_errors[ready.id].append({
                                "type": type(last_error).__name__,
                                "message": str(last_error),
                            })
                            self._save_boundary()
                            if ownership_lease is not None:
                                ownership_lease.release()
                            continue
                    try:
                        workspace.update(workspace_manager.complete(
                            workspace,
                            stage_id=ready.id,
                            attempt=self._attempts[ready.id],
                        ))
                    except Exception as exc:
                        try:
                            workspace.update(workspace_manager.fail(workspace))
                        except Exception as cleanup_exc:
                            workspace["status"] = "cleanup_failed"
                            workspace["cleanup_error"] = (
                                f"{type(cleanup_exc).__name__}: {cleanup_exc}"
                            )
                        last_error = WorkerExecutionError(
                            f"worktree finalize failed: {type(exc).__name__}: {exc}"
                        )
                        self._attempt_errors[ready.id].append({
                            "type": type(last_error).__name__,
                            "message": str(last_error),
                        })
                        self._save_boundary()
                        if ownership_lease is not None:
                            ownership_lease.release()
                        continue
                self._outputs[ready.id] = output
                self._states[ready.id] = "completed"
                self._error = None
                self._save_boundary()
                if ownership_lease is not None:
                    ownership_lease.release()
                break
            else:
                assert last_error is not None
                route = ready.on_fail
                if route == "human":
                    self._states[ready.id] = "needs_human"
                    self._error = {
                        "stage": ready.id,
                        "type": type(last_error).__name__,
                        "message": str(last_error),
                        "action": "human_intervention",
                    }
                    self._save_boundary()
                    raise HumanInterventionRequired(
                        f"stage {ready.id!r} requires human intervention"
                    ) from last_error
                self._states[ready.id] = "failed"
                self._error = {
                    "stage": ready.id,
                    "type": type(last_error).__name__,
                    "message": str(last_error),
                    "action": "fallback" if route else "raise",
                }
                if route:
                    self._activated_fallbacks.add(route)
                    self._save_boundary()
                    continue
                self._save_boundary()
                raise last_error
        if integration_command and self._integration is None:
            if workspace_manager is None:
                raise WorkflowDefinitionError(
                    "integration verification requires a workspace_manager"
                )
            completed_records: list[dict] = []
            for stage in self.stages:
                if stage.write != "worktree":
                    continue
                completed = next(
                    (
                        record for record in reversed(self._worktrees[stage.id])
                        if record.get("status") in {"completed", "completed_recovered"}
                    ),
                    None,
                )
                if completed is not None:
                    completed_records.append(completed)
            if not completed_records:
                raise IntegrationError("no completed write branches to integrate")
            try:
                self._integration = workspace_manager.integrate(
                    completed_records,
                    verification_command=integration_command,
                    verification_timeout=integration_timeout,
                )
            except Exception as exc:
                self._integration = {
                    "status": "integration_error",
                    "error": f"{type(exc).__name__}: {exc}",
                }
                self._save_boundary()
                raise IntegrationError(self._integration["error"]) from exc
            self._save_boundary()
        if integration_command and self._integration is not None:
            if self._integration.get("status") == "merge_conflict":
                fixer_attempts = int(self._integration.get("fixer_attempts", 0))
                if conflict_fixer is None or fixer_attempts >= 1:
                    self._integration["status"] = "needs_human"
                    self._integration["handoff_reason"] = (
                        "fixer_unavailable" if conflict_fixer is None else "fixer_limit_exhausted"
                    )
                    self._error = {
                        "stage": "integration",
                        "type": "MergeConflict",
                        "message": self._integration["handoff_reason"],
                        "action": "human_intervention",
                    }
                    self._save_boundary()
                    raise HumanInterventionRequired(
                        "merge conflict requires human intervention"
                    )
                if self._worker_spawns >= self.max_worker_spawns:
                    self._integration["status"] = "needs_human"
                    self._integration["handoff_reason"] = "worker_spawn_limit"
                    self._save_boundary()
                    raise HumanInterventionRequired(
                        "merge fixer cannot start because worker spawn limit is exhausted"
                    )
                conflicts = tuple(
                    str(path) for path in self._integration.get("conflict_paths") or ()
                )
                if not conflicts:
                    self._integration["status"] = "needs_human"
                    self._integration["handoff_reason"] = "missing_conflict_paths"
                    self._save_boundary()
                    raise HumanInterventionRequired(
                        "merge conflict paths are unavailable"
                    )
                effective_fixer_limits = conflict_fixer_limits or ExecutionLimits(
                    timeout_seconds=180,
                    max_tool_calls=8,
                    retries=0,
                )
                effective_fixer_limits = ExecutionLimits(
                    timeout_seconds=effective_fixer_limits.timeout_seconds,
                    max_tool_calls=effective_fixer_limits.max_tool_calls,
                    retries=0,
                )
                self._worker_spawns += 1
                self._integration["fixer_attempts"] = 1
                self._save_boundary()
                lease = None
                try:
                    lease = ownership_table.acquire("merge-fixer", conflicts)
                    before = workspace_manager.working_fingerprints(
                        self._integration["root_path"]
                    )
                    fixer_output = self._run_isolated_attempt(
                        conflict_fixer,
                        Stage("merge_fixer"),
                        effective_fixer_limits,
                        tool_dispatcher=tool_dispatcher,
                        workspace_root=self._integration["root_path"],
                        owned_paths=conflicts,
                        scheduler=scheduler,
                        scheduler_events=self._scheduler_events,
                        context_metrics=self._context_metrics,
                        context_token_threshold=context_token_threshold,
                        model_config=(
                            model_router.resolve("coder")
                            if model_router is not None else None
                        ),
                        fanout_index=0,
                        fanout_total=1,
                        start_method=start_method,
                    )
                    after = workspace_manager.working_fingerprints(
                        self._integration["root_path"]
                    )
                    changed = workspace_manager.validate_fixer_changes(
                        before, after, conflicts
                    )
                    self._integration["fixer_output"] = fixer_output
                    self._integration["fixer_changed_paths"] = changed
                    self._integration = workspace_manager.continue_after_fix(
                        self._integration,
                        verification_command=integration_command,
                        verification_timeout=integration_timeout,
                    )
                except Exception as exc:
                    self._integration["status"] = "needs_human"
                    self._integration["handoff_reason"] = (
                        f"fixer_failed:{type(exc).__name__}:{exc}"
                    )
                    self._error = {
                        "stage": "integration",
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "action": "human_intervention",
                    }
                    self._save_boundary()
                    raise HumanInterventionRequired(
                        "merge fixer failed; human intervention required"
                    ) from exc
                finally:
                    if lease is not None:
                        lease.release()
                self._save_boundary()
                if self._integration.get("status") != "verified":
                    previous = self._integration.get("status")
                    self._integration["status"] = "needs_human"
                    self._integration["handoff_reason"] = f"after_fixer:{previous}"
                    self._save_boundary()
                    raise HumanInterventionRequired(
                        "merge fixer limit exhausted; human intervention required"
                    )
            elif self._integration.get("status") != "verified":
                raise IntegrationError(
                    f"integration stopped at {self._integration.get('status')}"
                )
        return self.snapshot()

    @staticmethod
    def _run_isolated_attempt(
        execute,
        stage: Stage,
        limits: ExecutionLimits,
        *,
        tool_dispatcher: Callable[[str, Mapping[str, Any]], Any] | None,
        workspace_root: str | None,
        owned_paths: tuple[str, ...],
        scheduler: scheduler_mod.ResourceScheduler,
        scheduler_events: list[dict[str, Any]],
        context_metrics: list[dict[str, Any]],
        context_token_threshold: int,
        model_config: dict[str, str] | None,
        fanout_index: int,
        fanout_total: int,
        start_method: str,
    ) -> Any:
        process_context = multiprocessing.get_context(start_method)
        parent, child = process_context.Pipe(duplex=True)
        process = process_context.Process(
            target=_isolated_worker_entry,
            args=(
                execute, stage, child, tool_dispatcher, workspace_root, model_config,
                fanout_index, fanout_total,
            ),
            daemon=True,
        )
        process.start()
        child.close()
        deadline = time.monotonic() + limits.timeout_seconds
        tool_calls = 0
        model_lease = None
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise WorkerTimeout(
                        f"stage {stage.id!r} exceeded {limits.timeout_seconds:g}s"
                    )
                if parent.poll(min(remaining, 0.05)):
                    message = parent.recv()
                    kind = message.get("kind")
                    if kind == "tool_call":
                        tool_calls += 1
                        if tool_calls > limits.max_tool_calls:
                            raise ToolCallLimitExceeded(
                                f"stage {stage.id!r} exceeded "
                                f"{limits.max_tool_calls} tool calls"
                            )
                        tool_name = str(message.get("name") or "")
                        if tool_name in {"write_file", "edit_file"}:
                            arguments = message.get("arguments") or {}
                            path = str(arguments.get("path") or "")
                            if not path or not owns_path(owned_paths, path):
                                raise OwnershipViolation(
                                    f"tool {tool_name!r} attempted path outside partition: {path!r}"
                                )
                        if tool_dispatcher is None:
                            parent.send({"kind": "tool_error", "error": "tools disabled"})
                        else:
                            parent.send({"kind": "tool_allowed"})
                    elif kind == "token_usage":
                        input_tokens = message.get("input_tokens")
                        output_tokens = message.get("output_tokens")
                        if any(
                            not isinstance(value, int)
                            or isinstance(value, bool)
                            or value < 0
                            for value in (input_tokens, output_tokens)
                        ):
                            parent.send({
                                "kind": "token_usage_error",
                                "error": "invalid token usage",
                            })
                            continue
                        total = input_tokens + output_tokens
                        context_metrics.append({
                            "stage": stage.id,
                            "fanout_index": fanout_index,
                            "input_tokens": input_tokens,
                            "output_tokens": output_tokens,
                            "total_tokens": total,
                            "threshold": context_token_threshold,
                            "compression_required": total >= context_token_threshold,
                            "compressed": False,
                            "checkpoint": "stage_boundary",
                        })
                        parent.send({
                            "kind": "token_usage_recorded",
                            "compression_required": total >= context_token_threshold,
                        })
                    elif kind == "context_checkpoint":
                        summary = str(message.get("summary") or "").strip()
                        metric = next((
                            item for item in reversed(context_metrics)
                            if item.get("stage") == stage.id
                            and item.get("fanout_index") == fanout_index
                            and item.get("compression_required")
                            and not item.get("compressed")
                        ), None)
                        if metric is None or not summary or len(summary) > MAX_SUMMARY_CHARS:
                            parent.send({
                                "kind": "context_checkpoint_error",
                                "error": "no pending compression or invalid summary",
                            })
                            continue
                        metric["compressed"] = True
                        metric["summary"] = summary
                        parent.send({"kind": "context_checkpointed"})
                    elif kind == "model_slot_acquire":
                        if model_lease is not None:
                            parent.send({
                                "kind": "model_slot_error",
                                "error": "worker already holds a model slot",
                            })
                            continue
                        queued_at = time.monotonic()
                        scheduler_events.append({
                            "kind": "queue_enter",
                            "resource": "model_generation",
                            "stage": stage.id,
                            "fanout_index": fanout_index,
                            "at": queued_at,
                        })
                        remaining_for_queue = max(0.001, deadline - time.monotonic())
                        try:
                            model_lease = scheduler.acquire(
                                scheduler_mod.ResourceClass.MODEL_GENERATION,
                                owner_id=stage.id,
                                timeout=remaining_for_queue,
                                on_wait=lambda wait: scheduler_events.append({
                                    "kind": "queue_wait",
                                    "stage": stage.id,
                                    "fanout_index": fanout_index,
                                    "at": time.monotonic(),
                                    **wait,
                                }),
                            )
                        except Exception as exc:
                            parent.send({
                                "kind": "model_slot_error",
                                "error": f"{type(exc).__name__}: {exc}",
                            })
                        else:
                            acquired_at = time.monotonic()
                            scheduler_events.append({
                                "kind": "lease_acquired",
                                "resource": "model_generation",
                                "stage": stage.id,
                                "fanout_index": fanout_index,
                                "lease_id": model_lease.id,
                                "at": acquired_at,
                                "queue_wait_ms": round(
                                    (acquired_at - queued_at) * 1000, 3
                                ),
                            })
                            parent.send({
                                "kind": "model_slot_acquired",
                                "lease_id": model_lease.id,
                            })
                    elif kind == "model_slot_release":
                        if model_lease is not None:
                            lease_id = model_lease.id
                            model_lease.release()
                            model_lease = None
                            scheduler_events.append({
                                "kind": "lease_released",
                                "resource": "model_generation",
                                "stage": stage.id,
                                "fanout_index": fanout_index,
                                "lease_id": lease_id,
                                "at": time.monotonic(),
                            })
                        parent.send({"kind": "model_slot_released"})
                    elif kind == "result":
                        pending_compression = any(
                            item.get("stage") == stage.id
                            and item.get("fanout_index") == fanout_index
                            and item.get("compression_required")
                            and not item.get("compressed")
                            for item in context_metrics
                        )
                        if pending_compression:
                            raise WorkerExecutionError(
                                f"stage {stage.id!r} exceeded context threshold "
                                "without a compressed checkpoint"
                            )
                        return message.get("result")
                    elif kind == "error":
                        raise WorkerExecutionError(
                            f"{message.get('type')}: {message.get('message')}"
                        )
                elif not process.is_alive():
                    raise WorkerExecutionError(
                        f"stage {stage.id!r} worker exited with code {process.exitcode}"
                    )
        finally:
            if model_lease is not None:
                lease_id = model_lease.id
                model_lease.release()
                scheduler_events.append({
                    "kind": "lease_released",
                    "resource": "model_generation",
                    "stage": stage.id,
                    "fanout_index": fanout_index,
                    "lease_id": lease_id,
                    "at": time.monotonic(),
                    "reason": "worker_exit",
                })
            parent.close()
            if process.is_alive():
                process.terminate()
                process.join(2)
            if process.is_alive():
                process.kill()
            process.join(2)
