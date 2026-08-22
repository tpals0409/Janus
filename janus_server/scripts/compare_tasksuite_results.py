#!/usr/bin/env python3
"""R1 baseline과 R3 TaskSuite 후보를 같은 key로 비교해 판정표를 만든다."""

from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path


def _pct(candidate: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return round((candidate - baseline) / baseline * 100, 2)


def _groups(report: dict) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = {}
    for run in report.get("runs") or []:
        groups.setdefault((run["task_id"], run["policy"]), []).append(run)
    return groups


def _metrics(runs: list[dict]) -> dict:
    return {
        "runs": len(runs),
        "acceptance_successes": sum(bool(run["acceptance_passed"]) for run in runs),
        "wall_mean_ms": round(statistics.mean(run["wall_time_ms"] for run in runs), 3),
        "prompt_tokens_mean": round(statistics.mean(
            run["tokens"]["prompt"] for run in runs
        ), 1),
        "completion_tokens_mean": round(statistics.mean(
            run["tokens"]["completion"] for run in runs
        ), 1),
        "queue_ms_mean": round(statistics.mean(
            run.get("timing_ms", {}).get("resource_queue", 0) for run in runs
        ), 3),
        "saved_token_estimate_mean": round(statistics.mean(
            run.get("efficiency", {}).get("saved_token_estimate", 0) for run in runs
        ), 1),
        "spawn_suppressions": sum(
            run.get("efficiency", {}).get("worker_spawn_suppressions", 0)
            for run in runs
        ),
    }


def compare(baseline: dict, candidate: dict) -> dict:
    baseline_groups = _groups(baseline)
    candidate_groups = _groups(candidate)
    if set(baseline_groups) != set(candidate_groups):
        raise ValueError("baseline/candidate의 Task·policy key가 다릅니다")
    rows = []
    for task_id, policy in sorted(baseline_groups):
        before = _metrics(baseline_groups[(task_id, policy)])
        after = _metrics(candidate_groups[(task_id, policy)])
        rows.append({
            "task_id": task_id,
            "policy": policy,
            "baseline": before,
            "candidate": after,
            "acceptance_delta": (
                after["acceptance_successes"] - before["acceptance_successes"]
            ),
            "wall_delta_pct": _pct(after["wall_mean_ms"], before["wall_mean_ms"]),
            "prompt_token_delta_pct": _pct(
                after["prompt_tokens_mean"], before["prompt_tokens_mean"]
            ),
            "completion_token_delta_pct": _pct(
                after["completion_tokens_mean"], before["completion_tokens_mean"]
            ),
        })
    regressions = [
        f"{row['task_id']}/{row['policy']}"
        for row in rows if row["acceptance_delta"] < 0
    ]
    return {
        "schema_version": 1,
        "baseline": baseline.get("label", "r1-baseline"),
        "candidate": candidate.get("label", "r3-candidate"),
        "verdict": "acceptance_regression" if regressions else "acceptance_maintained",
        "acceptance_regressions": regressions,
        "rows": rows,
    }


def markdown(comparison: dict) -> str:
    lines = [
        "# R1 baseline vs R3 candidate",
        "",
        f"Verdict: **{comparison['verdict']}**",
        "",
        "| Task | Policy | Acceptance B→C | Wall Δ | Prompt tok Δ | Completion tok Δ | Queue ms | Saved tok est. | Suppress |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for row in comparison["rows"]:
        before, after = row["baseline"], row["candidate"]
        lines.append(
            f"| {row['task_id']} | {row['policy']} | "
            f"{before['acceptance_successes']}/{before['runs']} → "
            f"{after['acceptance_successes']}/{after['runs']} | "
            f"{row['wall_delta_pct']:+.2f}% | {row['prompt_token_delta_pct']:+.2f}% | "
            f"{row['completion_token_delta_pct']:+.2f}% | "
            f"{after['queue_ms_mean']:.1f} | {after['saved_token_estimate_mean']:.1f} | "
            f"{after['spawn_suppressions']} |"
        )
    if comparison["acceptance_regressions"]:
        lines.extend([
            "", "Acceptance regressions: "
            + ", ".join(comparison["acceptance_regressions"]),
        ])
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args()
    baseline = json.loads(args.baseline.read_text(encoding="utf-8"))
    candidate = json.loads(args.candidate.read_text(encoding="utf-8"))
    comparison = compare(baseline, candidate)
    output_dir = args.output_dir or args.candidate.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "comparison.json").write_text(
        json.dumps(comparison, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (output_dir / "comparison.md").write_text(
        markdown(comparison), encoding="utf-8"
    )
    print(json.dumps({
        "verdict": comparison["verdict"],
        "comparison": str(output_dir / "comparison.json"),
    }, ensure_ascii=False))
    return 1 if comparison["acceptance_regressions"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
