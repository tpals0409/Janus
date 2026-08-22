#!/usr/bin/env python3
"""UI 없이 실제 Qwen3.8-27B MLX와 Janus runtime을 검증한다.

실행:
    .venv/bin/python scripts/p0_smoke_27b.py

8080 서버가 이미 정상이면 외부 소유로 사용하고 종료하지 않는다. 비어 있으면 이
harness가 MLX 서버를 시작하고, 자신이 시작한 프로세스 그룹만 종료한다.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import socket
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

PROJECT_DIR = Path(__file__).resolve().parents[1]
REPO_ROOT = PROJECT_DIR.parent
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from janus_server import runtime  # noqa: E402
from janus_server.workspace import WorkspaceContext  # noqa: E402


class SmokeFailure(RuntimeError):
    pass


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def port_open(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=0.5):
            return True
    except OSError:
        return False


def model_api_ready() -> bool:
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/v1/models", timeout=2) as res:
            return res.status == 200
    except (OSError, urllib.error.URLError):
        return False


class ModelServer:
    def __init__(self, artifact_dir: Path, startup_timeout: float):
        self.artifact_dir = artifact_dir
        self.startup_timeout = startup_timeout
        self.process: subprocess.Popen | None = None
        self.log_handle = None
        self.ownership = "none"
        self.stop_result: dict = {}

    @property
    def pid(self) -> int | None:
        return self.process.pid if self.process is not None else None

    def log_tail(self, limit: int = 2000) -> str:
        if self.log_handle is not None:
            self.log_handle.flush()
        path = self.artifact_dir / "mlx-server.log"
        if not path.is_file():
            return ""
        return path.read_text(encoding="utf-8", errors="replace")[-limit:]

    def start(self) -> None:
        if port_open(8080):
            self.ownership = "external"
            if not model_api_ready():
                raise SmokeFailure("8080 포트는 사용 중이지만 /v1/models가 응답하지 않습니다")
            return

        model = runtime.resolve_local_model("qwen3.8-27b")
        python = REPO_ROOT / "qwen3.8mlx" / ".venv" / "bin" / "python"
        if not python.is_file():
            raise SmokeFailure(f"MLX Python을 찾을 수 없습니다: {python}")
        self.log_handle = (self.artifact_dir / "mlx-server.log").open(
            "a", encoding="utf-8"
        )
        self.process = subprocess.Popen(
            [str(python), "-m", "mlx_vlm.server", "--model", model, "--port", "8080"],
            cwd=REPO_ROOT / "qwen3.8mlx",
            stdout=self.log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
        self.ownership = "owned"
        deadline = time.monotonic() + self.startup_timeout
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise SmokeFailure(
                    f"MLX 서버가 준비 전에 종료됐습니다(exit={self.process.returncode})\n"
                    f"--- mlx-server.log tail ---\n{self.log_tail()}"
                )
            if model_api_ready():
                return
            time.sleep(1)
        raise SmokeFailure(
            f"MLX 서버 준비 timeout({self.startup_timeout:.0f}s)\n"
            f"--- mlx-server.log tail ---\n{self.log_tail()}"
        )

    def stop(self) -> None:
        # 외부 서버는 절대 종료하지 않는다.
        if self.ownership != "owned" or self.process is None:
            if self.log_handle is not None:
                self.log_handle.close()
            self.stop_result = {
                "orphan_processes": 0,
                "owned_process_alive": False,
                "port_open_after_stop": port_open(8080),
            }
            return
        process = self.process
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=5)
        if self.log_handle is not None:
            self.log_handle.close()
        deadline = time.monotonic() + 5
        while port_open(8080) and time.monotonic() < deadline:
            time.sleep(0.1)
        alive = process.poll() is None
        port_still_open = port_open(8080)
        self.stop_result = {
            "orphan_processes": int(alive or port_still_open),
            "owned_process_alive": alive,
            "port_open_after_stop": port_still_open,
        }


def base_spec(system_prompt: str) -> dict:
    return {
        "name": "P0 27B smoke",
        "model": "qwen3.8-27b",
        "system_prompt": system_prompt,
        "tools": [],
        "approval": "auto",
        "max_steps": 4,
    }


class ScenarioRunner:
    def __init__(self, timeout: float):
        self.timeout = timeout
        self.events: list[dict] = []
        self.event_hook: Callable[[dict], None] | None = None

    def send(self, event: dict) -> None:
        self.events.append(event)
        if self.event_hook is not None:
            self.event_hook(event)

    def orchestration(self, system_prompt: str) -> runtime.Orchestration:
        context = WorkspaceContext(
            root=REPO_ROOT,
            task_id=f"task_p0_smoke_{uuid.uuid4().hex[:12]}",
            workspace_id="workspace_p0_smoke_repo",
        )
        return runtime.Orchestration(
            base_spec(system_prompt), send=self.send, approver=None,
            workspace_context=context,
        )

    def turn(self, orch: runtime.Orchestration, prompt: str) -> None:
        failure: list[BaseException] = []

        def target() -> None:
            try:
                orch.turn(prompt)
            except BaseException as error:
                failure.append(error)

        thread = threading.Thread(target=target, daemon=True)
        thread.start()
        thread.join(self.timeout)
        if thread.is_alive():
            orch.cancel_all()
            thread.join(5)
            raise SmokeFailure(f"turn timeout({self.timeout:.0f}s): {prompt[:80]}")
        if failure:
            raise SmokeFailure(f"turn 실패: {type(failure[0]).__name__}: {failure[0]}")

    @staticmethod
    def verify(orch: runtime.Orchestration, check: Callable[[], None], label: str) -> None:
        operation_id = uuid.uuid4().hex[:16]
        dispatch_id = next(
            (
                item["dispatch_id"]
                for item in reversed(orch.telemetry.intervals)
                if item["category"] == "active_turn"
            ),
            None,
        )
        orch.telemetry.record_event(
            "verification_start", node_id="verifier", dispatch_id=dispatch_id,
            worker_id=None, operation_id=operation_id, label=label,
        )
        try:
            check()
        except Exception:
            orch.telemetry.record_event(
                "verification_end", node_id="verifier", dispatch_id=dispatch_id,
                worker_id=None, operation_id=operation_id, status="error", label=label,
            )
            raise
        orch.telemetry.record_event(
            "verification_end", node_id="verifier", dispatch_id=dispatch_id,
            worker_id=None, operation_id=operation_id, status="success", label=label,
        )

    def result(self, orch: runtime.Orchestration) -> dict:
        return {
            "reply": orch.last_text,
            "spans": orch.snapshot_spans(),
            "telemetry": orch.snapshot_telemetry(),
        }


def require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeFailure(message)


def scenario_multi_turn(timeout: float) -> dict:
    runner = ScenarioRunner(timeout)
    orch = runner.orchestration(
        "This is a deterministic smoke test. Do not call tools unless explicitly asked. "
        "Follow the requested output format exactly and keep replies under 20 tokens."
    )
    marker = "JANUS-MULTITURN-7F3A"
    runner.turn(orch, f"Remember this marker for the next turn: {marker}. Reply SAVED only.")
    runner.turn(orch, "Reply with the exact marker I asked you to remember, and nothing else.")
    runner.verify(
        orch,
        lambda: require(marker in orch.last_text, f"멀티턴 marker 누락: {orch.last_text!r}"),
        "multi_turn_marker",
    )
    return runner.result(orch)


def scenario_worker_spawn(timeout: float) -> dict:
    runner = ScenarioRunner(timeout)
    orch = runner.orchestration(
        "You are a tool-use smoke test. When explicitly told to call create_worker, you must "
        "call it before answering. Keep all replies short."
    )
    runner.turn(
        orch,
        "Call create_worker exactly once with name smoke-worker, system_prompt 'Reply with the "
        "requested marker only', task 'Reply JANUS_WORKER_OK only', tools [], max_steps 2. "
        "After the tool returns, reply JANUS_ORCH_OK only.",
    )

    def check() -> None:
        spans = orch.snapshot_spans()
        workers = [span for span in spans if span["worker_id"] is not None]
        require(len(workers) == 1, f"worker 1개 기대, 실제 {len(workers)}")
        require(workers[0]["status"] == "success", f"worker 실패: {workers[0]}")
        require(orch.telemetry.snapshot(usage=orch.node_usage, worker_count=orch.worker_seq)[
            "worker_count"
        ] == 1, "telemetry worker_count 불일치")

    runner.verify(orch, check, "worker_spawn")
    return runner.result(orch)


def scenario_worker_stop(timeout: float) -> dict:
    runner = ScenarioRunner(timeout)
    orch = runner.orchestration(
        "You are a tool-use smoke test. When explicitly told to call create_worker, you must "
        "call it before answering. Keep all replies short."
    )
    stopped: list[str] = []

    def stop_on_span(event: dict) -> None:
        span = event.get("span") or {}
        worker_id = span.get("worker_id")
        if event.get("type") == "span_start" and worker_id and not stopped:
            stopped.append(worker_id)
            orch.stop_worker(worker_id)

    runner.event_hook = stop_on_span
    runner.turn(
        orch,
        "Call create_worker exactly once with name stop-worker, system_prompt 'Follow the task', "
        "task 'Write a very long numbered list', tools [], max_steps 4. After it returns, "
        "reply JANUS_STOP_OBSERVED only.",
    )

    def check() -> None:
        require(len(stopped) == 1, "중단할 worker span이 생성되지 않음")
        worker = next(
            span for span in orch.snapshot_spans() if span["worker_id"] == stopped[0]
        )
        require(worker["status"] == "error", f"중단 worker 상태가 error가 아님: {worker}")
        require("중단" in str(worker.get("output")), f"중단 사유 누락: {worker}")

    runner.verify(orch, check, "worker_stop")
    return runner.result(orch)


def scenario_cancel_resume(timeout: float) -> dict:
    runner = ScenarioRunner(timeout)
    orch = runner.orchestration(
        "This is a cancellation smoke test. Do not call tools. Follow output formats exactly."
    )
    cancelled = threading.Event()

    def cancel_on_generation(event: dict) -> None:
        if (
            event.get("type") == "agent_event"
            and event.get("kind") == "model_generation_start"
            and not cancelled.is_set()
        ):
            cancelled.set()
            orch.cancel_all()

    runner.event_hook = cancel_on_generation
    runner.turn(orch, "Write 500 numbered words.")
    runner.event_hook = None
    runner.turn(orch, "Reply JANUS_RESUMED only.")

    def check() -> None:
        require(cancelled.is_set(), "generation cancel hook가 실행되지 않음")
        require("JANUS_RESUMED" in orch.last_text, f"취소 후 재개 marker 누락: {orch.last_text!r}")
        turns = [
            item for item in orch.telemetry.intervals if item["category"] == "active_turn"
        ]
        require(len(turns) == 2, f"active turn 2개 기대, 실제 {len(turns)}")
        require(turns[0]["status"] == "cancelled", f"첫 turn 상태 오류: {turns[0]}")
        require(turns[1]["status"] == "success", f"둘째 turn 상태 오류: {turns[1]}")

    runner.verify(orch, check, "cancel_resume")
    return runner.result(orch)


SCENARIOS = [
    ("multi_turn", scenario_multi_turn),
    ("worker_spawn", scenario_worker_spawn),
    ("worker_stop", scenario_worker_stop),
    ("cancel_resume", scenario_cancel_resume),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--turn-timeout", type=float, default=180)
    parser.add_argument("--model-startup-timeout", type=float, default=240)
    parser.add_argument(
        "--output-dir", type=Path,
        default=PROJECT_DIR / "artifacts" / "p0" / "smoke",
    )
    args = parser.parse_args()

    run_stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    artifact_dir = args.output_dir / run_stamp
    artifact_dir.mkdir(parents=True, exist_ok=False)
    report: dict = {
        "schema_version": 1,
        "started_at": utc_now(),
        "status": "running",
        "environment": {
            "platform": platform.platform(),
            "python": sys.version,
            "model": "qwen3.8-27b",
            "model_path": runtime.resolve_local_model("qwen3.8-27b"),
        },
        "model_server": {},
        "scenarios": {},
    }
    server = ModelServer(artifact_dir, args.model_startup_timeout)
    exit_code = 1
    try:
        server.start()
        report["model_server"] = {"ownership": server.ownership, "pid": server.pid}
        for name, scenario in SCENARIOS:
            started = time.monotonic()
            try:
                result = scenario(args.turn_timeout)
            except Exception as error:
                report["scenarios"][name] = {
                    "status": "failed",
                    "duration_ms": round((time.monotonic() - started) * 1000, 3),
                    "error": f"{type(error).__name__}: {error}",
                    "traceback": traceback.format_exc(),
                }
                raise
            report["scenarios"][name] = {
                "status": "passed",
                "duration_ms": round((time.monotonic() - started) * 1000, 3),
                **result,
            }
        report["status"] = "passed"
        exit_code = 0
    except Exception as error:
        report["status"] = "failed"
        report["error"] = f"{type(error).__name__}: {error}"
        report["traceback"] = traceback.format_exc()
    finally:
        server.stop()
        report["finished_at"] = utc_now()
        report["model_server"]["exit_code"] = (
            server.process.returncode if server.process is not None else None
        )
        report["model_server"].update(server.stop_result)
        report_path = artifact_dir / "result.json"
        report_path.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(json.dumps({
            "status": report["status"],
            "result": str(report_path),
            "scenarios": {
                name: item["status"] for name, item in report["scenarios"].items()
            },
        }, ensure_ascii=False))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
