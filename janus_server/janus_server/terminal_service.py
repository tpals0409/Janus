"""Task-scoped PTY sessions with bounded reconnect buffers."""

from __future__ import annotations

import fcntl
import os
import pty
import signal
import struct
import subprocess
import termios
import threading
import uuid
from collections.abc import Callable
from pathlib import Path

MAX_TERMINAL_BUFFER_CHARS = 200_000


class TerminalServiceError(RuntimeError):
    pass


class TerminalSession:
    def __init__(
        self, *, terminal_id: str, task_id: str, pane_id: str, cwd: Path,
        on_output: Callable[[str, str, int], None],
        on_exit: Callable[[str, int | None], None], shell: str = "/bin/zsh",
    ):
        self.id = terminal_id
        self.task_id = task_id
        self.pane_id = pane_id
        self.cwd = cwd.resolve()
        self.shell = shell
        self.on_output = on_output
        self.on_exit = on_exit
        self.lock = threading.RLock()
        self.buffer = ""
        self.offset = 0
        self.closed = False
        master, slave = pty.openpty()
        self.master_fd = master
        try:
            self.process = subprocess.Popen(
                [shell, "-l"], cwd=self.cwd, stdin=slave, stdout=slave, stderr=slave,
                start_new_session=True, close_fds=True,
                env={**os.environ, "TERM": "xterm-256color"},
            )
        finally:
            os.close(slave)
        os.set_blocking(master, False)
        self.reader = threading.Thread(
            target=self._read_loop, name=f"terminal-{terminal_id}", daemon=True
        )
        self.reader.start()

    def _read_loop(self) -> None:
        while not self.closed:
            try:
                chunk = os.read(self.master_fd, 16_384)
            except BlockingIOError:
                if self.process.poll() is not None:
                    break
                threading.Event().wait(0.03)
                continue
            except OSError:
                break
            if not chunk:
                break
            text = chunk.decode("utf-8", errors="replace")
            with self.lock:
                self.offset += len(text)
                self.buffer = (self.buffer + text)[-MAX_TERMINAL_BUFFER_CHARS:]
                offset = self.offset
            self.on_output(self.id, text, offset)
        exit_code = self.process.poll()
        if exit_code is None:
            try:
                exit_code = self.process.wait(timeout=0.2)
            except subprocess.TimeoutExpired:
                exit_code = None
        self.closed = True
        self.on_exit(self.id, exit_code)

    def write(self, value: str) -> None:
        if self.closed or self.process.poll() is not None:
            raise TerminalServiceError("종료된 terminal에는 입력할 수 없습니다")
        os.write(self.master_fd, str(value).encode("utf-8"))

    def resize(self, columns: int, rows: int) -> None:
        columns = max(20, min(int(columns), 400))
        rows = max(5, min(int(rows), 200))
        fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, struct.pack("HHHH", rows, columns, 0, 0))

    def snapshot(self) -> dict:
        with self.lock:
            return {
                "id": self.id, "task_id": self.task_id, "pane_id": self.pane_id,
                "cwd": str(self.cwd), "shell": self.shell, "pid": self.process.pid,
                "state": "running" if self.process.poll() is None and not self.closed else "exited",
                "exit_code": self.process.poll(), "buffer": self.buffer, "offset": self.offset,
            }

    def stop(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            os.killpg(self.process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            self.process.wait(timeout=2)
        try:
            os.close(self.master_fd)
        except OSError:
            pass


class TerminalManager:
    def __init__(
        self, *, on_output: Callable[[str, str, int], None],
        on_exit: Callable[[str, int | None], None],
    ):
        self.on_output = on_output
        self.on_exit = on_exit
        self.lock = threading.RLock()
        self.sessions: dict[str, TerminalSession] = {}

    def create(self, *, task_id: str, pane_id: str, cwd: str | Path) -> TerminalSession:
        with self.lock:
            existing = next((
                item for item in self.sessions.values()
                if item.task_id == task_id and item.pane_id == pane_id
                and item.process.poll() is None and not item.closed
            ), None)
            if existing is not None:
                return existing
            terminal_id = f"terminal_{uuid.uuid4().hex}"
            session = TerminalSession(
                terminal_id=terminal_id, task_id=task_id, pane_id=pane_id,
                cwd=Path(cwd), on_output=self.on_output, on_exit=self.on_exit,
            )
            self.sessions[terminal_id] = session
            return session

    def get(self, terminal_id: str) -> TerminalSession:
        with self.lock:
            session = self.sessions.get(terminal_id)
        if session is None:
            raise TerminalServiceError(f"live terminal이 없습니다: {terminal_id}")
        return session

    def list_task(self, task_id: str) -> list[TerminalSession]:
        with self.lock:
            return [item for item in self.sessions.values() if item.task_id == task_id]

    def stop(self, terminal_id: str) -> None:
        self.get(terminal_id).stop()

    def stop_all(self) -> None:
        with self.lock:
            sessions = list(self.sessions.values())
        for session in sessions:
            session.stop()
