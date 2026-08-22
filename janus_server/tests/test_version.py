"""Desktop, backend package, and API versions must never drift."""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

from janus_server.server import app
from janus_server.version import __version__


ROOT = Path(__file__).resolve().parents[2]


def test_product_versions_match():
    desktop = json.loads((ROOT / "janus" / "package.json").read_text(encoding="utf-8"))
    backend = tomllib.loads(
        (ROOT / "janus_server" / "pyproject.toml").read_text(encoding="utf-8")
    )
    assert desktop["version"] == backend["project"]["version"] == __version__ == app.version
