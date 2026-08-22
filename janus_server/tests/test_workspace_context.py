"""P1 WorkspaceContext의 명시적 주입과 병렬 격리 계약."""

from __future__ import annotations

import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from janus_server import tools as T
from janus_server import verification
from janus_server.workspace import WorkspaceContext


class WorkspaceContextTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.root_a = root / "a"
        self.root_b = root / "b"
        self.root_a.mkdir()
        self.root_b.mkdir()
        self.context_a = WorkspaceContext(
            root=self.root_a, task_id="task_a", workspace_id="workspace_a"
        ).for_dispatch("dispatch_a")
        self.context_b = WorkspaceContext(
            root=self.root_b, task_id="task_b", workspace_id="workspace_b"
        ).for_dispatch("dispatch_b")

    def tearDown(self):
        self.temp.cleanup()

    def test_workspace_tools_require_an_explicit_context(self):
        read = T.dispatch("read_file", {"path": "missing.txt"})
        write = T.dispatch(
            "write_file", {"path": "created.txt", "content": "bad"},
            approve=lambda *_: True,
        )

        self.assertIn("WorkspaceContext", read["error"])
        self.assertIn("WorkspaceContext", write["error"])
        self.assertFalse((self.root_a / "created.txt").exists())

    def test_two_parallel_contexts_isolate_same_relative_path(self):
        def write(context: WorkspaceContext, content: str) -> dict:
            return T.dispatch(
                "write_file", {"path": "same/result.txt", "content": content},
                approve=lambda *_: True, context=context,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            result_a = pool.submit(write, self.context_a, "A")
            result_b = pool.submit(write, self.context_b, "B")

        self.assertNotIn("error", result_a.result())
        self.assertNotIn("error", result_b.result())
        self.assertEqual("A", (self.root_a / "same/result.txt").read_text())
        self.assertEqual("B", (self.root_b / "same/result.txt").read_text())

    def test_context_cannot_read_or_write_another_workspace(self):
        secret = self.root_b / "secret.txt"
        secret.write_text("B only", encoding="utf-8")

        read = T.dispatch(
            "read_file", {"path": str(secret)}, context=self.context_a
        )
        write = T.dispatch(
            "write_file", {"path": str(secret), "content": "overwritten"},
            approve=lambda *_: True, context=self.context_a,
        )

        self.assertIn("밖 경로", read["error"])
        self.assertIn("밖 경로", write["error"])
        self.assertEqual("B only", secret.read_text(encoding="utf-8"))

    def test_shell_starts_in_the_context_root(self):
        result = T.dispatch(
            "run_bash", {"command": "pwd"}, approve=lambda *_: True,
            context=self.context_b,
        )

        self.assertEqual(0, result["exit_code"])
        self.assertEqual(str(self.context_b.root), result["stdout"].strip())

    def test_verification_uses_the_same_context_root_and_ids(self):
        result = verification.run("pwd", self.context_a)

        self.assertEqual(0, result["exit_code"])
        self.assertEqual(str(self.context_a.root), result["stdout"].strip())
        self.assertEqual(self.context_a.identifiers(), {
            key: result[key] for key in ("task_id", "workspace_id", "dispatch_id")
        })


if __name__ == "__main__":
    unittest.main()
