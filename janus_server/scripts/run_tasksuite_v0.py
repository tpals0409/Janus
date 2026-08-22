#!/usr/bin/env python3
"""TaskSuite v0를 실제 27B runtime으로 반복 실행하고 baseline을 저장한다."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import shutil
import statistics
import sys
import threading
import time
import traceback
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from janus_server import runtime  # noqa: E402
from janus_server import verification  # noqa: E402
from janus_server.workspace import WorkspaceContext  # noqa: E402
from p0_smoke_27b import ModelServer, SmokeFailure  # noqa: E402

POLICIES = ("none", "fixed_one", "autonomous")
IGNORED_PARTS = {"__pycache__", ".pytest_cache", ".git"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def file_hashes(root: Path) -> dict[str, str]:
    hashes = {}
    for path in sorted(root.rglob("*")):
        if not path.is_file() or any(part in IGNORED_PARTS for part in path.parts):
            continue
        if path.suffix in {".pyc", ".pyo"}:
            continue
        hashes[str(path.relative_to(root))] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def changed_files(before: dict[str, str], after: dict[str, str]) -> list[str]:
    return sorted(path for path in before.keys() | after.keys() if before.get(path) != after.get(path))


SEMANTIC_EVENT_KINDS = {
    "resource_queue_enter", "resource_lease_acquired",
    "model_generation_start", "model_generation_end",
    "tool_run_start", "tool_run_end", "tool_start", "tool_result",
    "verification_start", "verification_end", "usage", "done",
}


def compact_telemetry(telemetry: dict) -> dict:
    """분석 가능한 timing/usage는 보존하고 token delta·prompt 중복만 제거한다."""
    compact = dict(telemetry)
    compact["events"] = [
        event for event in telemetry.get("events", [])
        if event.get("kind") in SEMANTIC_EVENT_KINDS
    ]
    return compact


def compact_spans(spans: list[dict]) -> list[dict]:
    return [{key: value for key, value in span.items() if key != "events"} for span in spans]


def compact_run_result(result: dict) -> dict:
    compact = dict(result)
    if isinstance(compact.get("telemetry"), dict):
        compact["telemetry"] = compact_telemetry(compact["telemetry"])
    if isinstance(compact.get("spans"), list):
        compact["spans"] = compact_spans(compact["spans"])
    return compact


def aggregate_run_result(result: dict) -> dict:
    """상위 result.json에는 표 계산에 필요한 값만 두고 상세는 runs/*/run.json에 둔다."""
    return {
        key: value for key, value in result.items()
        if key not in {"telemetry", "spans", "reply"}
    }


def policy_prompt(policy: str) -> str:
    common = (
        "You are an autonomous coding agent in a benchmark workspace. Read relevant files, "
        "make the requested change with file tools, and finish with a short factual summary. "
        "The harness runs acceptance independently; you have no shell tool. Never access paths "
        "outside the workspace."
    )
    if policy == "none":
        return common + " Work directly; no worker tool is available."
    if policy == "fixed_one":
        return (
            common
            + " Before doing other work, call create_worker exactly once. Delegate the complete "
              "task to it and give it every file tool it needs. Integrate its result."
        )
    return (
        common
        + " Decide yourself whether workers help. Spawn only genuinely useful workers and integrate "
          "their results before finishing."
    )


def task_prompt(task: dict) -> str:
    constraints = "\n".join(f"- {item}" for item in task["constraints"])
    return (
        f"Task ID: {task['id']}\n"
        f"Objective: {task['objective']}\n\n"
        f"Constraints:\n{constraints}\n\n"
        f"Acceptance command (exact): {task['acceptance_command']}"
    )


def run_turn(orch: runtime.Orchestration, prompt: str, timeout: float) -> str | None:
    error: list[BaseException] = []

    def target() -> None:
        try:
            orch.turn(prompt)
        except BaseException as exc:
            error.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)
    if thread.is_alive():
        orch.cancel_all()
        thread.join(5)
        return f"turn timeout({timeout:.0f}s)"
    if error:
        return f"{type(error[0]).__name__}: {error[0]}"
    return None


def last_dispatch_id(orch: runtime.Orchestration) -> str | None:
    return next(
        (
            item["dispatch_id"]
            for item in reversed(orch.telemetry.intervals)
            if item["category"] == "active_turn"
        ),
        None,
    )


def run_acceptance(
    orch: runtime.Orchestration, command: str, context: WorkspaceContext
) -> dict:
    operation_id = uuid.uuid4().hex[:16]
    dispatch_id = last_dispatch_id(orch)
    orch.telemetry.record_event(
        "verification_start", node_id="verifier", dispatch_id=dispatch_id,
        worker_id=None, operation_id=operation_id, command=command,
    )
    result = verification.run(command, context, timeout=120)
    orch.telemetry.record_event(
        "verification_end", node_id="verifier", dispatch_id=dispatch_id,
        worker_id=None, operation_id=operation_id,
        status="success" if result["exit_code"] == 0 else "error",
        command=command,
    )
    return result


def run_once(task: dict, policy: str, repeat: int, run_dir: Path, timeout: float) -> dict:
    fixture = PROJECT_DIR / "tasksuite" / "v0" / "fixtures" / task["id"]
    workspace = run_dir / "workspace"
    shutil.copytree(fixture, workspace)
    before = file_hashes(workspace)
    context = WorkspaceContext(
        root=workspace,
        task_id=f"task_tasksuite_{task['id']}_{policy}_{repeat}",
        workspace_id=f"workspace_tasksuite_{task['id']}_{policy}_{repeat}",
    )
    approval_requests: list[dict] = []

    def approve(
        node_id: str, tool: str, args: dict, approval_context: WorkspaceContext
    ) -> bool:
        approval_requests.append({
            "node_id": node_id, "tool": tool, "args": args,
            **approval_context.identifiers(),
        })
        return True

    spec = {
        "name": f"TaskSuite {task['id']} {policy}",
        "model": "qwen3.8-27b",
        "system_prompt": policy_prompt(policy),
        "tools": ["read_file", "glob", "grep", "write_file", "edit_file"],
        "worker_policy": policy,
        "approval": "ask",
        "max_steps": 10,
    }
    events: list[dict] = []
    orch = runtime.Orchestration(
        spec, send=events.append, approver=approve, workspace_context=context
    )
    started = time.monotonic()
    turn_error = run_turn(orch, task_prompt(task), timeout)
    after_agent = file_hashes(workspace)
    changed = changed_files(before, after_agent)
    acceptance = run_acceptance(orch, task["acceptance_command"], context)
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)

    required = set(task["required_changed_files"])
    allowed = set(task["allowed_changed_files"])
    missing_required = sorted(required - set(changed))
    unexpected = sorted(set(changed) - allowed)
    worker_count = orch.worker_seq
    policy_conformant = (
        worker_count == 0 if policy == "none"
        else worker_count == 1 if policy == "fixed_one"
        else True
    )
    acceptance_passed = (
        turn_error is None
        and acceptance["exit_code"] == 0
        and not missing_required
        and not unexpected
    )
    telemetry = compact_telemetry(orch.snapshot_telemetry())
    result = {
        "schema_version": 1,
        "task_id": task["id"],
        "category": task["category"],
        "policy": policy,
        "repeat": repeat,
        "status": "passed" if acceptance_passed and policy_conformant else "failed",
        "acceptance_passed": acceptance_passed,
        "policy_conformant": policy_conformant,
        "turn_error": turn_error,
        "wall_time_ms": elapsed_ms,
        "changed_files": changed,
        "missing_required_files": missing_required,
        "unexpected_changed_files": unexpected,
        "acceptance": acceptance,
        "worker_count": worker_count,
        "approval_requests": len(approval_requests),
        "user_inputs": 1,
        "reply": orch.last_text,
        "tokens": telemetry["tokens"],
        "telemetry": telemetry,
        "spans": compact_spans(orch.snapshot_spans()),
    }
    (run_dir / "run.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return result


def summarize(runs: list[dict]) -> list[dict]:
    rows = []
    keys = sorted({(run["task_id"], run["policy"]) for run in runs})
    for task_id, policy in keys:
        group = [run for run in runs if run["task_id"] == task_id and run["policy"] == policy]
        wall = [run["wall_time_ms"] for run in group]
        rows.append({
            "task_id": task_id,
            "policy": policy,
            "runs": len(group),
            "acceptance_successes": sum(run["acceptance_passed"] for run in group),
            "policy_conformant_runs": sum(run["policy_conformant"] for run in group),
            "wall_mean_ms": round(statistics.mean(wall), 3),
            "wall_stdev_ms": round(statistics.pstdev(wall), 3),
            "prompt_tokens_mean": round(statistics.mean(run["tokens"]["prompt"] for run in group), 1),
            "completion_tokens_mean": round(
                statistics.mean(run["tokens"]["completion"] for run in group), 1
            ),
            "approval_requests_mean": round(
                statistics.mean(run["approval_requests"] for run in group), 1
            ),
            "worker_count_mean": round(statistics.mean(run["worker_count"] for run in group), 1),
        })
    return rows


def write_summary(output_dir: Path, report: dict) -> None:
    rows = summarize(report["runs"]) if report["runs"] else []
    report["summary"] = rows
    tmp = output_dir / ".result.json.tmp"
    tmp.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(output_dir / "result.json")
    if not rows:
        return
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    lines = [
        "# TaskSuite v0 baseline",
        "",
        "| Task | Policy | Success | Policy | Wall mean ± σ (s) | Prompt / Completion tok | Approvals | Workers |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for row in rows:
        lines.append(
            f"| {row['task_id']} | {row['policy']} | "
            f"{row['acceptance_successes']}/{row['runs']} | "
            f"{row['policy_conformant_runs']}/{row['runs']} | "
            f"{row['wall_mean_ms'] / 1000:.2f} ± {row['wall_stdev_ms'] / 1000:.2f} | "
            f"{row['prompt_tokens_mean']:.1f} / {row['completion_tokens_mean']:.1f} | "
            f"{row['approval_requests_mean']:.1f} | {row['worker_count_mean']:.1f} |"
        )
    lines.extend([
        "",
        "## Overall by policy",
        "",
        "| Policy | Success | Wall mean ± σ (s) | Prompt / Completion tok | Approvals | Workers |",
        "|---|---:|---:|---:|---:|---:|",
    ])
    for policy in POLICIES:
        group = [run for run in report["runs"] if run["policy"] == policy]
        if not group:
            continue
        wall = [run["wall_time_ms"] for run in group]
        lines.append(
            f"| {policy} | {sum(run['acceptance_passed'] for run in group)}/{len(group)} | "
            f"{statistics.mean(wall) / 1000:.2f} ± {statistics.pstdev(wall) / 1000:.2f} | "
            f"{statistics.mean(run['tokens']['prompt'] for run in group):.1f} / "
            f"{statistics.mean(run['tokens']['completion'] for run in group):.1f} | "
            f"{statistics.mean(run['approval_requests'] for run in group):.2f} | "
            f"{statistics.mean(run['worker_count'] for run in group):.2f} |"
        )
    lines.extend([
        "",
        "`Approvals`는 benchmark가 자동 승인한 write/edit 요청 수다. 실제 user message는 "
        "모든 실행이 1개로 고정됐다. acceptance는 agent와 분리된 harness가 실행한다.",
    ])
    (output_dir / "baseline.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    manifest_path = PROJECT_DIR / "tasksuite" / "v0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    parser = argparse.ArgumentParser()
    parser.add_argument("--repeats", type=int, default=manifest["repeats"])
    parser.add_argument("--policies", nargs="+", choices=POLICIES, default=list(POLICIES))
    parser.add_argument("--tasks", nargs="+", default=[task["id"] for task in manifest["tasks"]])
    parser.add_argument("--turn-timeout", type=float, default=180)
    parser.add_argument("--model-startup-timeout", type=float, default=240)
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_DIR / "artifacts" / "p0" / "tasksuite" /
        datetime.now().strftime("%Y%m%d-%H%M%S"),
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be >= 1")
    tasks = [task for task in manifest["tasks"] if task["id"] in args.tasks]
    if len(tasks) != len(set(args.tasks)):
        parser.error("unknown or duplicate task id")

    output_dir = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=False)
    report = {
        "schema_version": 1,
        "suite": manifest["suite"],
        "started_at": utc_now(),
        "status": "running",
        "conditions": {
            **manifest["conditions"],
            "platform": platform.platform(),
            "python": sys.version,
            "model_path": runtime.resolve_local_model("qwen3.8-27b"),
            "turn_timeout_seconds": args.turn_timeout,
            "repeats": args.repeats,
            "policies": args.policies,
            "tasks": [task["id"] for task in tasks],
        },
        "model_server": {},
        "runs": [],
        "summary": [],
    }
    server = ModelServer(output_dir, args.model_startup_timeout)
    harness_error = None
    try:
        server.start()
        report["model_server"] = {"ownership": server.ownership, "pid": server.pid}
        total = len(tasks) * len(args.policies) * args.repeats
        index = 0
        for task in tasks:
            for policy in args.policies:
                for repeat in range(1, args.repeats + 1):
                    index += 1
                    run_dir = output_dir / "runs" / task["id"] / policy / str(repeat)
                    run_dir.mkdir(parents=True, exist_ok=False)
                    print(f"[{index}/{total}] {task['id']} {policy} repeat={repeat}", flush=True)
                    try:
                        run = run_once(task, policy, repeat, run_dir, args.turn_timeout)
                    except Exception as exc:
                        run = {
                            "task_id": task["id"], "policy": policy, "repeat": repeat,
                            "status": "harness_error", "acceptance_passed": False,
                            "policy_conformant": False, "wall_time_ms": 0,
                            "worker_count": 0, "approval_requests": 0,
                            "tokens": {"prompt": 0, "completion": 0, "total": 0},
                            "error": f"{type(exc).__name__}: {exc}",
                            "traceback": traceback.format_exc(),
                        }
                        (run_dir / "run.json").write_text(
                            json.dumps(run, ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8",
                        )
                    report["runs"].append(aggregate_run_result(run))
                    write_summary(output_dir, report)
                    print(
                        f"  -> {run['status']} acceptance={run['acceptance_passed']} "
                        f"workers={run['worker_count']} wall={run['wall_time_ms']/1000:.2f}s",
                        flush=True,
                    )
    except Exception as exc:
        harness_error = f"{type(exc).__name__}: {exc}"
        report["error"] = harness_error
        report["traceback"] = traceback.format_exc()
    finally:
        server.stop()
        report["model_server"]["exit_code"] = (
            server.process.returncode if server.process is not None else None
        )
        report["model_server"].update(server.stop_result)
        report["finished_at"] = utc_now()
        expected = len(tasks) * len(args.policies) * args.repeats
        report["status"] = "completed" if harness_error is None and len(report["runs"]) == expected else "failed"
        write_summary(output_dir, report)
        print(json.dumps({
            "status": report["status"],
            "result": str(output_dir / "result.json"),
            "runs": len(report["runs"]),
        }, ensure_ascii=False), flush=True)
    return 0 if report["status"] == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
