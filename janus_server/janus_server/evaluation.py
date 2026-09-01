"""Reproducible AgentProfile A/B evaluation and regression decisions."""

from __future__ import annotations

import csv
import io
import json
import math
import re
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
# 노이즈 게이트: 평균 차이가 baseline 표준편차의 이 배수를 넘어야 회귀로 본다.
# 실행 3회짜리 비교에서 한 번 튄 값이 회귀로 찍히는 것을 막는다.
NOISE_SIGMA = 1.0
_VERSION_PART = re.compile(r"[0-9][0-9.]*")


def coarse_platform(value: object) -> str:
    """OS 패치 레벨을 뺀 비교 키.

    `platform.platform()`은 'macOS-26.6.2-arm64-arm-64bit'처럼 빌드 번호를 품는다.
    그대로 비교하면 OS 마이너 업데이트 한 번에 저장된 모든 baseline이
    incomparable_conditions가 된다. 아키텍처 차이는 남기고 버전만 접는다.
    """
    text = str(value or "")
    parts = [part for part in text.split("-") if not _VERSION_PART.fullmatch(part)]
    return "-".join(parts) or text


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


def _exceeds_noise(row: dict, mean_key: str, stdev_key: str) -> bool:
    """평균 차이가 baseline 산포를 실제로 넘는가.

    metrics()가 표준편차를 계산해 두고도 비교에서 안 써서, 실행 3회 중 한 번
    튄 값이 그대로 회귀로 찍혔다 — 표준편차가 존재하는 이유가 바로 그 경우다.
    산포가 0이면(완전 재현) 어떤 차이든 실재로 본다.
    """
    stdev = float(row["baseline"].get(stdev_key) or 0.0)
    if stdev <= 0:
        return True
    change = abs(
        float(row["candidate"][mean_key]) - float(row["baseline"][mean_key])
    )
    return change > stdev * NOISE_SIGMA


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

    def comparable_value(conditions: dict, field: str) -> str:
        raw = conditions.get(field)
        return coarse_platform(raw) if field == "platform" else str(raw or "")

    mismatches = [
        {
            "field": field, "baseline": baseline["conditions"].get(field),
            "candidate": candidate["conditions"].get(field),
        }
        for field in COMPARABILITY_FIELDS
        if comparable_value(baseline["conditions"], field)
        != comparable_value(candidate["conditions"], field)
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
    # 임계는 넘었지만 baseline 산포 안에 있는 항목 — 버리지 않고 따로 보고한다.
    noise: list[dict] = []
    for scope, row in [("overall", overall)] + [
        (item["task_id"], item) for item in rows
    ]:
        if row["success_rate_delta_pp"] < -configured["max_success_rate_drop_pp"]:
            regressions.append({
                "scope": scope, "metric": "success_rate",
                "delta": row["success_rate_delta_pp"],
            })
        for metric, threshold_key, mean_key, stdev_key in (
            ("wall_delta_pct", "max_wall_regression_pct",
             "wall_mean_ms", "wall_stdev_ms"),
            ("token_delta_pct", "max_token_regression_pct",
             "tokens_mean", "tokens_stdev"),
        ):
            delta = row[metric]
            if delta is None:
                continue
            if delta > configured[threshold_key]:
                if _exceeds_noise(row, mean_key, stdev_key):
                    regressions.append(
                        {"scope": scope, "metric": metric, "delta": delta})
                else:
                    noise.append({
                        "scope": scope, "metric": metric, "delta": delta,
                        "baseline_stdev": row["baseline"][stdev_key],
                    })
            if delta <= -configured["min_improvement_pct"]:
                improvements.append({"scope": scope, "metric": metric, "delta": delta})
        if row["intervention_delta"] > configured["max_intervention_increase"]:
            if _exceeds_noise(row, "interventions_mean", "interventions_stdev"):
                regressions.append({
                    "scope": scope, "metric": "interventions",
                    "delta": row["intervention_delta"],
                })
            else:
                noise.append({
                    "scope": scope, "metric": "interventions",
                    "delta": row["intervention_delta"],
                    "baseline_stdev": row["baseline"]["interventions_stdev"],
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
        "within_noise": noise,
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
    delta_fields = [
        "success_rate_delta_pp", "wall_delta_pct",
        "wall_p95_delta_pct", "token_delta_pct", "intervention_delta",
    ]
    # 산포와 워커·메모리 평균은 metrics()가 이미 계산한다. 내보내지 않으면
    # 델타 하나가 노이즈인지 실재인지 CSV만 보고는 판단할 수 없다.
    summary_fields = [
        "wall_stdev_ms", "tokens_stdev", "interventions_stdev",
        "worker_count_mean", "memory_peak_bytes_mean",
    ]
    fieldnames = [
        "task_id", "baseline_runs", "candidate_runs", "baseline_success_rate",
        "candidate_success_rate", *delta_fields,
        *(f"baseline_{key}" for key in summary_fields),
        *(f"candidate_{key}" for key in summary_fields),
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
            **{key: row[key] for key in delta_fields},
            **{f"baseline_{key}": row["baseline"][key] for key in summary_fields},
            **{f"candidate_{key}": row["candidate"][key] for key in summary_fields},
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

        def format_delta(item: float | None) -> str:
            return "—" if item is None else f"{item:+.2f}%"

        lines.append(
            f"| {row['task_id']} | {before['successes']}/{before['runs']} → "
            f"{after['successes']}/{after['runs']} | {format_delta(row['wall_delta_pct'])} | "
            f"{format_delta(row['wall_p95_delta_pct'])} | {format_delta(row['token_delta_pct'])} | "
            f"{row['intervention_delta']:+.2f} |"
        )
    if comparison["condition_mismatches"]:
        lines.extend(["", "## Condition mismatches", ""])
        lines.extend(
            f"- {item['field']}: `{item['baseline']}` ≠ `{item['candidate']}`"
            for item in comparison["condition_mismatches"]
        )
    return "\n".join(lines) + "\n"
