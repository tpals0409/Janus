#!/usr/bin/env python3
"""Fail when Janus desktop, backend package, and API versions drift."""

from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def product_versions(root: Path = ROOT) -> dict[str, str]:
    desktop = json.loads((root / "janus" / "package.json").read_text(encoding="utf-8"))
    backend = tomllib.loads(
        (root / "janus_server" / "pyproject.toml").read_text(encoding="utf-8")
    )
    version_source = (root / "janus_server" / "janus_server" / "version.py").read_text(
        encoding="utf-8"
    )
    match = re.search(r'^__version__\s*=\s*["\']([^"\']+)["\']', version_source, re.MULTILINE)
    if match is None:
        raise ValueError("could not find janus_server.version.__version__")
    return {
        "desktop": str(desktop["version"]),
        "backend": str(backend["project"]["version"]),
        "api": match.group(1),
    }


def verify(expected: str | None = None, root: Path = ROOT) -> dict[str, str]:
    versions = product_versions(root)
    if len(set(versions.values())) != 1:
        raise ValueError(
            "product versions disagree: "
            + ", ".join(f"{key}={value}" for key, value in versions.items())
        )
    actual = next(iter(versions.values()))
    if expected is not None and actual != expected.removeprefix("v"):
        raise ValueError(f"release tag does not match product version: tag={expected}, product={actual}")
    return versions


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected", help="Expected semantic version or v-prefixed release tag")
    args = parser.parse_args()
    try:
        versions = verify(args.expected)
    except (OSError, KeyError, ValueError, tomllib.TOMLDecodeError) as error:
        parser.exit(1, f"version check failed: {error}\n")
    print("version invariant ok: " + next(iter(versions.values())))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
