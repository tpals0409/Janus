#!/usr/bin/env python3
"""Run the deterministic workflow core against the real local 27B model."""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from janus_server import runtime  # noqa: E402
from janus_server.workflow import (  # noqa: E402
    CheckpointStore,
    ExecutionLimits,
    Stage,
    WorkflowEngine,
)
from p0_smoke_27b import ModelServer  # noqa: E402
from janus_server.workflow_workspace import WriteWorkspaceManager  # noqa: E402
from janus_server.model_router import ModelRouter  # noqa: E402
from janus_server.scheduler import assess_vram_sizing  # noqa: E402
from janus_server.workflow_template import WorkflowTemplate  # noqa: E402
from janus_server.airgap import assert_local_artifacts, local_network_only  # noqa: E402
from janus_server.pipeline import (  # noqa: E402
    PlanSpec,
    ReviewFeedback,
    ReviewLoop,
    ReviewPacket,
)


class SimulatedCrash(BaseException):
    """Represent process loss immediately after a durable running boundary."""


class CrashOnceCheckpointStore(CheckpointStore):
    def __init__(self, path: Path):
        super().__init__(path)
        self.crashed = False

    def save(self, snapshot) -> None:
        super().save(snapshot)
        if (
            not self.crashed
            and snapshot["stages"].get("explore") == "completed"
            and snapshot["stages"].get("plan") == "running"
            and all(
                state == "pending"
                for stage, state in snapshot["stages"].items()
                if stage not in {"explore", "plan"}
            )
        ):
            self.crashed = True
            raise SimulatedCrash


def record_response_usage(context, response) -> None:
    usage = response.usage
    context.record_tokens(
        int(getattr(usage, "prompt_tokens", 0) or 0),
        int(getattr(usage, "completion_tokens", 0) or 0),
    )


class LocalModelStageExecutor:
    """Pickle-safe executor instantiated independently inside each worker."""

    def __init__(self, checkpoint_path: Path, request_timeout: float):
        self.checkpoint_path = checkpoint_path
        self.request_timeout = request_timeout

    def __call__(self, stage: Stage, context) -> dict:
        prior = ""
        if stage.id in {"plan", "review"}:
            saved = CheckpointStore(self.checkpoint_path).load()
            if stage.id == "plan":
                artifacts = (
                    saved.get("outputs", {}).get("explore", {}).get("summaries", [])
                )
                prior = "\n".join(
                    CheckpointStore(
                        self.checkpoint_path.parent / artifact["path"]
                    ).load()["summary"]
                    for artifact in artifacts
                )
            else:
                plan_ref = saved.get("outputs", {}).get("plan", {}).get("spec_path", "")
                prior = (self.checkpoint_path.parent / plan_ref).read_text(encoding="utf-8")
        explore_marker = f"EXPLORE_{context.fanout_index}_OK"
        prompts = {
            "explore": (
                "You are the explore stage of a local deterministic workflow. "
                f"You are worker {context.fanout_index} of {context.fanout_total}. "
                f"Reply with one short sentence containing {explore_marker}."
            ),
            "plan": (
                "You are the plan stage. The completed explore output is:\n"
                f"{prior}\nThe explore work is already complete; do not create tasks for it. "
                "Return exactly one implementation task by copying this JSON exactly, "
                "with no Markdown and no additional tasks or fields: "
                '{"tasks":[{"id":"implementation","purpose":"PLAN_OK implement the '
                'workflow marker","output_format":"working text file plus passing test",'
                '"allowed_tools":["read","edit","test"],"boundaries":['
                '"only edit the owned file","do not call the network"],'
                '"owns":["workflow-e2e.txt"],'
                '"check":"test \\\"$(cat workflow-e2e.txt)\\\" = WORKTREE_OK"}]}'
            ),
            "review": (
                "You are the review stage. The completed plan output is:\n"
                f"{prior}\nReply with one short sentence containing REVIEW_OK."
            ),
        }
        client = runtime.make_client().with_options(timeout=self.request_timeout)
        started = time.monotonic()
        response_options = (
            {"response_format": {"type": "json_object"}}
            if stage.id == "plan"
            else {}
        )
        with context.model_slot():
            response = client.chat.completions.create(
                model=runtime.resolve_local_model(context.model_key),
                messages=[{"role": "user", "content": prompts[stage.id]}],
                max_tokens=512 if stage.id == "plan" else 96,
                extra_body={"enable_thinking": False},
                **response_options,
            )
        record_response_usage(context, response)
        text = str(response.choices[0].message.content or "").strip()
        marker = explore_marker if stage.id == "explore" else f"{stage.id.upper()}_OK"
        if marker not in text:
            raise RuntimeError(f"stage {stage.id!r} omitted required marker {marker!r}")
        if stage.id == "explore":
            return {"summary": text}
        if stage.id == "plan":
            try:
                plan_payload = json.loads(text)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid plan JSON: {text!r}") from exc
            plan = PlanSpec.from_dict(plan_payload)
            plan_path = self.checkpoint_path.parent / "plan-spec.json"
            plan.save(plan_path)
            return {"spec_path": plan_path.name}
        return {
            "text": text,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }


class LocalModelFallbackExecutor:
    """Exercise an exhausted primary attempt and a real-model fallback."""

    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout

    def __call__(self, stage: Stage, context) -> dict:
        marker = "PRIMARY_MARKER_THAT_MUST_NOT_APPEAR" if stage.id == "primary" else "FALLBACK_OK"
        prompt = (
            "Reply with exactly ORDINARY_PRIMARY_RESPONSE."
            if stage.id == "primary"
            else "You are a fallback stage. Reply with exactly FALLBACK_OK."
        )
        client = runtime.make_client().with_options(timeout=self.request_timeout)
        with context.model_slot():
            response = client.chat.completions.create(
                model=runtime.resolve_local_model(context.model_key),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=96,
                extra_body={"enable_thinking": False},
            )
        record_response_usage(context, response)
        text = str(response.choices[0].message.content or "").strip()
        if marker not in text:
            raise RuntimeError(f"stage {stage.id!r} omitted required marker {marker!r}")
        return {"text": text}


class LocalModelWriteExecutor:
    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout

    def __call__(self, _stage: Stage, context) -> dict:
        client = runtime.make_client().with_options(timeout=self.request_timeout)
        with context.model_slot():
            response = client.chat.completions.create(
                model=runtime.resolve_local_model(context.model_key),
                messages=[{
                    "role": "user",
                    "content": "Reply with exactly WORKTREE_OK.",
                }],
                max_tokens=96,
                extra_body={"enable_thinking": False},
            )
        record_response_usage(context, response)
        text = str(response.choices[0].message.content or "").strip()
        if "WORKTREE_OK" not in text:
            raise RuntimeError("write stage omitted WORKTREE_OK")
        root = Path(str(context.workspace_root))
        (root / "workflow-e2e.txt").write_text(text + "\n", encoding="utf-8")
        return {"text": text, "workspace_root": str(root)}


class LocalModelCleanReviewExecutor:
    """A reviewer whose only durable input is a sealed plan+diff packet."""

    def __init__(
        self,
        packet_path: Path,
        request_timeout: float,
        *,
        approve: bool = True,
    ):
        self.packet_path = packet_path
        self.request_timeout = request_timeout
        self.approve = approve

    def __call__(self, stage: Stage, context) -> dict:
        packet = ReviewPacket.from_dict(
            json.loads(self.packet_path.read_text(encoding="utf-8"))
        )
        expected = (
            '{"verdict":"approved","findings":[]}'
            if self.approve
            else json.dumps({
                "verdict": "changes_requested",
                "findings": [{
                    "id": stage.id,
                    "path": "workflow-e2e.txt",
                    "line": 1,
                    "severity": "high",
                    "message": "remove BUG_MARKER",
                }],
            })
        )
        instruction = (
            "The diff adds the exact WORKTREE_OK marker required by the plan, so approve it."
            if self.approve
            else "The diff contains BUG_MARKER, so request the specified change."
        )
        prompt = (
            "Review the implementation using only this validated plan and diff. "
            f"{instruction} Return exactly this JSON with no additional fields: "
            f"{expected}\nPACKET={json.dumps(packet.to_dict(), ensure_ascii=False)}"
        )
        client = runtime.make_client().with_options(timeout=self.request_timeout)
        with context.model_slot():
            response = client.chat.completions.create(
                model=runtime.resolve_local_model(context.model_key),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=96,
                response_format={"type": "json_object"},
                extra_body={"enable_thinking": False},
            )
        record_response_usage(context, response)
        text = str(response.choices[0].message.content or "").strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid review JSON: {text!r}") from exc
        return ReviewFeedback.from_dict(payload).to_dict()


class LocalModelConflictExecutor:
    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout

    def __call__(self, stage: Stage, context) -> dict:
        marker = f"CONFLICT_{stage.id.upper()}"
        client = runtime.make_client().with_options(timeout=self.request_timeout)
        with context.model_slot():
            response = client.chat.completions.create(
                model=runtime.resolve_local_model(context.model_key),
                messages=[{"role": "user", "content": f"Reply with exactly {marker}."}],
                max_tokens=96,
                extra_body={"enable_thinking": False},
            )
        record_response_usage(context, response)
        text = str(response.choices[0].message.content or "").strip()
        if marker not in text:
            raise RuntimeError(f"conflict stage omitted {marker}")
        Path(context.workspace_root, "conflict-e2e.txt").write_text(
            text + "\n", encoding="utf-8"
        )
        return {"text": text}


class LocalModelConflictFixer:
    def __init__(self, request_timeout: float):
        self.request_timeout = request_timeout

    def __call__(self, _stage: Stage, context) -> dict:
        client = runtime.make_client().with_options(timeout=self.request_timeout)
        with context.model_slot():
            response = client.chat.completions.create(
                model=runtime.resolve_local_model(context.model_key),
                messages=[{
                    "role": "user",
                    "content": "Resolve the merge by replying with exactly RESOLVED_BY_MODEL.",
                }],
                max_tokens=96,
                extra_body={"enable_thinking": False},
            )
        record_response_usage(context, response)
        text = str(response.choices[0].message.content or "").strip()
        if "RESOLVED_BY_MODEL" not in text:
            raise RuntimeError("real model fixer omitted RESOLVED_BY_MODEL")
        Path(context.workspace_root, "conflict-e2e.txt").write_text(
            "RESOLVED_BY_MODEL\n", encoding="utf-8"
        )
        return {"text": text}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def main(network_events: list[dict] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--model-startup-timeout", type=float, default=240)
    parser.add_argument("--request-timeout", type=float, default=180)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=PROJECT_DIR / "artifacts" / "orchestration" / "runs",
    )
    args = parser.parse_args()

    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact_dir = args.output_dir / run_stamp
    artifact_dir.mkdir(parents=True, exist_ok=False)
    checkpoint_path = artifact_dir / "checkpoint.json"
    report = {
        "schema_version": 1,
        "started_at": utc_now(),
        "status": "running",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "model": "qwen3.8-27b",
            "model_path": runtime.resolve_local_model("qwen3.8-27b"),
        },
        "model_server": {},
        "checkpoint": str(checkpoint_path),
    }
    server = ModelServer(artifact_dir, args.model_startup_timeout)
    exit_code = 1
    try:
        server.start()
        report["model_server"] = {"ownership": server.ownership, "pid": server.pid}
        model_router = ModelRouter.load(PROJECT_DIR / "config" / "models.yaml")
        standard_template = WorkflowTemplate.load(
            PROJECT_DIR / "config" / "workflows" / "standard.yaml"
        )
        template_by_id = {item.stage.id: item for item in standard_template.stages}
        stages = [
            Stage("explore", role="summarizer", fanout=3),
            Stage("plan", ("explore",), role="coder"),
            Stage("review", ("plan",), role="reviewer"),
        ]
        execute = LocalModelStageExecutor(checkpoint_path, args.request_timeout)
        limits = ExecutionLimits(
            timeout_seconds=args.request_timeout,
            max_tool_calls=0,
            retries=0,
        )
        crashing_store = CrashOnceCheckpointStore(checkpoint_path)
        engine = WorkflowEngine(stages, crashing_store, max_worker_spawns=6)
        try:
            engine.run_isolated(
                execute,
                limits,
                model_router=model_router,
                output_contracts={"explore": template_by_id["explore"].output},
                output_root=artifact_dir,
            )
        except SimulatedCrash:
            pass
        else:
            raise RuntimeError("simulated crash boundary was not reached")
        crash_snapshot = CheckpointStore(checkpoint_path).load()
        if crash_snapshot["stages"] != {
            "explore": "completed", "plan": "running", "review": "pending"
        }:
            raise RuntimeError(f"unexpected crash checkpoint: {crash_snapshot['stages']}")

        resumed = WorkflowEngine.resume(
            stages, CheckpointStore(checkpoint_path), max_worker_spawns=6
        )
        final = resumed.run_isolated(
            execute,
            limits,
            model_router=model_router,
            output_contracts={"explore": template_by_id["explore"].output},
            output_root=artifact_dir,
        )
        if final["stages"] != {
            "explore": "completed", "plan": "completed", "review": "completed"
        }:
            raise RuntimeError(f"unexpected final states: {final['stages']}")
        if final["sequence"] != 9:
            raise RuntimeError(f"unexpected checkpoint boundary count: {final['sequence']}")
        if final["attempts"] != {"explore": 1, "plan": 2, "review": 1}:
            raise RuntimeError(f"unexpected stage attempts: {final['attempts']}")
        if final["worker_spawns"] != 6:
            raise RuntimeError(f"unexpected worker spawn count: {final['worker_spawns']}")
        explore_outputs = final.get("outputs", {}).get("explore", {})
        if set(explore_outputs) != {"summaries"}:
            raise RuntimeError(f"explore leaked non-summary output: {explore_outputs}")
        summary_markers = {
            CheckpointStore(checkpoint_path.parent / item["path"])
            .load()["summary"]
            .split("EXPLORE_", 1)[1]
            .split("_OK", 1)[0]
            for item in explore_outputs["summaries"]
        }
        if summary_markers != {"0", "1", "2"}:
            raise RuntimeError(f"unexpected explore fan-out markers: {summary_markers}")
        plan_output = final.get("outputs", {}).get("plan", {})
        if set(plan_output) != {"spec_path"}:
            raise RuntimeError(f"plan leaked non-spec output: {plan_output}")
        plan_spec = PlanSpec.from_dict(
            json.loads(
                (checkpoint_path.parent / plan_output["spec_path"]).read_text(
                    encoding="utf-8"
                )
            )
        )
        implement_stages = plan_spec.implement_stages(needs=())
        if len(implement_stages) != 1 or implement_stages[0].owns != ("workflow-e2e.txt",):
            raise RuntimeError(f"unexpected plan ownership partition: {plan_spec.to_dict()}")
        report["status"] = "passed"
        report["crash_snapshot"] = crash_snapshot
        report["limits"] = {
            "timeout_seconds": limits.timeout_seconds,
            "max_tool_calls": limits.max_tool_calls,
            "retries": limits.retries,
        }
        report["final"] = final
        report["template"] = {
            "path": "config/workflows/standard.yaml",
            "stages": [item.stage.id for item in standard_template.stages],
            "actual_contract_stage": "explore",
        }

        failure_checkpoint = artifact_dir / "failure-checkpoint.json"
        failure_stages = [
            Stage("primary", on_fail="fallback", role="coder"),
            Stage(
                "fallback", ("primary",), on_fail="human", role="summarizer"
            ),
        ]
        failure_engine = WorkflowEngine(
            failure_stages,
            CheckpointStore(failure_checkpoint),
            max_worker_spawns=2,
        )
        failure_final = failure_engine.run_isolated(
            LocalModelFallbackExecutor(args.request_timeout),
            ExecutionLimits(
                timeout_seconds=args.request_timeout,
                max_tool_calls=0,
                retries=0,
            ),
            model_router=model_router,
        )
        expected_failure_states = {"primary": "failed", "fallback": "completed"}
        if failure_final["stages"] != expected_failure_states:
            raise RuntimeError(
                f"unexpected fallback states: {failure_final['stages']}"
            )
        if "FALLBACK_OK" not in failure_final["outputs"]["fallback"]["text"]:
            raise RuntimeError("real-model fallback omitted FALLBACK_OK")
        report["failure_route"] = failure_final

        write_manager = WriteWorkspaceManager(
            storage_root=artifact_dir / "worktrees",
            repo_path=REPO_ROOT,
            base_ref="HEAD",
            pipeline_id=f"e2e-{run_stamp}",
        )
        implement_stage = implement_stages[0]
        write_engine = WorkflowEngine(
            [implement_stage],
            CheckpointStore(artifact_dir / "write-checkpoint.json"),
            max_worker_spawns=1,
        )
        write_final = write_engine.run_isolated(
            LocalModelWriteExecutor(args.request_timeout),
            ExecutionLimits(
                timeout_seconds=args.request_timeout,
                max_tool_calls=0,
                retries=0,
            ),
            workspace_manager=write_manager,
            integration_command=(
                "test \"$(cat workflow-e2e.txt)\" = WORKTREE_OK"
            ),
            model_router=model_router,
        )
        write_record = write_final["worktrees"][implement_stage.id][0]
        if write_record.get("worker_verification", {}).get("exit_code") != 0:
            raise RuntimeError(f"worker verification did not pass: {write_record}")
        if not write_record.get("commit_sha") or not write_record.get("archived"):
            raise RuntimeError(f"write worktree was not committed and archived: {write_record}")
        if Path(write_record["root_path"]).exists():
            raise RuntimeError("write worktree remains after archive")
        integration = write_final.get("integration") or {}
        if integration.get("status") != "verified":
            raise RuntimeError(f"write integration did not verify: {integration}")
        review_diff = write_manager.service._git(
            REPO_ROOT,
            "diff",
            "HEAD",
            integration["branch_name"],
            "--",
            "workflow-e2e.txt",
        ).stdout
        review_packet_path = artifact_dir / "review-packet.json"
        ReviewPacket.from_dict({
            "plan": plan_spec.to_dict(),
            "diff": review_diff,
        }).save(review_packet_path)
        review_engine = WorkflowEngine(
            [Stage("clean_review", role="reviewer")],
            CheckpointStore(artifact_dir / "review-checkpoint.json"),
            max_worker_spawns=1,
        )
        review_final = review_engine.run_isolated(
            LocalModelCleanReviewExecutor(review_packet_path, args.request_timeout),
            ExecutionLimits(
                timeout_seconds=args.request_timeout,
                max_tool_calls=0,
                retries=0,
            ),
            model_router=model_router,
        )
        review_loop = ReviewLoop(artifact_dir)
        review_result = review_loop.record(review_final["outputs"]["clean_review"])
        if review_result["status"] != "approved":
            raise RuntimeError(f"clean review did not approve: {review_result}")
        rejection_packet_path = artifact_dir / "rejection-review-packet.json"
        ReviewPacket.from_dict({
            "plan": plan_spec.to_dict(),
            "diff": "diff --git a/workflow-e2e.txt b/workflow-e2e.txt\n+BUG_MARKER\n",
        }).save(rejection_packet_path)
        rejection_engine = WorkflowEngine(
            [
                Stage("review_one", role="reviewer"),
                Stage("review_two", needs=("review_one",), role="reviewer"),
            ],
            CheckpointStore(artifact_dir / "rejection-review-checkpoint.json"),
            max_worker_spawns=2,
        )
        rejection_final = rejection_engine.run_isolated(
            LocalModelCleanReviewExecutor(
                rejection_packet_path,
                args.request_timeout,
                approve=False,
            ),
            ExecutionLimits(
                timeout_seconds=args.request_timeout,
                max_tool_calls=0,
                retries=0,
            ),
            model_router=model_router,
        )
        rejection_loop = ReviewLoop(artifact_dir / "rejection-feedback")
        rejection_results = [
            rejection_loop.record(rejection_final["outputs"][stage_id])
            for stage_id in ("review_one", "review_two")
        ]
        if [item["status"] for item in rejection_results] != ["revise", "needs_human"]:
            raise RuntimeError(f"review loop did not stop for human: {rejection_results}")
        deleted = []
        for branch in (write_record["branch_name"], integration["branch_name"]):
            result = write_manager.service.delete_branch(
                repo_path=REPO_ROOT,
                branch_name=branch,
            )
            deleted.append(result)
        if not all(item["deleted"] for item in deleted):
            raise RuntimeError("E2E write or integration branch was not deleted")
        report["write_isolation"] = {
            **write_final,
            "e2e_branch_deleted": True,
        }
        report["clean_review"] = {
            "packet_fields": sorted(
                json.loads(review_packet_path.read_text(encoding="utf-8"))
            ),
            "workflow": review_final,
            "result": review_result,
            "rejection_workflow": rejection_final,
            "rejection_results": rejection_results,
        }

        conflict_manager = WriteWorkspaceManager(
            storage_root=artifact_dir / "conflict-worktrees",
            repo_path=REPO_ROOT,
            base_ref="HEAD",
            pipeline_id=f"conflict-e2e-{run_stamp}",
        )
        conflict_engine = WorkflowEngine(
            [
                Stage("a", write="worktree", owns=("conflict-e2e.txt",)),
                Stage("b", write="worktree", owns=("conflict-e2e.txt",)),
            ],
            CheckpointStore(artifact_dir / "conflict-checkpoint.json"),
            max_worker_spawns=3,
        )
        conflict_final = conflict_engine.run_isolated(
            LocalModelConflictExecutor(args.request_timeout),
            ExecutionLimits(
                timeout_seconds=args.request_timeout,
                max_tool_calls=0,
                retries=0,
            ),
            workspace_manager=conflict_manager,
            integration_command="grep -qx RESOLVED_BY_MODEL conflict-e2e.txt",
            conflict_fixer=LocalModelConflictFixer(args.request_timeout),
            conflict_fixer_limits=ExecutionLimits(
                timeout_seconds=args.request_timeout,
                max_tool_calls=0,
                retries=10,
            ),
            model_router=model_router,
        )
        if conflict_final["integration"].get("status") != "verified":
            raise RuntimeError("real-model conflict fixer did not verify")
        if conflict_final["integration"].get("fixer_attempts") != 1:
            raise RuntimeError("real-model conflict fixer did not run exactly once")
        conflict_branches = [
            record["branch_name"]
            for records in conflict_final["worktrees"].values()
            for record in records
            if record.get("branch_name")
        ]
        conflict_branches.append(conflict_final["integration"]["branch_name"])
        conflict_deleted = [
            conflict_manager.service.delete_branch(
                repo_path=REPO_ROOT, branch_name=branch
            )
            for branch in conflict_branches
        ]
        if not all(item["deleted"] for item in conflict_deleted):
            raise RuntimeError("real-model conflict E2E branches were not deleted")
        report["conflict_fixer"] = {
            **conflict_final,
            "e2e_branches_deleted": True,
        }
        all_scheduler_events = [
            *final["scheduler_events"],
            *failure_final["scheduler_events"],
            *write_final["scheduler_events"],
            *conflict_final["scheduler_events"],
        ]
        vram_gate = assess_vram_sizing(all_scheduler_events)
        if vram_gate["status"] not in {"deferred", "recommended"}:
            raise RuntimeError(f"unexpected VRAM sizing gate: {vram_gate}")
        if vram_gate["status"] == "recommended" and vram_gate["reason"] != "measured_model_slot_bottleneck":
            raise RuntimeError(f"inconsistent VRAM sizing recommendation: {vram_gate}")
        report["vram_sizing_gate"] = vram_gate
        assert_local_artifacts(report)
        exit_code = 0
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
    finally:
        server.stop()
        report["finished_at"] = utc_now()
        if network_events is not None:
            report["airgap"] = {
                "policy": "loopback_and_unix_only",
                "allowed_local_attempts": len(network_events),
                "blocked_attempts": sum(
                    event["kind"].startswith("blocked") for event in network_events
                ),
            }
        report["model_server"].update(server.stop_result)
        result_path = artifact_dir / "result.json"
        result_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({
            "status": report["status"],
            "result": str(result_path),
            "stages": (report.get("final") or {}).get("stages", {}),
        }, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    with local_network_only() as audit_events:
        raise SystemExit(main(audit_events))
