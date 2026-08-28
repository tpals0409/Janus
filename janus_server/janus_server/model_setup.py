"""로컬 모델 셋업 — 진단·용량 계산·다운로드.

터미널에서 `bootstrap_macos.sh --with-model`을 돌리게 하는 대신 앱 안에서 끝낸다.

경로는 Electron이 정해서 env로 내려준다(JANUS_HF_HUB_ROOT / JANUS_MODEL_*). 캐시 루트를
양쪽이 따로 계산하면 HF_HOME을 쓰는 사용자에게 "다운로드는 됐는데 앱은 없다고 한다"가
생긴다 — 실제로 그 버그가 있었다.

진행률은 hf stdout을 파싱하지 않고 캐시 디렉터리 크기로 잰다. hf CLI에는 스트리밍
진행률 포맷이 없고(--format은 auto/human/agent/json/quiet), tqdm 파싱은 부서지기 쉽다.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# UI의 모델 id -> HuggingFace repo와 스냅샷 안 하위 경로.
# repo마다 배치가 다르다 — mlx-community는 safetensors가 repo 루트에, orcarouter는
# `4-bit/` 아래에 있다. include 패턴도 여기서 나온다.
MODEL_CATALOG: dict[str, dict[str, str | None]] = {
    "qwen3.8-27b": {
        "repo": "mlx-community/Qwen3.8-27B-4bit",
        "subpath": "",
        "include": None,
    },
    "qwen3.8-27b-uncensored": {
        "repo": "orcarouter/Qwen3.8-27B-Uncensored-MLX",
        "subpath": "4-bit",
        "include": "4-bit/*",
    },
}
DRAFT_MODEL_ID = "qwen3.8-27b-mtp"
DRAFT_REPO = "mlx-community/Qwen3.8-27B-MTP-4bit"
DEFAULT_MODEL_ID = "qwen3.8-27b"
# 받은 뒤에도 로드·KV 캐시에 쓸 자리가 필요하다. 딱 맞게 비어 있으면 받자마자 못 쓴다.
DISK_HEADROOM_BYTES = 8 * 1024**3
# ETA를 말하기 전에 필요한 최소 신호 — 아래 근거는 view()에.
ETA_MIN_ELAPSED_S = 30.0
ETA_MIN_FRACTION = 0.02
_SIZE_UNITS = {"K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def parse_size(value: str) -> int:
    """hf --dry-run이 주는 "5.3G" / "632.0" / "20.0M"을 바이트로.

    이미 캐시된 파일은 크기가 "-"로 온다 — 받을 게 없다는 뜻이라 0이 맞다.
    알 수 없는 값에 예외를 던지면 "내려받기"를 누른 사용자가 파이썬 오류를 본다.
    """
    text = str(value).strip()
    if not text or text == "-":
        return 0
    unit = text[-1].upper()
    try:
        if unit in _SIZE_UNITS:
            return int(float(text[:-1]) * _SIZE_UNITS[unit])
        return int(float(text))
    except ValueError:
        return 0


def hub_root() -> Path:
    """hf CLI와 같은 우선순위. Electron이 정한 값이 있으면 그게 이긴다."""
    override = os.environ.get("JANUS_HF_HUB_ROOT")
    if override:
        return Path(override).expanduser()
    cache = os.environ.get("HF_HUB_CACHE")
    if cache:
        return Path(cache).expanduser()
    home = os.environ.get("HF_HOME")
    if home:
        return Path(home).expanduser() / "hub"
    return Path.home() / ".cache" / "huggingface" / "hub"


def repo_dir(repo: str) -> Path:
    return hub_root() / f"models--{repo.replace('/', '--')}"


def _dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for entry in path.rglob("*"):
        try:
            if entry.is_file() or entry.is_symlink():
                total += entry.lstat().st_size
        except OSError:
            continue  # 다운로드 중 사라지는 임시 파일
    return total


def _model_runtime() -> tuple[str, dict[str, str]]:
    """hf가 사는 uv 프로젝트. janus_server에는 huggingface_hub가 없다."""
    root = os.environ.get("JANUS_MODEL_RUNTIME_ROOT")
    if not root:
        raise RuntimeError(
            "모델 런타임 경로를 모릅니다. Janus 앱에서 실행하면 자동으로 설정됩니다."
        )
    env = dict(os.environ)
    environment = os.environ.get("JANUS_MODEL_ENVIRONMENT")
    if environment:
        env["UV_PROJECT_ENVIRONMENT"] = environment
    # 받는 곳과 찾는 곳을 같은 값으로 못박는다. 이걸 안 넘기면 hf는 제 기본 위치에
    # 받고 Janus는 hub_root()를 보므로, 16GB를 받고도 "모델 없음"이 된다.
    env["HF_HUB_CACHE"] = str(hub_root())
    return root, env


def plan(model_id: str) -> dict:
    """받아야 할 파일 목록과 총량. 네트워크는 쓰지만 내려받지는 않는다."""
    entry = MODEL_CATALOG.get(model_id) or MODEL_CATALOG[DEFAULT_MODEL_ID]
    return _plan_repo(str(entry["repo"]), entry["include"])


def classify_hf_failure(stderr: str, repo: str) -> str:
    """hf 실패를 사람이 읽는 한 문장으로. 트레이스백 꼬리를 UI에 흘리지 않는다."""
    text = (stderr or "").lower()
    if any(hint in text for hint in (
        "internet connection", "connectionerror", "max retries", "name or service not known",
        "temporary failure in name resolution", "failed to establish",
    )):
        return "네트워크에 연결할 수 없습니다. 인터넷 연결을 확인한 뒤 다시 시도하세요."
    if any(hint in text for hint in ("401", "gated", "authentication", "unauthorized", "access to model")):
        return (
            f"{repo}는 접근 권한이 필요한 모델입니다. 터미널에서 `hf auth login`으로 "
            "HuggingFace 계정에 로그인한 뒤 다시 시도하세요."
        )
    if "404" in text or "repositorynotfound" in text or "not found" in text:
        return f"{repo}를 찾을 수 없습니다. 모델 이름이 바뀌었거나 삭제됐을 수 있습니다."
    if "no space left" in text or "enospc" in text:
        return "디스크 공간이 부족합니다."
    tail = " ".join((stderr or "").split())[-200:]
    return f"모델 정보를 가져오지 못했습니다 ({repo}). {tail}".strip()


def _plan_repo(repo: str, include: str | None) -> dict:
    root, env = _model_runtime()
    command = ["uv", "run", "--frozen", "hf", "download", repo, "--dry-run", "--json"]
    if include:
        command += ["--include", include]
    try:
        result = subprocess.run(
            command, cwd=root, env=env, capture_output=True, text=True, timeout=120,
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError(
            "HuggingFace 응답이 없습니다. 네트워크를 확인한 뒤 다시 시도하세요."
        ) from error
    if result.returncode != 0:
        raise RuntimeError(classify_hf_failure(result.stderr, repo))
    files = json.loads(result.stdout or "[]")
    return {
        "repo": repo,
        "files": len(files),
        "total_bytes": sum(parse_size(item.get("size", "0")) for item in files),
    }


def disk() -> dict:
    root = hub_root()
    probe = root if root.exists() else root.parent
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    usage = shutil.disk_usage(probe)
    return {"free_bytes": usage.free, "total_bytes": usage.total, "path": str(root)}


@dataclass
class DownloadJob:
    model_id: str
    repo: str
    total_bytes: int
    baseline_bytes: int
    status: str = "running"  # running | completed | failed | cancelled
    error: str | None = None
    downloaded_bytes: int = 0
    started_at: float = field(default_factory=time.monotonic)
    process: subprocess.Popen | None = None
    cancelled: bool = False

    def view(self) -> dict:
        elapsed = time.monotonic() - self.started_at
        remaining = max(0, self.total_bytes - self.downloaded_bytes)
        # 초반 표본으로 ETA를 내면 3시간 → 7시간 → 10시간처럼 요동친다(실측). uv 기동과
        # 병렬 워커 준비가 초반을 지배하고, .incomplete 파일 크기는 뒤늦게 반영된다.
        # 신호가 쌓이기 전에는 아무 말도 하지 않는 편이 낫다.
        settled = (
            elapsed >= ETA_MIN_ELAPSED_S
            and self.total_bytes > 0
            and self.downloaded_bytes >= self.total_bytes * ETA_MIN_FRACTION
        )
        rate = self.downloaded_bytes / elapsed if settled else 0.0
        return {
            "model_id": self.model_id,
            "repo": self.repo,
            "status": self.status,
            "error": self.error,
            "downloaded_bytes": self.downloaded_bytes,
            "total_bytes": self.total_bytes,
            "elapsed_ms": round(elapsed * 1000),
            "eta_ms": round(remaining / rate * 1000) if rate > 0 else None,
        }


class ModelDownloader:
    """한 번에 하나. workspaces.py의 잡 패턴과 같은 계약이다."""

    def __init__(self, publish=None) -> None:
        self._lock = threading.Lock()
        self._job: DownloadJob | None = None
        self._thread: threading.Thread | None = None
        self._publish = publish or (lambda *a, **k: None)

    def snapshot(self) -> dict | None:
        with self._lock:
            return self._job.view() if self._job is not None else None

    def active(self) -> bool:
        with self._lock:
            return self._thread is not None and self._thread.is_alive()

    def start(self, model_id: str, *, with_draft: bool = True) -> dict:
        entry = MODEL_CATALOG.get(model_id)
        if entry is None:
            raise ValueError(f"알 수 없는 모델입니다: {model_id}")
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("이미 다운로드가 진행 중입니다")

        needed = plan(model_id)["total_bytes"]
        if with_draft:
            needed += _plan_repo(DRAFT_REPO, None)["total_bytes"]
        free = disk()["free_bytes"]
        if free < needed + DISK_HEADROOM_BYTES:
            raise RuntimeError(
                f"디스크 여유가 부족합니다. 필요 {_gb(needed + DISK_HEADROOM_BYTES)}, "
                f"남음 {_gb(free)}"
            )

        repo = str(entry["repo"])
        with self._lock:
            self._job = DownloadJob(
                model_id=model_id, repo=repo, total_bytes=needed,
                baseline_bytes=_dir_size(repo_dir(repo)) + _dir_size(repo_dir(DRAFT_REPO)),
            )
            self._thread = threading.Thread(
                target=self._run, args=(model_id, entry, with_draft),
                name=f"janus-model-{model_id}", daemon=True,
            )
            self._thread.start()
            return self._job.view()

    def cancel(self) -> dict | None:
        with self._lock:
            job = self._job
            if job is None or job.status != "running":
                return job.view() if job else None
            job.cancelled = True
            process = job.process
        if process is not None and process.poll() is None:
            try:
                os.killpg(os.getpgid(process.pid), 15)
            except (ProcessLookupError, PermissionError, OSError):
                process.terminate()
        return self.snapshot()

    # ── 내부 ──

    def _run(self, model_id: str, entry: dict, with_draft: bool) -> None:
        try:
            self._download(str(entry["repo"]), entry["include"])
            if with_draft and not self._job_cancelled():
                self._download(DRAFT_REPO, None)
            self._settle("cancelled" if self._job_cancelled() else "completed")
        except Exception as error:  # noqa: BLE001 — 어떤 실패든 UI에 사유를 남긴다
            self._settle(
                "cancelled" if self._job_cancelled() else "failed",
                error=f"{type(error).__name__}: {error}",
            )

    def _job_cancelled(self) -> bool:
        with self._lock:
            return self._job is not None and self._job.cancelled

    def _download(self, repo: str, include: str | None) -> None:
        root, env = _model_runtime()
        command = ["uv", "run", "--frozen", "hf", "download", repo]
        if include:
            command += ["--include", include]
        # 프로세스 그룹을 따로 만들어 uv가 낳는 자식까지 한 번에 취소한다.
        process = subprocess.Popen(
            command, cwd=root, env=env, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, start_new_session=True,
        )
        with self._lock:
            if self._job is not None:
                self._job.process = process

        while process.poll() is None:
            self._measure()
            time.sleep(1.0)
        self._measure()
        stderr = (process.stderr.read() if process.stderr else "")[-500:]
        with self._lock:
            if self._job is not None:
                self._job.process = None
        if process.returncode != 0 and not self._job_cancelled():
            raise RuntimeError(
                classify_hf_failure(stderr, repo)
                if stderr.strip() else f"hf download 종료 코드 {process.returncode}"
            )

    def _measure(self) -> None:
        with self._lock:
            job = self._job
            if job is None:
                return
            current = _dir_size(repo_dir(job.repo)) + _dir_size(repo_dir(DRAFT_REPO))
            job.downloaded_bytes = max(0, current - job.baseline_bytes)
            view = job.view()
        self._publish("model", "progress", **view)

    def _settle(self, status: str, error: str | None = None) -> None:
        with self._lock:
            job = self._job
            if job is None:
                return
            job.status = status
            job.error = error
            if status == "completed":
                job.downloaded_bytes = job.total_bytes
            view = job.view()
        self._publish("model", "ready" if status == "completed" else status, **view)


def _gb(value: int) -> str:
    return f"{value / 1024**3:.1f}GB"


def demo() -> None:
    """assert 기반 자기 검증 — 크기 파서와 디스크 계산."""
    assert parse_size("5.3G") == int(5.3 * 1024**3)
    assert parse_size("20.0M") == int(20.0 * 1024**2)
    assert parse_size("632.0") == 632
    assert parse_size("1.6K") == 1638
    assert parse_size("") == 0
    assert repo_dir("mlx-community/Qwen3.8-27B-4bit").name == (
        "models--mlx-community--Qwen3.8-27B-4bit"
    )
    usage = disk()
    assert usage["free_bytes"] > 0 and usage["total_bytes"] >= usage["free_bytes"]
    print("model_setup self-check ok")


if __name__ == "__main__":
    demo()
