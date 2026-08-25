#!/usr/bin/env python3
"""Install from a clean source copy, package Janus, and boot a clean backend."""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import signal
import socket
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def copy_source(destination: Path) -> None:
    shutil.copy2(ROOT / "README.md", destination / "README.md")
    shutil.copy2(ROOT / ".gitignore", destination / ".gitignore")
    shutil.copytree(
        ROOT / "janus", destination / "janus",
        ignore=shutil.ignore_patterns("node_modules", "out", "dist", ".DS_Store"),
    )
    shutil.copytree(
        ROOT / "janus_server", destination / "janus_server",
        ignore=shutil.ignore_patterns(
            ".venv", "artifacts", "__pycache__", ".pytest_cache",
        ),
    )
    model = destination / "qwen3.8mlx"
    model.mkdir()
    for name in ("pyproject.toml", "uv.lock"):
        shutil.copy2(ROOT / "qwen3.8mlx" / name, model / name)


def run(command: list[str], cwd: Path, timeout: int = 600) -> None:
    completed = subprocess.run(command, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if completed.returncode:
        detail = (completed.stdout + "\n" + completed.stderr)[-8000:]
        raise RuntimeError(f"{' '.join(command)} failed ({completed.returncode}):\n{detail}")


def request(url: str, token: str, *, method: str = "GET") -> dict:
    payload = b"{}" if method == "POST" else None
    call = urllib.request.Request(
        url, data=payload, method=method,
        headers={"X-Janus-Token": token, "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(call, timeout=2) as response:
        return json.load(response)


def backend_smoke(root: Path) -> dict:
    data = root / "clean-user-data"
    token = "fresh-install-smoke-token"
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = int(probe.getsockname()[1])
    environment = {
        **os.environ,
        "JANUS_AUTH_TOKEN": token,
        "JANUS_ALLOWED_ORIGINS": "null",
        "JANUS_PORT": str(port),
        "JANUS_DB_FILE": str(data / "janus.sqlite3"),
        "JANUS_WORKTREES_DIR": str(data / "workspaces"),
        "JANUS_BACKUPS_DIR": str(data / "backups"),
        "JANUS_LOG_DIR": str(data / "logs"),
        "JANUS_DIAGNOSTICS_DIR": str(data / "diagnostics"),
    }
    (data / "logs").mkdir(parents=True)
    process = subprocess.Popen(
        ["uv", "run", "--frozen", "python", "-m", "janus_server.server"],
        cwd=root / "janus_server", env=environment,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 30
        health = None
        while time.monotonic() < deadline and process.poll() is None:
            try:
                health = request(f"http://127.0.0.1:{port}/health", token)
                break
            except Exception:
                time.sleep(0.2)
        if health is None:
            raise RuntimeError(f"clean backend did not become healthy; exit={process.poll()}")
        backup = request(
            f"http://127.0.0.1:{port}/maintenance/backups", token, method="POST"
        )
        diagnostics = request(
            f"http://127.0.0.1:{port}/maintenance/diagnostics", token, method="POST"
        )
        return {
            "health": health,
            "backup_integrity": backup["integrity"]["ok"],
            "diagnostics_redacted": diagnostics["redacted"],
        }
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        if process.poll() is None:
            raise RuntimeError(f"orphan backend process: {process.pid}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-model-runtime", action="store_true")
    parser.add_argument("--skip-package", action="store_true")
    args = parser.parse_args()
    if platform.system() != "Darwin" or platform.machine() != "arm64":
        parser.error("fresh install smoke requires Apple Silicon macOS")
    for command in ("git", "uv", "node", "pnpm"):
        if shutil.which(command) is None:
            parser.error(f"missing prerequisite: {command}")

    with tempfile.TemporaryDirectory(prefix="janus-fresh-install-") as temporary:
        root = Path(temporary)
        copy_source(root)
        run(["uv", "sync", "--frozen"], root / "janus_server")
        if not args.skip_model_runtime:
            run(["uv", "sync", "--frozen"], root / "qwen3.8mlx", timeout=1200)
        run(["pnpm", "install", "--frozen-lockfile"], root / "janus", timeout=1200)
        run(["pnpm", "test:main"], root / "janus")
        run(["pnpm", "build"], root / "janus")
        packaged = False
        if not args.skip_package:
            run(["pnpm", "package:mac"], root / "janus", timeout=1200)
            packaged = any((root / "janus" / "dist").glob("mac*/Janus.app"))
            if not packaged:
                raise RuntimeError("electron-builder did not create Janus.app")
        backend = backend_smoke(root)
        report = {
            "platform": f"{platform.system()} {platform.machine()}",
            "clean_backend": backend, "packaged_app": packaged,
            "model_runtime_installed": not args.skip_model_runtime,
        }
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
