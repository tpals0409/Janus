import hashlib
import json
from pathlib import Path
import zipfile

from janus_server.airgap import assert_local_artifacts
from janus_server.orchestration_bundle import BUNDLE_COMPONENTS, create_orchestration_bundle


def test_airgap_bundle_contains_exact_runtime_template_and_schema_components(tmp_path: Path):
    project = Path(__file__).parents[1]
    output = tmp_path / "orchestration-airgap.zip"
    result = create_orchestration_bundle(project, output)

    with zipfile.ZipFile(output) as bundle:
        assert set(bundle.namelist()) == {*BUNDLE_COMPONENTS, "manifest.json"}
        manifest = json.loads(bundle.read("manifest.json"))
        assert set(manifest["components"]) == set(BUNDLE_COMPONENTS)
        for name, metadata in manifest["components"].items():
            content = bundle.read(name)
            assert hashlib.sha256(content).hexdigest() == metadata["sha256"]
            assert len(content) == metadata["size"]
        assert bundle.read("config/workflows/standard.yaml")
        assert bundle.read("janus_server/workflow_template.py")
        assert bundle.read("janus_server/airgap.py")
    assert result["network_policy"] == "loopback_and_unix_only"
    assert_local_artifacts(result)


def test_airgap_bundle_is_byte_reproducible(tmp_path: Path):
    project = Path(__file__).parents[1]
    first = tmp_path / "first.zip"
    second = tmp_path / "second.zip"
    create_orchestration_bundle(project, first)
    create_orchestration_bundle(project, second)
    assert first.read_bytes() == second.read_bytes()
