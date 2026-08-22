"""TaskSuite v0 manifest와 fixture 격리 계약."""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path


SUITE = Path(__file__).parents[1] / "tasksuite" / "v0"


class TaskSuiteTests(unittest.TestCase):
    def test_manifest_has_three_fixed_task_shapes(self):
        manifest = json.loads((SUITE / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(5, manifest["repeats"])
        self.assertEqual(
            {"single_file_bug", "multi_file_refactor", "investigate_code_tests"},
            {task["id"] for task in manifest["tasks"]},
        )
        for task in manifest["tasks"]:
            self.assertTrue(task["objective"])
            self.assertTrue(task["constraints"])
            self.assertTrue(task["acceptance_command"])
            self.assertTrue(task["required_changed_files"])
            self.assertTrue((SUITE / "fixtures" / task["id"]).is_dir())

    def test_every_pristine_fixture_fails_its_acceptance(self):
        manifest = json.loads((SUITE / "manifest.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            for task in manifest["tasks"]:
                workspace = Path(tmp) / task["id"]
                shutil.copytree(SUITE / "fixtures" / task["id"], workspace)
                result = subprocess.run(
                    task["acceptance_command"], cwd=workspace, shell=True,
                    capture_output=True, text=True, timeout=10,
                )
                self.assertNotEqual(0, result.returncode, task["id"])


if __name__ == "__main__":
    unittest.main()
