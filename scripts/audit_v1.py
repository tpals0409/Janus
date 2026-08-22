#!/usr/bin/env python3
"""Verify committed real-model evidence and release metadata for the v1 gate."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(relative: str) -> dict:
    return json.loads((ROOT / relative).read_text(encoding="utf-8"))


def successes(result: dict) -> int:
    return sum(bool(run.get("acceptance_passed")) for run in result["runs"])


def main() -> int:
    baseline = load("janus_server/artifacts/p0/tasksuite/20260822-115844/result.json")
    smoke = load("janus_server/artifacts/p0/smoke/20260822-125259/result.json")
    scheduler = load("janus_server/artifacts/r3/tasksuite/20260822-183500/result.json")
    fixed = load(
        "janus_server/artifacts/r3/tasksuite/20260822-p2-final-fixed-one-v2/result.json"
    )

    assert baseline["status"] == "completed" and len(baseline["runs"]) == 45
    assert baseline["conditions"]["model"] == "qwen3.8-27b"
    assert baseline["conditions"]["quantization"] == "4-bit MLX"
    assert baseline["conditions"]["repeats"] == 5
    assert smoke["status"] == "passed"
    assert all(item["status"] == "passed" for item in smoke["scenarios"].values())
    assert smoke["model_server"]["orphan_processes"] == 0
    assert scheduler["status"] == "completed" and len(scheduler["runs"]) == 45
    assert fixed["status"] == "completed" and len(fixed["runs"]) == 15
    assert fixed["model_server"]["orphan_processes"] == 0

    desktop = json.loads((ROOT / "janus/package.json").read_text(encoding="utf-8"))
    backend = tomllib.loads(
        (ROOT / "janus_server/pyproject.toml").read_text(encoding="utf-8")
    )
    version_source = (ROOT / "janus_server/janus_server/version.py").read_text(
        encoding="utf-8"
    )
    version = desktop["version"]
    assert backend["project"]["version"] == version
    assert f'__version__ = "{version}"' in version_source

    report = {
        "version": version,
        "actual_model": baseline["conditions"]["model"],
        "quantization": baseline["conditions"]["quantization"],
        "baseline": {"runs": 45, "acceptance": successes(baseline)},
        "scheduler_candidate": {"runs": 45, "acceptance": successes(scheduler)},
        "fixed_one_final": {"runs": 15, "acceptance": successes(fixed)},
        "smoke_scenarios": sorted(smoke["scenarios"]),
        "owned_model_orphans": smoke["model_server"]["orphan_processes"],
        "status": "passed",
    }
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
