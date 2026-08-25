"""패키징 앱의 실제 기동 경로가 import 단계에서 죽지 않는지 검증한다."""

from __future__ import annotations

import os
import subprocess
import sys
import unittest


class EntrypointTests(unittest.TestCase):
    def test_entry_and_router_first_imports_are_safe(self):
        env = {**os.environ, "JANUS_AUTH_TOKEN": "test-token"}
        for module in ("janus_server.__main__", "janus_server.routers.development"):
            with self.subTest(module=module):
                proc = subprocess.run(
                    [sys.executable, "-c", f"import {module}"],
                    capture_output=True, text=True, env=env, timeout=60,
                )
                self.assertEqual(0, proc.returncode, proc.stderr)


if __name__ == "__main__":
    unittest.main()
