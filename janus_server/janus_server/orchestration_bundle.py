"""Deterministic closed-network bundle for the orchestration runtime."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile


BUNDLE_COMPONENTS = (
    "janus_server/airgap.py",
    "janus_server/model_router.py",
    "janus_server/ownership.py",
    "janus_server/pipeline.py",
    "janus_server/scheduler.py",
    "janus_server/verification.py",
    "janus_server/workflow.py",
    "janus_server/workflow_template.py",
    "janus_server/workflow_workspace.py",
    "janus_server/workspace.py",
    "janus_server/workspace_service.py",
    "config/models.yaml",
    "config/workflows/standard.yaml",
)


def create_orchestration_bundle(project_root: Path, output: Path) -> dict:
    root = Path(project_root).resolve()
    target = Path(output)
    entries: dict[str, bytes] = {}
    for relative in BUNDLE_COMPONENTS:
        path = (root / relative).resolve()
        if root not in path.parents or not path.is_file():
            raise FileNotFoundError(f"required orchestration component missing: {relative}")
        entries[relative] = path.read_bytes()
    manifest = {
        "schema_version": 1,
        "network_policy": "loopback_and_unix_only",
        "components": {
            name: {
                "sha256": hashlib.sha256(content).hexdigest(),
                "size": len(content),
            }
            for name, content in sorted(entries.items())
        },
    }
    manifest_bytes = (
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
    ).encode()
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for name, content in [*sorted(entries.items()), ("manifest.json", manifest_bytes)]:
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o644 << 16
                bundle.writestr(info, content)
        temporary.replace(target)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise
    return {**manifest, "path": str(target), "sha256": _sha256(target)}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
