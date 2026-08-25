#!/usr/bin/env python3
"""Repeat crash/reopen/backup cycles to expose leaked transient state.

The release soak defaults to 30 minutes. CI and local review can use a short
duration while exercising the identical loop, for example:
`uv run python scripts/robustness_soak.py --duration-seconds 3 --minimum-cycles 10`.
"""

from __future__ import annotations

import argparse
import json
import sys
import tempfile
import time
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from janus_server.domain import DomainStore  # noqa: E402
from janus_server.recovery import create_database_backup, database_integrity  # noqa: E402


def run(duration_seconds: float, minimum_cycles: int, root: Path) -> dict:
    database = root / "janus.sqlite3"
    backups = root / "backups"
    store = DomainStore(database)
    project = store.create_project(name="P5 soak", repo_path=str(root / "repo"))
    started = time.monotonic()
    cycles = 0
    while cycles < minimum_cycles or time.monotonic() - started < duration_seconds:
        task = store.create_task(
            project_id=project["id"], title=f"cycle {cycles}",
            objective="survive a simulated process crash", acceptance_command="true",
            base_ref="main",
        )
        workspace = store.create_workspace(
            task_id=task["id"], repo_path=project["repo_path"], base_ref="main",
        )
        store.transition_workspace(
            workspace["id"], "ready", root_path=str(root / f"workspace-{cycles}"),
            branch_name=f"janus/soak-{cycles}",
        )
        store.transition_task(task["id"], "working", expected="todo")
        dispatch = store.create_dispatch(
            task_id=task["id"], workspace_id=workspace["id"],
            agent_profile_id="agent_default",
        )
        store.transition_dispatch(dispatch["id"], "running", expected="queued")
        session = store.create_session(
            task_id=task["id"], dispatch_id=dispatch["id"],
            agent_profile_id="agent_default",
        )
        store.transition_session(session["id"], "running")

        store = DomainStore(database)
        recovered = store.recover_interrupted_runtime()
        assert recovered["sessions"] == 1
        assert recovered["dispatches"] == 1
        assert store.get_session(session["id"])["status"] == "idle"
        assert store.get_dispatch(dispatch["id"])["status"] == "needs_you"
        assert store.get_task(task["id"])["status"] == "needs_you"
        cycles += 1
        if cycles % 25 == 0:
            create_database_backup(database, backups, retain=3)

    with store._connect() as connection:
        transient = {
            "running_sessions": connection.execute(
                "SELECT count(*) FROM agent_sessions WHERE status='running'"
            ).fetchone()[0],
            "running_dispatches": connection.execute(
                "SELECT count(*) FROM dispatches WHERE status='running'"
            ).fetchone()[0],
            "preparing_workspaces": connection.execute(
                "SELECT count(*) FROM workspaces WHERE state='preparing'"
            ).fetchone()[0],
        }
    assert not any(transient.values()), transient
    integrity = database_integrity(database)
    assert integrity["ok"], integrity
    return {
        "cycles": cycles,
        "duration_seconds": round(time.monotonic() - started, 3),
        "integrity": integrity,
        "transient": transient,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--duration-seconds", type=float, default=1800)
    parser.add_argument("--minimum-cycles", type=int, default=100)
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.duration_seconds < 0 or args.minimum_cycles < 1:
        parser.error("duration은 0 이상, minimum-cycles는 1 이상이어야 합니다")
    if args.root:
        args.root.mkdir(parents=True, exist_ok=True)
        report = run(args.duration_seconds, args.minimum_cycles, args.root.resolve())
    else:
        with tempfile.TemporaryDirectory(prefix="janus-p5-soak-") as temporary:
            report = run(args.duration_seconds, args.minimum_cycles, Path(temporary))
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
