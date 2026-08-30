"""워커 경유 vs 부모 직접 dispatch의 실측 비용 비교 리포트.

1-slot 로컬에서 워커의 가치는 병렬이 아니라 컨텍스트 격리다. 그 격리가 추가
prefill 비용을 상회하는지는 믿음이 아니라 dispatches.usage_json의 실측으로
판정한다. 읽기 전용 — DB를 변경하지 않는다.

    uv run python scripts/worker_value_report.py            # 기본 DB (~/.janus)
    uv run python scripts/worker_value_report.py <db-path>
    uv run python scripts/worker_value_report.py demo       # 자체 검증
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
from pathlib import Path
from statistics import median

TERMINAL_STATUSES = ("completed", "failed", "cancelled")
METRICS = ("prompt_tokens", "completion_tokens", "steps", "active_time_ms")


def default_db_path() -> str:
    return os.environ.get(
        "JANUS_DB_FILE", str(Path.home() / ".janus" / "janus.sqlite3")
    )


def load_rows(db_path: str) -> list[dict]:
    """종료된 dispatch의 usage를 평탄한 dict 리스트로 만든다."""
    uri = f"file:{db_path}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        cursor = conn.execute(
            "SELECT status, usage_json, budget_exhausted_reason FROM dispatches "
            f"WHERE status IN ({','.join('?' * len(TERMINAL_STATUSES))})",
            TERMINAL_STATUSES,
        )
        rows = []
        for status, usage_json, exhausted in cursor:
            try:
                usage = json.loads(usage_json or "{}")
            except json.JSONDecodeError:
                usage = {}
            rows.append({
                "status": status,
                "budget_exhausted": bool(exhausted),
                **{key: float(usage.get(key) or 0) for key in METRICS},
                "workers_started": int(usage.get("workers_started") or 0),
            })
        return rows


def summarize(rows: list[dict]) -> dict:
    """workers_started 유무로 갈라 그룹별 중앙값·비율을 계산한다."""
    groups = {"with_workers": [], "without_workers": []}
    for row in rows:
        key = "with_workers" if row["workers_started"] > 0 else "without_workers"
        groups[key].append(row)
    summary = {}
    for name, group in groups.items():
        if not group:
            summary[name] = {"n": 0}
            continue
        summary[name] = {
            "n": len(group),
            "completed_rate": sum(
                row["status"] == "completed" for row in group
            ) / len(group),
            "budget_exhausted_rate": sum(
                row["budget_exhausted"] for row in group
            ) / len(group),
            **{f"median_{key}": median(row[key] for row in group)
               for key in METRICS},
        }
    return summary


def render(summary: dict) -> str:
    lines = [f"{'metric':<28}{'with_workers':>16}{'without_workers':>18}"]
    with_group, without = summary["with_workers"], summary["without_workers"]

    def cell(group: dict, key: str) -> str:
        return f"{group[key]:.2f}" if key in group else "-"

    keys = ["n", "completed_rate", "budget_exhausted_rate"] + [
        f"median_{key}" for key in METRICS
    ]
    for key in keys:
        lines.append(f"{key:<28}{cell(with_group, key):>16}{cell(without, key):>18}")
    if not with_group["n"] or not without["n"]:
        lines.append(
            "\n한쪽 그룹이 비어 있어 비교가 불가능합니다. 워커 정책을 켠/끈 "
            "실사용 dispatch가 양쪽 다 쌓인 뒤 다시 실행하세요."
        )
    return "\n".join(lines)


def demo() -> None:
    rows = [
        {"status": "completed", "budget_exhausted": False, "prompt_tokens": 9000,
         "completion_tokens": 1200, "steps": 6, "active_time_ms": 40000,
         "workers_started": 1},
        {"status": "failed", "budget_exhausted": True, "prompt_tokens": 15000,
         "completion_tokens": 2000, "steps": 12, "active_time_ms": 90000,
         "workers_started": 2},
        {"status": "completed", "budget_exhausted": False, "prompt_tokens": 5000,
         "completion_tokens": 900, "steps": 5, "active_time_ms": 25000,
         "workers_started": 0},
    ]
    summary = summarize(rows)
    assert summary["with_workers"]["n"] == 2
    assert summary["without_workers"]["n"] == 1
    assert summary["with_workers"]["completed_rate"] == 0.5
    assert summary["with_workers"]["median_prompt_tokens"] == 12000
    assert summary["without_workers"]["budget_exhausted_rate"] == 0.0
    assert "median_steps" in render(summary)
    assert "비교가 불가능" not in render(summary)
    assert "비교가 불가능" in render(summarize(rows[:2]))
    print("OK — worker_value_report 집계 규칙 통과")


def main() -> int:
    arg = sys.argv[1] if len(sys.argv) > 1 else ""
    if arg == "demo":
        demo()
        return 0
    db_path = arg or default_db_path()
    if not Path(db_path).is_file():
        print(f"DB 파일이 없습니다: {db_path}", file=sys.stderr)
        return 1
    print(render(summarize(load_rows(db_path))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
