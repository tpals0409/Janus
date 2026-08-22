"""Reproducible AgentProfile A/B evaluation and regression decisions."""

from __future__ import annotations

import csv
import io
import json
import math
import statistics
from typing import Any


class EvaluationError(ValueError):
    pass


DEFAULT_THRESHOLDS = {
    "max_success_rate_drop_pp": 0.0,
    "max_wall_regression_pct": 15.0,
    "max_token_regression_pct": 10.0,
    "max_intervention_increase": 0.0,
    "min_improvement_pct": 5.0,
}
COMPARABILITY_FIELDS = ("model", "quantization", "platform")


def _number(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise EvaluationError(f"{label}은 숫자여야 합니다")
    result = float(value)
    if not math.isfinite(result) or result < 0:
        raise EvaluationError(f"{label}은 0 이상의 유한한 숫자여야 합니다")
    return result


def validate_report(report: Any) -> dict:
    if not isinstance(report, dict):
        raise EvaluationError("evaluation report는 객체여야 합니다")
    if not str(report.get("label") or "").strip():
        raise EvaluationError("report label이 필요합니다")
    conditions = report.get("conditions")
    if not isinstance(conditions, dict):
        raise EvaluationError("report conditions가 필요합니다")
    for field in ("model", "quantization", "platform"):
        if not str(conditions.get(field) or "").strip():
            raise EvaluationError(f"conditions.{field}가 필요합니다")
    runs = report.get("runs")
    if not isinstance(runs, list) or not runs:
        raise EvaluationError("report에 실행 결과가 하나 이상 필요합니다")
    normalized = []
    for index, item in enumerate(runs):
        if not isinstance(item, dict):
            raise EvaluationError(f"runs[{index}]는 객체여야 합니다")
        task_id = str(item.get("task_id") or "").strip()
        if not task_id:
            raise EvaluationError(f"runs[{index}].task_id가 필요합니다")
        tokens = item.get("tokens") or {}
        normalized.append({
            **item,
            "task_id": task_id,
            "acceptance_passed": bool(item.get("acceptance_passed")),
            "wall_time_ms": _number(item.get("wall_time_ms", 0), f"runs[{index}].wall_time_ms"),
            "tokens": {
                **tokens,
                "prompt": _number(tokens.get("prompt", 0), f"runs[{index}].tokens.prompt"),
                "completion": _number(tokens.get("completion", 0), f"runs[{index}].tokens.completion"),
                "total": _number(tokens.get("total", 0), f"runs[{index}].tokens.total"),
            },
            "user_inputs": _number(item.get("user_inputs", 0), f"runs[{index}].user_inputs"),
            "approval_requests": _number(
                item.get("approval_requests", 0), f"runs[{index}].approval_requests"
            ),
        })
    return {**report, "conditions": dict(conditions), "runs": normalized}


def _percentile(values: list[float], quantile: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    position = (len(ordered) - 1) * quantile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    return ordered[lower] + (ordered[upper] - ordered[lower]) * (position - lower)


def _mean(values: list[float]) -> float:
    return statistics.mean(values) if values else 0.0


def metrics(runs: list[dict]) -> dict:
    if not runs:
        raise EvaluationError("빈 실행 그룹은 요약할 수 없습니다")
    wall = [float(item["wall_time_ms"]) for item in runs]
    total_tokens = [float(item["tokens"]["total"]) for item in runs]
    interventions = [
        float(item["user_inputs"]) + float(item["approval_requests"]) for item in runs
    ]
    successes = sum(bool(item["acceptance_passed"]) for item in runs)
    return {
        "runs": len(runs),
        "successes": successes,
        "success_rate": round(successes / len(runs), 6),
        "wall_mean_ms": round(_mean(wall), 3),
        "wall_stdev_ms": round(statistics.pstdev(wall), 3),
        "wall_p95_ms": round(_percentile(wall, 0.95), 3),
        "tokens_mean": round(_mean(total_tokens), 3),
        "tokens_stdev": round(statistics.pstdev(total_tokens), 3),
        "interventions_mean": round(_mean(interventions), 3),
        "interventions_stdev": round(statistics.pstdev(interventions), 3),
        "worker_count_mean": round(_mean([
            float(item.get("worker_count", 0)) for item in runs
        ]), 3),
        "memory_peak_bytes_mean": round(_mean([
            float(item.get("memory_peak_bytes", 0)) for item in runs
        ]), 3),
    }


def summarize(report: dict) -> dict:
    report = validate_report(report)
    task_ids = sorted({item["task_id"] for item in report["runs"]})
    by_task = {
        task_id: metrics([
            item for item in report["runs"] if item["task_id"] == task_id
        ])
        for task_id in task_ids
    }
    return {
        "label": report["label"], "conditions": report["conditions"],
        "overall": metrics(report["runs"]), "by_task": by_task,
    }


def _delta_pct(candidate: float, baseline: float) -> float | None:
    if baseline == 0:
        return None
    return round((candidate - baseline) / baseline * 100, 3)


def _compare_metrics(baseline: dict, candidate: dict) -> dict:
    return {
        "baseline": baseline,
        "candidate": candidate,
        "success_rate_delta_pp": round(
            (candidate["success_rate"] - baseline["success_rate"]) * 100, 3
        ),
        "wall_delta_pct": _delta_pct(candidate["wall_mean_ms"], baseline["wall_mean_ms"]),
        "wall_p95_delta_pct": _delta_pct(
            candidate["wall_p95_ms"], baseline["wall_p95_ms"]
        ),
        "token_delta_pct": _delta_pct(candidate["tokens_mean"], baseline["tokens_mean"]),
        "intervention_delta": round(
            candidate["interventions_mean"] - baseline["interventions_mean"], 3
        ),
    }


def compare(
    baseline_report: dict, candidate_report: dict,
    thresholds: dict | None = None,
) -> dict:
    baseline = summarize(baseline_report)
    candidate = summarize(candidate_report)
    configured = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    for key, value in configured.items():
        configured[key] = _number(value, f"thresholds.{key}")

    mismatches = [
        {
            "field": field, "baseline": baseline["conditions"].get(field),
            "candidate": candidate["conditions"].get(field),
        }
        for field in COMPARABILITY_FIELDS
        if baseline["conditions"].get(field) != candidate["conditions"].get(field)
    ]
    if set(baseline["by_task"]) != set(candidate["by_task"]):
        raise EvaluationError("baseline/candidate의 Task 집합이 다릅니다")

    overall = _compare_metrics(baseline["overall"], candidate["overall"])
    rows = [
        {"task_id": task_id, **_compare_metrics(
            baseline["by_task"][task_id], candidate["by_task"][task_id]
        )}
        for task_id in baseline["by_task"]
    ]
    regressions: list[dict] = []
    improvements: list[dict] = []
    for scope, row in [("overall", overall)] + [
        (item["task_id"], item) for item in rows
    ]:
        if row["success_rate_delta_pp"] < -configured["max_success_rate_drop_pp"]:
            regressions.append({
                "scope": scope, "metric": "success_rate",
                "delta": row["success_rate_delta_pp"],
            })
        for metric, threshold_key in (
            ("wall_delta_pct", "max_wall_regression_pct"),
            ("token_delta_pct", "max_token_regression_pct"),
        ):
            delta = row[metric]
            if delta is not None and delta > configured[threshold_key]:
                regressions.append({"scope": scope, "metric": metric, "delta": delta})
            if delta is not None and delta <= -configured["min_improvement_pct"]:
                improvements.append({"scope": scope, "metric": metric, "delta": delta})
        if row["intervention_delta"] > configured["max_intervention_increase"]:
            regressions.append({
                "scope": scope, "metric": "interventions",
                "delta": row["intervention_delta"],
            })
    verdict = (
        "incomparable_conditions" if mismatches else
        "regression" if regressions else
        "improved" if improvements else "equivalent"
    )
    return {
        "schema_version": 2,
        "baseline": baseline["label"], "candidate": candidate["label"],
        "verdict": verdict, "thresholds": configured,
        "condition_mismatches": mismatches, "regressions": regressions,
        "improvements": improvements, "overall": overall, "rows": rows,
        "conditions": {
            "baseline": baseline["conditions"],
            "candidate": candidate["conditions"],
        },
    }


def export_json(comparison: dict) -> str:
    return json.dumps(comparison, ensure_ascii=False, indent=2) + "\n"


def export_csv(comparison: dict) -> str:
    output = io.StringIO()
    fieldnames = [
        "task_id", "baseline_runs", "candidate_runs", "baseline_success_rate",
        "candidate_success_rate", "success_rate_delta_pp", "wall_delta_pct",
        "wall_p95_delta_pct", "token_delta_pct", "intervention_delta",
    ]
    writer = csv.DictWriter(output, fieldnames=fieldnames)
    writer.writeheader()
    for row in comparison["rows"]:
        writer.writerow({
            "task_id": row["task_id"],
            "baseline_runs": row["baseline"]["runs"],
            "candidate_runs": row["candidate"]["runs"],
            "baseline_success_rate": row["baseline"]["success_rate"],
            "candidate_success_rate": row["candidate"]["success_rate"],
            **{key: row[key] for key in fieldnames[5:]},
        })
    return output.getvalue()


def export_markdown(comparison: dict) -> str:
    lines = [
        f"# {comparison['baseline']} vs {comparison['candidate']}", "",
        f"Verdict: **{comparison['verdict']}**", "",
        "| Task | Success B→C | Wall Δ | p95 Δ | Token Δ | Intervention Δ |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in comparison["rows"]:
        before, after = row["baseline"], row["candidate"]
        value = lambda item: "—" if item is None else f"{item:+.2f}%"
        lines.append(
            f"| {row['task_id']} | {before['successes']}/{before['runs']} → "
            f"{after['successes']}/{after['runs']} | {value(row['wall_delta_pct'])} | "
            f"{value(row['wall_p95_delta_pct'])} | {value(row['token_delta_pct'])} | "
            f"{row['intervention_delta']:+.2f} |"
        )
    if comparison["condition_mismatches"]:
        lines.extend(["", "## Condition mismatches", ""])
        lines.extend(
            f"- {item['field']}: `{item['baseline']}` ≠ `{item['candidate']}`"
            for item in comparison["condition_mismatches"]
        )
    return "\n".join(lines) + "\n"
