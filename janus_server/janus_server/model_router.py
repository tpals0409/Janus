"""Validated engine-owned role to local-model routing."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


REQUIRED_ROLES = frozenset({"coder", "reviewer", "summarizer"})
LOCAL_PROVIDERS = frozenset({"mlx-local"})


class ModelRoutingError(ValueError):
    pass


class ModelRouter:
    def __init__(self, config: dict[str, Any]):
        if config.get("version") != 1:
            raise ModelRoutingError("models config version must be 1")
        models = config.get("models")
        roles = config.get("roles")
        if not isinstance(models, dict) or not models:
            raise ModelRoutingError("models must be a non-empty mapping")
        if not isinstance(roles, dict) or set(roles) != REQUIRED_ROLES:
            raise ModelRoutingError(
                f"roles must contain exactly {sorted(REQUIRED_ROLES)}"
            )
        normalized: dict[str, dict[str, str]] = {}
        for alias, value in models.items():
            if not isinstance(alias, str) or not alias.strip() or not isinstance(value, dict):
                raise ModelRoutingError("each model alias must map to an object")
            key = value.get("key")
            provider = value.get("provider")
            if not isinstance(key, str) or not key.strip():
                raise ModelRoutingError(f"model {alias!r} requires key")
            if provider not in LOCAL_PROVIDERS:
                raise ModelRoutingError(
                    f"model {alias!r} uses non-local provider {provider!r}"
                )
            normalized[alias] = {"alias": alias, "key": key, "provider": provider}
        for role, alias in roles.items():
            if alias not in normalized:
                raise ModelRoutingError(
                    f"role {role!r} references unknown model alias {alias!r}"
                )
        self._models = normalized
        self._roles = dict(roles)

    @classmethod
    def load(cls, path: str | Path) -> "ModelRouter":
        try:
            value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise ModelRoutingError(f"cannot load models config: {exc}") from exc
        if not isinstance(value, dict):
            raise ModelRoutingError("models config root must be a mapping")
        return cls(value)

    def resolve(self, role: str) -> dict[str, str]:
        try:
            alias = self._roles[str(role)]
        except KeyError as exc:
            raise ModelRoutingError(f"unknown model role: {role!r}") from exc
        return dict(self._models[alias])

    def snapshot(self) -> dict[str, Any]:
        return {
            "models": {alias: dict(value) for alias, value in self._models.items()},
            "roles": dict(self._roles),
        }
