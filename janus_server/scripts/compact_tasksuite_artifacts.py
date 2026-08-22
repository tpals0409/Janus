#!/usr/bin/env python3
"""기존 TaskSuite 결과에서 중복 prompt/text delta를 제거하되 계측 구간은 보존한다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from run_tasksuite_v0 import aggregate_run_result, compact_run_result


def atomic_write(path: Path, value: dict) -> None:
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("artifact_dirs", nargs="+", type=Path)
    args = parser.parse_args()
    for artifact_dir in args.artifact_dirs:
        result_path = artifact_dir / "result.json"
        report = json.loads(result_path.read_text(encoding="utf-8"))
        compact_runs = []
        for run_path in sorted((artifact_dir / "runs").glob("*/*/*/run.json")):
            run = compact_run_result(json.loads(run_path.read_text(encoding="utf-8")))
            atomic_write(run_path, run)
            compact_runs.append(run)
        if compact_runs:
            by_key = {
                (run["task_id"], run["policy"], run["repeat"]): run for run in compact_runs
            }
            report["runs"] = [
                aggregate_run_result(by_key.get(
                    (run["task_id"], run["policy"], run["repeat"]), compact_run_result(run)
                ))
                for run in report.get("runs", [])
            ]
        else:
            report["runs"] = [
                aggregate_run_result(compact_run_result(run)) for run in report.get("runs", [])
            ]
        atomic_write(result_path, report)
        print(f"compacted {artifact_dir}: {len(report['runs'])} runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
