"""패키징 앱의 실제 기동 경로가 import 단계에서 죽지 않는지 검증한다."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest
from pathlib import Path


class EntrypointTests(unittest.TestCase):
    def test_entry_and_router_first_imports_are_safe(self):
        # 서브프로세스는 부모의 sys.path 조작을 물려받지 않는다 — 패키지 루트를 명시한다.
        package_root = Path(__file__).resolve().parents[1]
        env = {
            **os.environ,
            "JANUS_AUTH_TOKEN": "test-token",
            "PYTHONPATH": os.pathsep.join(
                [str(package_root), os.environ.get("PYTHONPATH", "")]
            ).rstrip(os.pathsep),
        }
        for module in ("janus_server.__main__", "janus_server.routers.development"):
            with self.subTest(module=module):
                proc = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    capture_output=True, text=True, env=env, timeout=60,
                )
                self.assertEqual(0, proc.returncode, proc.stderr)


if __name__ == "__main__":
    unittest.main()
