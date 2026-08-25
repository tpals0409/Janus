"""Janus operations 라우터 — shared.py에서 분리되었다."""

from __future__ import annotations

import json
from datetime import datetime

from fastapi import APIRouter

from .. import domain as D
from .. import scheduler as scheduler_mod
from .. import telemetry as telemetry_mod
from ..shared import (
    _dispatch_json,
    get_domain_store,
)

router = APIRouter()

def _budget_progress(dispatch: dict | None) -> dict:
    if dispatch is None:
        return {"tokens": 0.0, "steps": 0.0, "time": 0.0, "workers": 0.0, "peak": 0.0}
    budget = json.loads(dispatch["budget_json"])
    usage = json.loads(dispatch["usage_json"])
    values = {
        "tokens": (
            int(usage.get("prompt_tokens", 0)) + int(usage.get("completion_tokens", 0))
        ) / budget["dispatch"]["token_limit"],
        "steps": int(usage.get("steps", 0)) / budget["dispatch"]["step_limit"],
        "time": float(usage.get("active_time_ms", 0)) / budget["dispatch"]["time_limit_ms"],
        "workers": int(usage.get("workers_started", 0)) / budget["workers"]["total_limit"],
    }
    values = {key: round(max(0.0, value) * 100, 1) for key, value in values.items()}
    return {**values, "peak": max(values.values())}



def _operations_lane(task: dict, dispatch: dict | None) -> str:
    if task["status"] == "review":
        return "review"
    if task["status"] == "needs_you":
        return "idle" if task.get("attention_reason") == "conversation_idle" else "needs_you"
    if task["status"] == "failed" or (dispatch and dispatch["status"] == "failed"):
        return "failed"
    if dispatch and dispatch["status"] in {"running", "needs_you"}:
        return "working" if dispatch["status"] == "running" else "needs_you"
    if task["status"] == "working" and dispatch and dispatch["status"] != "queued":
        return "working"
    return "queue"



def _operations_timeline(
    store: D.DomainStore, task_id: str, session: dict | None,
) -> list[dict]:
    timeline: list[dict] = []
    if session is not None:
        for event in store.list_session_events(session["id"])[-60:]:
            payload = event["payload"]
            kind = str(payload.get("kind") or payload.get("type") or event["kind"])
            if kind.startswith("model_generation"):
                category = "generation"
            elif kind.startswith("tool_"):
                category = "tool"
            elif kind.startswith("resource_queue") or kind == "resource_lease_acquired":
                category = "queue"
            elif kind.startswith("worker_") or kind.startswith("span_"):
                category = "worker"
            else:
                continue
            timeline.append({
                "category": category, "kind": kind, "at": event["created_at"],
                "status": payload.get("status"), "label": payload.get("name"),
            })
    for run in store.list_verification_runs(task_id)[:12]:
        timeline.append({
            "category": "verification", "kind": run["kind"],
            "at": run["started_at"] or run["created_at"], "status": run["status"],
            "label": run["command"],
        })
    return sorted(timeline, key=lambda item: item["at"] or "")[-24:]



@router.get("/operations/dashboard")
def operations_dashboard(project_id: str | None = None):
    store = get_domain_store()
    projects = (
        [store.get_project(project_id)] if project_id
        else store.list_projects()
    )
    project_names = {project["id"]: project["name"] for project in projects}
    tasks = [
        task for project in projects
        for task in store.list_tasks(project["id"])
    ]
    rows = []
    for task in tasks:
        dispatch = store.latest_dispatch(task["id"])
        sessions = store.list_sessions(task["id"])
        session = sessions[0] if sessions else None
        lane = _operations_lane(task, dispatch)
        rows.append({
            "id": task["id"], "project_id": task["project_id"],
            "project_name": project_names[task["project_id"]],
            "title": task["title"], "status": task["status"], "lane": lane,
            "updated_at": task["updated_at"],
            "dispatch": _dispatch_json(dispatch) if dispatch else None,
            "session": ({
                "id": session["id"], "status": session["status"],
                "updated_at": session["updated_at"],
            } if session else None),
            "budget_progress": _budget_progress(dispatch),
            "timeline": _operations_timeline(store, task["id"], session),
            "attention": lane in {"needs_you", "review", "failed"},
        })
    lane_order = {
        "needs_you": 0, "failed": 1, "review": 2,
        "working": 3, "idle": 4, "queue": 5,
    }
    rows.sort(key=lambda item: (lane_order[item["lane"]], item["updated_at"]), reverse=False)
    scheduler = scheduler_mod.default_scheduler().snapshot()
    lane_counts = {
        lane: sum(row["lane"] == lane for row in rows)
        for lane in ("queue", "working", "idle", "needs_you", "review", "failed")
    }
    return {
        "generated_at": datetime.now().astimezone().isoformat(),
        "summary": {
            "total": len(rows), "attention": sum(row["attention"] for row in rows),
            "lanes": lane_counts,
        },
        "scheduler": scheduler,
        "memory": {
            "janus_process_peak_rss_bytes": telemetry_mod.process_peak_rss_bytes(),
        },
        "tasks": rows,
    }
