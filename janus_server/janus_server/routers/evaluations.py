"""Janus evaluations 라우터 — server.py에서 분리되었다."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from .. import domain as D
from .. import evaluation, runtime, server
from ..server import (
    _evaluation_comparison_json,
    _publish_change,
    get_domain_store,
)

router = APIRouter()

def _evaluation_experiment_json(item: dict) -> dict:
    value = dict(item)
    for source, target in (
        ("profile_snapshot_json", "profile_snapshot"),
        ("config_json", "config"), ("conditions_json", "conditions"),
        ("report_json", "report"),
    ):
        raw = value.pop(source, None)
        value[target] = json.loads(raw) if raw else None
    return value



def _evaluation_profile_snapshot(profile_id: str) -> dict:
    store = get_domain_store()
    profile = store.get_agent_profile(profile_id)
    model = store.get_model_profile(profile["model_profile_id"])
    if model["provider"] != "local":
        raise D.Conflict("P4 Evaluation Lab은 현재 local model profile만 실행합니다")
    return {
        "id": profile["id"], "name": profile["name"],
        # Evaluations must snapshot what the model actually receives. The profile's
        # custom prompt may intentionally be empty because Janus is always prepended.
        "system_prompt": runtime.persona_prompt(
            "janus", custom_prompt=str(profile.get("system_prompt") or ""),
        ),
        "custom_system_prompt": profile["system_prompt"],
        "tools": json.loads(profile["tools_json"]), "approval": profile["approval"],
        "worker_policy": profile["worker_policy"], "max_steps": profile["max_steps"],
        "budget": json.loads(profile["budget_json"]),
        "model_profile_id": model["id"], "model_key": model["model_key"],
        "quantization": model["quantization"],
        "model_config": json.loads(model["config_json"]),
    }



def _evaluation_run_config(body: dict) -> dict:
    manifest_path = Path(__file__).resolve().parents[2] / "tasksuite" / "v0" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    available = {item["id"] for item in manifest["tasks"]}
    tasks = body.get("tasks") or sorted(available)
    if not isinstance(tasks, list) or not tasks or len(tasks) != len(set(tasks)):
        raise D.Conflict("tasks는 중복 없는 하나 이상의 배열이어야 합니다")
    tasks = [str(item) for item in tasks]
    unknown = sorted(set(tasks) - available)
    if unknown:
        raise D.Conflict(f"모르는 TaskSuite task: {unknown}")
    try:
        repeats = int(body.get("repeats", manifest["repeats"]))
        turn_timeout = float(body.get("turn_timeout_seconds", 180))
        startup_timeout = float(body.get("model_startup_timeout_seconds", 240))
    except (TypeError, ValueError) as error:
        raise D.Conflict("repeats/timeout은 숫자여야 합니다") from error
    if not 1 <= repeats <= 20:
        raise D.Conflict("repeats는 1~20 사이여야 합니다")
    if not 30 <= turn_timeout <= 900 or not 30 <= startup_timeout <= 900:
        raise D.Conflict("timeout은 30~900초 사이여야 합니다")
    return {
        "tasks": tasks, "repeats": repeats,
        "turn_timeout_seconds": turn_timeout,
        "model_startup_timeout_seconds": startup_timeout,
    }



def _evaluation_root() -> Path:
    return Path(
        os.environ.get(
            "JANUS_EVALUATIONS_DIR", str(Path.home() / ".janus" / "evaluations")
        )
    ).expanduser().resolve()



def _run_evaluation_job(experiment_id: str) -> None:
    store = get_domain_store()
    process: subprocess.Popen[str] | None = None
    output_dir = _evaluation_root() / experiment_id
    try:
        item = store.start_evaluation_experiment(experiment_id)
        _publish_change(
            "evaluation", "running", experiment_id=experiment_id, status="running",
        )
        with server._EVALUATION_JOBS_LOCK:
            cancelled_before_start = experiment_id in server._EVALUATION_CANCELLED
        if cancelled_before_start:
            store.finish_evaluation_experiment(
                experiment_id, status="cancelled", error="cancelled by user"
            )
            return
        profile = json.loads(item["profile_snapshot_json"])
        config = json.loads(item["config_json"])
        root = _evaluation_root()
        root.mkdir(parents=True, exist_ok=True)
        profile_path = root / f".{experiment_id}-profile.json"
        profile_path.write_text(
            json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        project_dir = Path(__file__).resolve().parents[2]
        command = [
            sys.executable, str(project_dir / "scripts" / "run_tasksuite_v0.py"),
            "--label", item["label"], "--profile-json", str(profile_path),
            "--output-dir", str(output_dir), "--repeats", str(config["repeats"]),
            "--turn-timeout", str(config["turn_timeout_seconds"]),
            "--model-startup-timeout", str(config["model_startup_timeout_seconds"]),
            "--tasks", *config["tasks"],
        ]
        process = subprocess.Popen(
            command, cwd=project_dir, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True,
        )
        with server._EVALUATION_JOBS_LOCK:
            server._EVALUATION_PROCESSES[experiment_id] = process
        output, _ = process.communicate()
        (root / f"{experiment_id}.log").write_text(output[-200_000:], encoding="utf-8")
        result_path = output_dir / "result.json"
        report = (
            json.loads(result_path.read_text(encoding="utf-8"))
            if result_path.is_file() else None
        )
        with server._EVALUATION_JOBS_LOCK:
            cancelled = experiment_id in server._EVALUATION_CANCELLED
        if cancelled:
            store.finish_evaluation_experiment(
                experiment_id, status="cancelled", report=report,
                conditions=report.get("conditions") if report else None,
                result_path=str(result_path) if report else None,
                error="cancelled by user",
            )
        elif process.returncode == 0 and report is not None:
            validated = evaluation.validate_report(report)
            store.finish_evaluation_experiment(
                experiment_id, status="completed", report=validated,
                conditions=validated["conditions"], result_path=str(result_path),
            )
        else:
            store.finish_evaluation_experiment(
                experiment_id, status="failed", report=report,
                conditions=report.get("conditions") if report else None,
                result_path=str(result_path) if report else None,
                error=f"TaskSuite runner exit {process.returncode}",
            )
    except Exception as error:
        try:
            current = store.get_evaluation_experiment(experiment_id)
            if current["status"] in {"queued", "running"}:
                store.finish_evaluation_experiment(
                    experiment_id, status="failed",
                    error=f"{type(error).__name__}: {error}",
                )
        except D.DomainError:
            pass
    finally:
        try:
            final_status = store.get_evaluation_experiment(experiment_id)["status"]
        except D.DomainError:
            final_status = "unknown"
        _publish_change(
            "evaluation", "finished", experiment_id=experiment_id,
            status=final_status,
        )
        profile_path = _evaluation_root() / f".{experiment_id}-profile.json"
        profile_path.unlink(missing_ok=True)
        with server._EVALUATION_JOBS_LOCK:
            server._EVALUATION_PROCESSES.pop(experiment_id, None)
            server._EVALUATION_CANCELLED.discard(experiment_id)
            if server._EVALUATION_JOBS.get(experiment_id) is threading.current_thread():
                server._EVALUATION_JOBS.pop(experiment_id, None)



def _start_evaluation_job(experiment_id: str) -> None:
    with server._EVALUATION_JOBS_LOCK:
        thread = threading.Thread(
            target=_run_evaluation_job, args=(experiment_id,),
            name=f"janus-evaluation-{experiment_id}", daemon=True,
        )
        server._EVALUATION_JOBS[experiment_id] = thread
        thread.start()



@router.get("/evaluations/experiments")
def list_evaluation_experiments():
    return [
        _evaluation_experiment_json(item)
        for item in get_domain_store().list_evaluation_experiments()
    ]



@router.get("/evaluations/experiments/{experiment_id}")
def get_evaluation_experiment(experiment_id: str):
    return _evaluation_experiment_json(
        get_domain_store().get_evaluation_experiment(experiment_id)
    )



@router.post("/evaluations/experiments/import")
def import_evaluation_experiment(body: dict):
    role = str(body.get("role") or "")
    if role not in {"baseline", "candidate"}:
        raise D.Conflict("Evaluation role은 baseline 또는 candidate여야 합니다")
    try:
        report = evaluation.validate_report(body.get("report"))
    except evaluation.EvaluationError as error:
        raise HTTPException(400, str(error)) from error
    item = get_domain_store().create_evaluation_experiment(
        role=role, label=str(report["label"]), source="import", status="completed",
        conditions=report["conditions"], report=report,
        profile_snapshot=report["conditions"].get("agent_profile") or {},
        config={"imported": True},
    )
    return _evaluation_experiment_json(item)



@router.post("/evaluations/experiments/run", status_code=202)
def run_evaluation_experiment(body: dict):
    role = str(body.get("role") or "")
    if role not in {"baseline", "candidate"}:
        raise D.Conflict("Evaluation role은 baseline 또는 candidate여야 합니다")
    label = str(body.get("label") or "").strip()
    if not label:
        raise D.Conflict("Evaluation label이 필요합니다")
    profile_id = str(body.get("agent_profile_id") or "")
    profile = _evaluation_profile_snapshot(profile_id)
    if profile["model_key"] != "qwen3.8-27b":
        raise D.Conflict("현재 TaskSuite model server는 qwen3.8-27b profile만 실행합니다")
    config = _evaluation_run_config(body)
    item = get_domain_store().create_evaluation_experiment(
        role=role, label=label, source="runner", status="queued",
        agent_profile_id=profile_id, profile_snapshot=profile, config=config,
        conditions={
            "model": profile["model_key"], "quantization": profile["quantization"],
            "agent_profile": profile,
        },
    )
    _start_evaluation_job(item["id"])
    return _evaluation_experiment_json(item)



@router.post("/evaluations/experiments/{experiment_id}/cancel")
def cancel_evaluation_experiment(experiment_id: str):
    item = get_domain_store().get_evaluation_experiment(experiment_id)
    if item["status"] not in {"queued", "running"}:
        raise D.Conflict(f"취소할 수 없는 Evaluation 상태: {item['status']}")
    with server._EVALUATION_JOBS_LOCK:
        server._EVALUATION_CANCELLED.add(experiment_id)
        process = server._EVALUATION_PROCESSES.get(experiment_id)
    if process is not None and process.poll() is None:
        process.send_signal(signal.SIGINT)
    return {"id": experiment_id, "cancellation_requested": True}



@router.get("/evaluations/comparisons")
def list_evaluation_comparisons():
    return [
        _evaluation_comparison_json(item)
        for item in get_domain_store().list_evaluation_comparisons()
    ]



@router.post("/evaluations/comparisons")
def create_evaluation_comparison(body: dict):
    store = get_domain_store()
    baseline = store.get_evaluation_experiment(str(body.get("baseline_id") or ""))
    candidate = store.get_evaluation_experiment(str(body.get("candidate_id") or ""))
    if baseline["role"] != "baseline" or candidate["role"] != "candidate":
        raise D.Conflict("baseline/candidate role이 올바른 experiment를 선택하세요")
    if baseline["status"] != "completed" or candidate["status"] != "completed":
        raise D.Conflict("완료된 experiment만 비교할 수 있습니다")
    thresholds = body.get("thresholds") or {}
    try:
        result = evaluation.compare(
            json.loads(baseline["report_json"]), json.loads(candidate["report_json"]),
            thresholds,
        )
    except evaluation.EvaluationError as error:
        raise HTTPException(400, str(error)) from error
    comparison = store.create_evaluation_comparison(
        baseline_experiment_id=baseline["id"],
        candidate_experiment_id=candidate["id"],
        thresholds=result["thresholds"], result=result,
    )
    return _evaluation_comparison_json(comparison)



@router.get("/evaluations/comparisons/{comparison_id}/export")
def export_evaluation_comparison(comparison_id: str, format: str = "json"):
    item = _evaluation_comparison_json(
        get_domain_store().get_evaluation_comparison(comparison_id)
    )
    exporters = {
        "json": (evaluation.export_json, "application/json", "json"),
        "csv": (evaluation.export_csv, "text/csv; charset=utf-8", "csv"),
        "markdown": (evaluation.export_markdown, "text/markdown; charset=utf-8", "md"),
    }
    if format not in exporters:
        raise HTTPException(400, "format은 json/csv/markdown 중 하나여야 합니다")
    exporter, media_type, suffix = exporters[format]
    return Response(
        exporter(item["result"]), media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="evaluation-{comparison_id}.{suffix}"'
        },
    )
