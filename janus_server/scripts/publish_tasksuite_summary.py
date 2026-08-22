#!/usr/bin/env python3
"""로컬 TaskSuite 결과에서 공개 가능한 집계 artifact만 만든다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


PUBLIC_RUN_KEYS = {
    "schema_version", "task_id", "category", "policy", "repeat", "status",
    "acceptance_passed", "policy_conformant", "turn_error", "wall_time_ms",
    "changed_files", "missing_required_files", "unexpected_changed_files",
    "worker_count", "approval_requests", "user_inputs", "tokens", "budget",
    "timing_ms", "efficiency",
}


def public_report(report: dict) -> dict:
    conditions = {
        key: value for key, value in (report.get("conditions") or {}).items()
        if key != "model_path"
    }
    model_server = {
        key: value for key, value in (report.get("model_server") or {}).items()
        if key != "pid"
    }
    runs = [
        {key: value for key, value in run.items() if key in PUBLIC_RUN_KEYS}
        for run in report.get("runs") or []
    ]
    return {
        "schema_version": report.get("schema_version", 1),
        "suite": report.get("suite"),
        "label": report.get("label"),
        "started_at": report.get("started_at"),
        "finished_at": report.get("finished_at"),
        "status": report.get("status"),
        "conditions": conditions,
        "model_server": model_server,
        "runs": runs,
        "summary": report.get("summary") or [],
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("result", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = public_report(json.loads(args.result.read_text(encoding="utf-8")))
    output = args.output or args.result
    encoded = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if "/Users/" in encoded or "workspace_root" in encoded or "model_path" in encoded:
        raise ValueError("공개 report에 로컬 경로가 남았습니다")
    output.write_text(encoded, encoding="utf-8")
    print(f"published summary: {output} ({len(report['runs'])} runs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
