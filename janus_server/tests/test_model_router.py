"""Engine-owned role model routing tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from janus_server.model_router import ModelRouter, ModelRoutingError


def valid_config():
    return {
        "version": 1,
        "models": {
            "local": {"key": "qwen3.8-27b", "provider": "mlx-local"},
        },
        "roles": {
            "coder": "local",
            "reviewer": "local",
            "summarizer": "local",
        },
    }


def test_repository_models_yaml_routes_all_required_roles():
    path = Path(__file__).resolve().parents[1] / "config" / "models.yaml"
    router = ModelRouter.load(path)

    assert {router.resolve(role)["key"] for role in (
        "coder", "reviewer", "summarizer"
    )} == {"qwen3.8-27b"}
    assert router.resolve("reviewer")["provider"] == "mlx-local"


def test_missing_role_unknown_alias_and_remote_provider_are_rejected():
    missing = valid_config()
    missing["roles"].pop("reviewer")
    with pytest.raises(ModelRoutingError, match="exactly"):
        ModelRouter(missing)

    unknown = valid_config()
    unknown["roles"]["coder"] = "missing"
    with pytest.raises(ModelRoutingError, match="unknown model alias"):
        ModelRouter(unknown)

    remote = valid_config()
    remote["models"]["local"]["provider"] = "openai"
    with pytest.raises(ModelRoutingError, match="non-local"):
        ModelRouter(remote)
