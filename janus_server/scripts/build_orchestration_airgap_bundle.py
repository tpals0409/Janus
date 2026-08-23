#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from janus_server.orchestration_bundle import create_orchestration_bundle  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Janus orchestration airgap bundle")
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    print(json.dumps(create_orchestration_bundle(PROJECT_DIR, args.output), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
