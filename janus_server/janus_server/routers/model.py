"""로컬 모델 셋업 라우터 — 진단·다운로드·취소.

workspaces.py의 잡 계약과 같다: 202 + job_active, 단일 실행 가드, 명시적 재시도.
진행률은 EventBus 토픽 `model`로 나간다.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

from .. import model_setup
from ..shared import _publish_change

router = APIRouter()

_downloader = model_setup.ModelDownloader(publish=_publish_change)


def _catalog() -> list[dict]:
    return [
        {"id": model_id, "repo": entry["repo"]}
        for model_id, entry in model_setup.MODEL_CATALOG.items()
    ]


@router.get("/model/status")
def model_status():
    """모델이 있는지·용량이 되는지. Electron이 준 경로를 그대로 쓴다."""
    return {
        "catalog": _catalog(),
        "disk": model_setup.disk(),
        "job": _downloader.snapshot(),
        "job_active": _downloader.active(),
    }


@router.get("/model/plan")
def model_plan(model_id: str = model_setup.DEFAULT_MODEL_ID):
    """받아야 할 총량. 네트워크는 쓰되 내려받지는 않는다(hf --dry-run)."""
    try:
        model = model_setup.plan(model_id)
        draft = model_setup._plan_repo(model_setup.DRAFT_REPO, None)
    except Exception as error:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=str(error)) from error
    usage = model_setup.disk()
    needed = model["total_bytes"] + draft["total_bytes"]
    return {
        "model": model,
        "draft": draft,
        "total_bytes": needed,
        "disk": usage,
        "enough_space": usage["free_bytes"] >= needed + model_setup.DISK_HEADROOM_BYTES,
    }


@router.post("/model/download", status_code=202)
def model_download(payload: dict | None = None):
    model_id = str((payload or {}).get("model_id") or model_setup.DEFAULT_MODEL_ID)
    with_draft = bool((payload or {}).get("with_draft", True))
    try:
        job = _downloader.start(model_id, with_draft=with_draft)
    except ValueError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    except RuntimeError as error:
        # 이미 진행 중이거나 디스크 부족 — 둘 다 사용자가 고칠 수 있는 상태다.
        raise HTTPException(status_code=409, detail=str(error)) from error
    _publish_change("model", "started", **job)
    return {"job": job, "job_active": True}


@router.post("/model/cancel")
def model_cancel():
    job = _downloader.cancel()
    if job is None:
        raise HTTPException(status_code=404, detail="진행 중인 다운로드가 없습니다")
    return {"job": job, "job_active": _downloader.active()}
