"""Deterministic workflow core tests."""

from __future__ import annotations

import json
import time
import threading
import os
from pathlib import Path

import pytest

from janus_server.workflow import (
    CheckpointError,
    CheckpointStore,
    ExecutionLimits,
    ABSOLUTE_MAX_WORKER_SPAWNS,
    Stage,
    WorkflowDefinitionError,
    WorkflowEngine,
    ToolCallLimitExceeded,
    SpawnLimitExceeded,
    HumanInterventionRequired,
    WorkerExecutionError,
    WorkerTimeout,
)
from janus_server.scheduler import ResourceClass, ResourceScheduler
from janus_server.model_router import ModelRouter


class SimulatedCrash(BaseException):
    pass


def sleeping_isolated_worker(_stage, context):
    time.sleep(0.2)
    return context.call_tool("late", {})


def excessive_tool_worker(_stage, context):
    context.call_tool("first", {})
    return context.call_tool("second", {})


def retrying_tool_worker(_stage, context):
    return context.call_tool("eventually", {})


def successful_isolated_worker(stage, _context):
    return stage.id


def slotted_worker(stage, context):
    with context.model_slot():
        started = time.monotonic()
        time.sleep(0.2)
        finished = time.monotonic()
    return {"stage": stage.id, "started": started, "finished": finished}


def failing_slotted_worker(_stage, context):
    with context.model_slot():
        raise RuntimeError("generation failed")


def routed_worker(stage, context):
    return {
        "stage": stage.id,
        "role": stage.role,
        "model_key": context.model_key,
        "provider": context.model["provider"],
    }


def summary_fanout_worker(_stage, context):
    with context.model_slot():
        time.sleep(0.1)
    return {
        "summary": (
            f"summary index={context.fanout_index} total={context.fanout_total} "
            f"pid={os.getpid()}"
        )
    }


def leaky_fanout_worker(_stage, context):
    return {"summary": f"summary {context.fanout_index}", "raw_context": "must drop"}


def token_recording_worker(_stage, context):
    if context.record_tokens(7_000, 1_500):
        context.checkpoint_context("compressed facts only")
    return {"summary": "compressed context checkpoint"}


def uncompressed_over_budget_worker(_stage, context):
    context.record_tokens(7_000, 1_500)
    return {"summary": "must fail"}


def primary_fails_fallback_succeeds(stage, _context):
    if stage.id == "primary":
        raise RuntimeError("primary exhausted")
    return f"handled:{stage.id}"


def hanging_tool_worker(_stage, context):
    return context.call_tool("hang", {})


class RecordingDispatcher:
    def __init__(self, path: str):
        self.path = path

    def __call__(self, name, _args):
        path = Path(self.path)
        existing = path.read_text(encoding="utf-8") if path.exists() else ""
        path.write_text(existing + name + "\n", encoding="utf-8")
        return name


class FailOnceDispatcher(RecordingDispatcher):
    def __call__(self, name, args):
        path = Path(self.path)
        first = not path.exists()
        result = super().__call__(name, args)
        if first:
            raise RuntimeError("transient")
        return "recovered" if result else result


class SlowDispatcher(RecordingDispatcher):
    def __call__(self, name, args):
        time.sleep(0.2)
        return super().__call__(name, args)


def test_needs_dag_runs_in_stable_declaration_order_and_checkpoints_boundaries(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "run" / "checkpoint.json")
    writes: list[dict] = []
    original_save = checkpoint.save

    def record(snapshot):
        writes.append(json.loads(json.dumps(snapshot)))
        original_save(snapshot)

    checkpoint.save = record
    engine = WorkflowEngine(
        [
            Stage("explore_b"),
            Stage("explore_a"),
            Stage("plan", ("explore_a", "explore_b")),
            Stage("implement", ("plan",)),
        ],
        checkpoint,
    )
    order: list[str] = []

    result = engine.run(lambda stage: order.append(stage.id) or {"id": stage.id})

    assert order == ["explore_b", "explore_a", "plan", "implement"]
    assert set(result["stages"].values()) == {"completed"}
    assert result == checkpoint.load()
    assert len(writes) == 1 + 2 * len(order)
    assert writes[0]["stages"]["explore_b"] == "pending"
    assert writes[1]["stages"]["explore_b"] == "running"
    assert writes[2]["stages"]["explore_b"] == "completed"


def test_failure_is_checkpointed_and_dependants_do_not_run(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    engine = WorkflowEngine(
        [Stage("explore"), Stage("plan", ("explore",))], checkpoint
    )

    with pytest.raises(RuntimeError, match="local model failed"):
        engine.run(lambda _stage: (_ for _ in ()).throw(RuntimeError("local model failed")))

    saved = checkpoint.load()
    assert saved["stages"] == {"explore": "failed", "plan": "pending"}
    assert saved["error"] == {
        "stage": "explore",
        "type": "RuntimeError",
        "message": "local model failed",
    }


def test_non_json_output_is_a_checkpointed_stage_failure(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    engine = WorkflowEngine([Stage("explore")], checkpoint)

    with pytest.raises(TypeError):
        engine.run(lambda _stage: object())

    saved = checkpoint.load()
    assert saved["stages"] == {"explore": "failed"}
    assert saved["error"]["stage"] == "explore"
    assert saved["error"]["type"] == "TypeError"


def test_resume_retries_running_stage_without_reexecuting_completed_stages(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    original_save = checkpoint.save

    def crash_after_running_boundary(snapshot):
        original_save(snapshot)
        if snapshot["stages"] == {"explore": "completed", "plan": "running"}:
            raise SimulatedCrash

    checkpoint.save = crash_after_running_boundary
    engine = WorkflowEngine(
        [Stage("explore"), Stage("plan", ("explore",))], checkpoint
    )
    first_calls: list[str] = []
    with pytest.raises(SimulatedCrash):
        engine.run(lambda stage: first_calls.append(stage.id) or stage.id)
    assert first_calls == ["explore"]

    resumed = WorkflowEngine.resume(
        [Stage("explore"), Stage("plan", ("explore",))],
        CheckpointStore(checkpoint.path),
    )
    resumed_calls: list[str] = []
    result = resumed.run(lambda stage: resumed_calls.append(stage.id) or stage.id)

    assert resumed_calls == ["plan"]
    assert result["stages"] == {"explore": "completed", "plan": "completed"}
    assert result["outputs"] == {"explore": "explore", "plan": "plan"}


def test_resume_rejects_checkpoint_from_changed_workflow(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    WorkflowEngine([Stage("explore")], checkpoint).run(lambda stage: stage.id)

    with pytest.raises(CheckpointError, match="definition does not match"):
        WorkflowEngine.resume(
            [Stage("explore"), Stage("plan", ("explore",))], checkpoint
        )


def test_isolated_timeout_terminates_worker_before_late_tool_call(tmp_path):
    calls = tmp_path / "calls.txt"
    engine = WorkflowEngine(
        [Stage("slow")], CheckpointStore(tmp_path / "checkpoint.json")
    )

    with pytest.raises(WorkerTimeout):
        engine.run_isolated(
            sleeping_isolated_worker,
            ExecutionLimits(timeout_seconds=0.05, max_tool_calls=1, retries=0),
            tool_dispatcher=RecordingDispatcher(str(calls)),
        )
    time.sleep(0.25)

    assert not calls.exists()
    assert engine.snapshot()["stages"] == {"slow": "failed"}


def test_timeout_terminates_tool_running_inside_worker_process(tmp_path):
    calls = tmp_path / "calls.txt"
    engine = WorkflowEngine(
        [Stage("hung_tool")], CheckpointStore(tmp_path / "checkpoint.json")
    )

    with pytest.raises(WorkerTimeout):
        engine.run_isolated(
            hanging_tool_worker,
            ExecutionLimits(timeout_seconds=0.05, max_tool_calls=1, retries=0),
            tool_dispatcher=SlowDispatcher(str(calls)),
        )
    time.sleep(0.25)

    assert not calls.exists()


def test_engine_terminates_worker_when_tool_call_cap_is_exceeded(tmp_path):
    calls = tmp_path / "calls.txt"
    engine = WorkflowEngine(
        [Stage("tools")], CheckpointStore(tmp_path / "checkpoint.json")
    )

    with pytest.raises(ToolCallLimitExceeded):
        engine.run_isolated(
            excessive_tool_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=1, retries=0),
            tool_dispatcher=RecordingDispatcher(str(calls)),
        )

    assert calls.read_text(encoding="utf-8").splitlines() == ["first"]
    assert engine.snapshot()["attempts"] == {"tools": 1}


def test_engine_retries_failed_worker_only_up_to_declared_limit(tmp_path):
    calls = tmp_path / "calls.txt"
    engine = WorkflowEngine(
        [Stage("retry")], CheckpointStore(tmp_path / "checkpoint.json")
    )
    result = engine.run_isolated(
        retrying_tool_worker,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=1, retries=1),
        tool_dispatcher=FailOnceDispatcher(str(calls)),
    )

    assert calls.read_text(encoding="utf-8").splitlines() == ["eventually", "eventually"]
    assert result["attempts"] == {"retry": 2}
    assert result["outputs"] == {"retry": "recovered"}
    assert result["attempt_errors"]["retry"] == [
        {"type": "WorkerExecutionError", "message": "RuntimeError: transient"}
    ]


def test_absolute_spawn_cap_overrides_larger_requested_value_and_survives_resume(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    engine = WorkflowEngine(
        [Stage("first"), Stage("second", ("first",))],
        checkpoint,
        max_worker_spawns=ABSOLUTE_MAX_WORKER_SPAWNS + 100,
    )
    assert engine.max_worker_spawns == ABSOLUTE_MAX_WORKER_SPAWNS

    constrained = WorkflowEngine(
        [Stage("first"), Stage("second", ("first",))],
        checkpoint,
        max_worker_spawns=1,
    )
    with pytest.raises(SpawnLimitExceeded):
        constrained.run_isolated(
            successful_isolated_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        )
    saved = checkpoint.load()
    assert saved["worker_spawns"] == 1
    assert saved["stages"] == {"first": "completed", "second": "failed"}

    resumed = WorkflowEngine.resume(
        [Stage("first"), Stage("second", ("first",))],
        checkpoint,
        max_worker_spawns=1,
    )
    assert resumed.snapshot()["worker_spawns"] == 1


def test_retry_exhaustion_activates_only_declared_fallback_stage(tmp_path):
    engine = WorkflowEngine(
        [
            Stage("primary", on_fail="fallback"),
            Stage("fallback", ("primary",), on_fail="human"),
        ],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )
    result = engine.run_isolated(
        primary_fails_fallback_succeeds,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=1),
    )

    assert result["stages"] == {"primary": "failed", "fallback": "completed"}
    assert result["attempts"] == {"primary": 2, "fallback": 1}
    assert result["outputs"] == {"fallback": "handled:fallback"}
    assert result["activated_fallbacks"] == ["fallback"]


def test_successful_primary_marks_unused_fallback_skipped(tmp_path):
    engine = WorkflowEngine(
        [Stage("primary", on_fail="fallback"), Stage("fallback")],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )
    result = engine.run_isolated(
        successful_isolated_worker,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
    )

    assert result["stages"] == {"primary": "completed", "fallback": "skipped"}
    assert result["attempts"] == {"primary": 1, "fallback": 0}


def test_human_failure_route_stops_and_survives_resume(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    stages = [Stage("primary", on_fail="human")]
    engine = WorkflowEngine(stages, checkpoint)

    with pytest.raises(HumanInterventionRequired):
        engine.run_isolated(
            primary_fails_fallback_succeeds,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        )
    saved = checkpoint.load()
    assert saved["stages"] == {"primary": "needs_human"}
    assert saved["error"]["action"] == "human_intervention"

    resumed = WorkflowEngine.resume(stages, checkpoint)
    with pytest.raises(HumanInterventionRequired, match="waiting for human"):
        resumed.run_isolated(
            successful_isolated_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        )
    assert resumed.snapshot()["attempts"] == {"primary": 1}


def test_final_trace_reconstructs_every_checkpoint_boundary(tmp_path):
    engine = WorkflowEngine(
        [Stage("explore"), Stage("plan", ("explore",))],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )
    result = engine.run_isolated(
        successful_isolated_worker,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
    )

    trace = result["trace"]
    assert [event["sequence"] for event in trace] == list(
        range(1, result["sequence"] + 1)
    )
    assert [event["stages"] for event in trace] == [
        {"explore": "pending", "plan": "pending"},
        {"explore": "running", "plan": "pending"},
        {"explore": "completed", "plan": "pending"},
        {"explore": "completed", "plan": "running"},
        {"explore": "completed", "plan": "completed"},
    ]
    assert trace[-1]["stages"] == result["stages"]
    assert trace[-1]["attempts"] == result["attempts"]
    assert trace[-1]["worker_spawns"] == result["worker_spawns"]


def test_more_logical_workers_than_model_slots_are_queued(tmp_path):
    scheduler = ResourceScheduler({ResourceClass.MODEL_GENERATION: 1})
    barrier = threading.Barrier(3)
    results: list[dict] = []
    errors: list[BaseException] = []

    def run_worker(index: int) -> None:
        engine = WorkflowEngine(
            [Stage(f"worker_{index}")],
            CheckpointStore(tmp_path / f"checkpoint-{index}.json"),
        )
        barrier.wait()
        try:
            results.append(engine.run_isolated(
                slotted_worker,
                ExecutionLimits(timeout_seconds=3, max_tool_calls=0, retries=0),
                scheduler=scheduler,
            ))
        except BaseException as exc:
            errors.append(exc)

    workers = [threading.Thread(target=run_worker, args=(index,)) for index in (1, 2)]
    for worker in workers:
        worker.start()
    barrier.wait()
    for worker in workers:
        worker.join(5)

    assert errors == []
    intervals = sorted(
        (next(iter(result["outputs"].values()))["started"],
         next(iter(result["outputs"].values()))["finished"])
        for result in results
    )
    assert intervals[0][1] <= intervals[1][0]
    events = [event for result in results for event in result["scheduler_events"]]
    assert len([event for event in events if event["kind"] == "queue_enter"]) == 2
    assert len([event for event in events if event["kind"] == "lease_acquired"]) == 2
    assert any(event["kind"] == "queue_wait" for event in events)
    assert scheduler.snapshot()["active_leases"] == 0


def test_model_slot_is_released_when_isolated_worker_fails(tmp_path):
    scheduler = ResourceScheduler({ResourceClass.MODEL_GENERATION: 1})
    engine = WorkflowEngine(
        [Stage("failing")], CheckpointStore(tmp_path / "checkpoint.json")
    )

    with pytest.raises(WorkerExecutionError, match="generation failed"):
        engine.run_isolated(
            failing_slotted_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
            scheduler=scheduler,
        )

    assert scheduler.snapshot()["active_leases"] == 0


def test_stage_roles_are_routed_by_engine_model_router(tmp_path):
    router = ModelRouter({
        "version": 1,
        "models": {
            "code": {"key": "code-model", "provider": "mlx-local"},
            "review": {"key": "review-model", "provider": "mlx-local"},
            "summary": {"key": "summary-model", "provider": "mlx-local"},
        },
        "roles": {
            "coder": "code",
            "reviewer": "review",
            "summarizer": "summary",
        },
    })
    engine = WorkflowEngine(
        [
            Stage("implement", role="coder"),
            Stage("review", ("implement",), role="reviewer"),
            Stage("summarize", ("review",), role="summarizer"),
        ],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    result = engine.run_isolated(
        routed_worker,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        model_router=router,
    )

    assert [result["outputs"][stage]["model_key"] for stage in (
        "implement", "review", "summarize"
    )] == ["code-model", "review-model", "summary-model"]
    assert [(route["stage"], route["role"], route["alias"]) for route in result["model_routes"]] == [
        ("implement", "coder", "code"),
        ("review", "reviewer", "review"),
        ("summarize", "summarizer", "summary"),
    ]


def test_read_fanout_runs_in_isolated_processes_and_keeps_only_summary_files(tmp_path):
    router = ModelRouter({
        "version": 1,
        "models": {"local": {"key": "summary-model", "provider": "mlx-local"}},
        "roles": {"coder": "local", "reviewer": "local", "summarizer": "local"},
    })
    scheduler = ResourceScheduler({ResourceClass.MODEL_GENERATION: 1})
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    engine = WorkflowEngine(
        [Stage("explore", role="summarizer", fanout=3)], checkpoint
    )

    result = engine.run_isolated(
        summary_fanout_worker,
        ExecutionLimits(timeout_seconds=3, max_tool_calls=0, retries=0),
        scheduler=scheduler,
        model_router=router,
    )

    artifacts = result["outputs"]["explore"]["summaries"]
    assert [artifact["index"] for artifact in artifacts] == [0, 1, 2]
    summaries = [
        CheckpointStore(tmp_path / artifact["path"]).load()["summary"]
        for artifact in artifacts
    ]
    assert [f"index={index}" in summary for index, summary in enumerate(summaries)] == [
        True, True, True
    ]
    assert len({summary.split("pid=")[-1] for summary in summaries}) == 3
    assert "raw_context" not in json.dumps(result)
    assert result["worker_spawns"] == 3
    assert any(event["kind"] == "queue_wait" for event in result["scheduler_events"])
    assert scheduler.snapshot()["active_leases"] == 0


def test_fanout_rejects_output_that_contains_worker_context(tmp_path):
    engine = WorkflowEngine(
        [Stage("explore", role="summarizer", fanout=2)],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    with pytest.raises(WorkerExecutionError, match="must return only summary"):
        engine.run_isolated(
            leaky_fanout_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        )


def test_fanout_static_limits_reject_write_workers_and_more_than_three(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    with pytest.raises(WorkflowDefinitionError, match="1..3"):
        WorkflowEngine([Stage("too_many", role="summarizer", fanout=4)], checkpoint)
    with pytest.raises(WorkflowDefinitionError, match="read-only"):
        WorkflowEngine([
            Stage(
                "writers", role="summarizer", fanout=2,
                write="worktree", owns=("src/",),
            )
        ], checkpoint)


@pytest.mark.parametrize(
    "stages, message",
    [
        ([Stage("a"), Stage("a")], "duplicate"),
        ([Stage("a", ("missing",))], "unknown"),
        ([Stage("a", ("b",)), Stage("b", ("a",))], "cycle"),
    ],
)
def test_invalid_dags_are_rejected_before_checkpoint_creation(tmp_path, stages, message):
    path = tmp_path / "checkpoint.json"
    with pytest.raises(WorkflowDefinitionError, match=message):
        WorkflowEngine(stages, CheckpointStore(path))
    assert not path.exists()


def test_worker_token_usage_is_checkpointed_and_marks_threshold_compression(tmp_path):
    checkpoint = CheckpointStore(tmp_path / "checkpoint.json")
    stages = [Stage("summarize", role="summarizer")]
    engine = WorkflowEngine(stages, checkpoint)
    result = engine.run_isolated(
        token_recording_worker,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        context_token_threshold=8_000,
    )
    assert result["context_metrics"] == [{
        "stage": "summarize",
        "fanout_index": 0,
        "input_tokens": 7_000,
        "output_tokens": 1_500,
        "total_tokens": 8_500,
        "threshold": 8_000,
        "compression_required": True,
        "compressed": True,
        "checkpoint": "stage_boundary",
        "summary": "compressed facts only",
    }]
    assert WorkflowEngine.resume(stages, checkpoint).snapshot()["context_metrics"] == result["context_metrics"]


def test_over_budget_worker_cannot_finish_without_compressed_context_checkpoint(tmp_path):
    engine = WorkflowEngine(
        [Stage("summarize", role="summarizer")],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )
    with pytest.raises(WorkerExecutionError, match="without a compressed checkpoint"):
        engine.run_isolated(
            uncompressed_over_budget_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
            context_token_threshold=8_000,
        )
