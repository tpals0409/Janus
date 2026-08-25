from __future__ import annotations

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[2] / "scripts" / "check_versions.py"
SPEC = importlib.util.spec_from_file_location("check_versions", SCRIPT)
assert SPEC and SPEC.loader
check_versions = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(check_versions)


def write_fixture(root: Path, desktop: str, backend: str, api: str) -> None:
    (root / "janus").mkdir()
    (root / "janus_server" / "janus_server").mkdir(parents=True)
    (root / "janus" / "package.json").write_text(
        json.dumps({"version": desktop}), encoding="utf-8"
    )
    (root / "janus_server" / "pyproject.toml").write_text(
        f'[project]\nname = "janus-server"\nversion = "{backend}"\n', encoding="utf-8"
    )
    (root / "janus_server" / "janus_server" / "version.py").write_text(
        f'__version__ = "{api}"\n', encoding="utf-8"
    )


class ReleaseContractTests(unittest.TestCase):
    def test_three_product_versions_and_release_tag_match(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root, "1.2.3", "1.2.3", "1.2.3")
            self.assertEqual("1.2.3", check_versions.verify("v1.2.3", root)["desktop"])

    def test_drift_and_wrong_release_tag_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root, "1.2.3", "1.2.4", "1.2.3")
            with self.assertRaisesRegex(ValueError, "제품 버전 불일치"):
                check_versions.verify(root=root)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_fixture(root, "1.2.3", "1.2.3", "1.2.3")
            with self.assertRaisesRegex(ValueError, "릴리스 태그"):
                check_versions.verify("v1.2.4", root)


if __name__ == "__main__":
    unittest.main()
