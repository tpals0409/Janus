"""Strict, non-programmable YAML workflow template loader."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

import yaml

from .ownership import InvalidPartition, normalize_partition
from .workflow import MAX_FANOUT, Stage, WorkflowDefinitionError, _validate_stages


class TemplateValidationError(ValueError):
    pass


TOP_FIELDS = {"version", "inputs", "stages"}
STAGE_FIELDS = {
    "id", "role", "needs", "fanout", "write", "context", "task", "output",
    "check", "on_fail",
}
FANOUT_FIELDS = {"max"}
OUTPUT_FIELDS = {"path", "schema"}
FORBIDDEN_ENGINE_FIELDS = {
    "checkpoint", "checkpoints", "slot", "slots", "model", "models",
    "retries", "timeout", "max_worker_spawns",
}
BINDING = re.compile(r"\$\{(inputs\.([a-zA-Z_][\w-]*)|stages\.([a-z][a-z0-9_-]*)\.output)\}")


@dataclass(frozen=True)
class OutputContract:
    path: str
    schema: dict[str, Any]


@dataclass(frozen=True)
class TemplateStage:
    stage: Stage
    context: dict[str, str]
    task: str
    output: OutputContract


@dataclass(frozen=True)
class WorkflowTemplate:
    inputs: dict[str, Any]
    stages: tuple[TemplateStage, ...]

    @classmethod
    def load(cls, path: Path) -> "WorkflowTemplate":
        try:
            value = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        except (OSError, yaml.YAMLError) as exc:
            raise TemplateValidationError(f"cannot load workflow template: {exc}") from exc
        return cls.from_dict(value)

    @classmethod
    def from_dict(cls, value: Any) -> "WorkflowTemplate":
        if not isinstance(value, dict) or set(value) != TOP_FIELDS:
            actual = sorted(value) if isinstance(value, dict) else type(value).__name__
            raise TemplateValidationError(
                f"template fields must be exactly {sorted(TOP_FIELDS)}; got {actual}"
            )
        if value["version"] != 1:
            raise TemplateValidationError("template version must be 1")
        inputs = value["inputs"]
        if not isinstance(inputs, dict) or any(not str(key).strip() for key in inputs):
            raise TemplateValidationError("inputs must be a mapping with non-empty names")
        raw_stages = value["stages"]
        if not isinstance(raw_stages, list) or not raw_stages:
            raise TemplateValidationError("stages must be a non-empty list")
        compiled = tuple(_parse_stage(item) for item in raw_stages)
        try:
            _validate_stages([item.stage for item in compiled])
        except WorkflowDefinitionError as exc:
            raise TemplateValidationError(str(exc)) from exc
        _validate_bindings(compiled, set(map(str, inputs)))
        return cls(dict(inputs), compiled)

    def engine_stages(self) -> tuple[Stage, ...]:
        return tuple(item.stage for item in self.stages)


def _parse_stage(value: Any) -> TemplateStage:
    if not isinstance(value, dict):
        raise TemplateValidationError("each stage must be a mapping")
    forbidden = sorted(set(value) & FORBIDDEN_ENGINE_FIELDS)
    if forbidden:
        raise TemplateValidationError(f"engine-owned fields are forbidden: {forbidden}")
    if set(value) != STAGE_FIELDS:
        raise TemplateValidationError(
            f"stage fields must be exactly {sorted(STAGE_FIELDS)}; got {sorted(value)}"
        )
    fanout = value["fanout"]
    if not isinstance(fanout, dict) or set(fanout) != FANOUT_FIELDS:
        raise TemplateValidationError("fanout must contain exactly required max")
    maximum = fanout["max"]
    if not isinstance(maximum, int) or isinstance(maximum, bool) or not 1 <= maximum <= MAX_FANOUT:
        raise TemplateValidationError(f"fanout.max must be 1..{MAX_FANOUT}")
    needs = value["needs"]
    if not isinstance(needs, list) or any(not isinstance(item, str) for item in needs):
        raise TemplateValidationError("needs must be a list of stage ids")
    context = value["context"]
    if not isinstance(context, dict) or any(
        not isinstance(key, str) or not isinstance(item, str)
        for key, item in context.items()
    ):
        raise TemplateValidationError("context must map names to binding strings")
    task = value["task"]
    if not isinstance(task, str) or not task.strip():
        raise TemplateValidationError("task must be a non-empty string")
    output = value["output"]
    if not isinstance(output, dict) or set(output) != OUTPUT_FIELDS:
        raise TemplateValidationError("output must contain exactly path and schema")
    try:
        output_path = normalize_partition(output["path"])
    except InvalidPartition as exc:
        raise TemplateValidationError(f"unsafe output path: {exc}") from exc
    if output_path.endswith("/"):
        raise TemplateValidationError("output path must name a file")
    schema = output["schema"]
    _validate_schema_definition(schema)
    write = value["write"]
    owns = (output_path,) if write == "worktree" else ()
    stage = Stage(
        id=str(value["id"]),
        needs=tuple(needs),
        on_fail=value["on_fail"],
        write=write,
        owns=owns,
        role=str(value["role"]),
        fanout=maximum,
        check=value["check"],
    )
    return TemplateStage(stage, dict(context), task, OutputContract(output_path, schema))


def _validate_bindings(stages: tuple[TemplateStage, ...], inputs: set[str]) -> None:
    by_id = {item.stage.id: item for item in stages}

    def ancestors(stage_id: str) -> set[str]:
        result: set[str] = set()
        for dependency in by_id[stage_id].stage.needs:
            result.add(dependency)
            result.update(ancestors(dependency))
        return result

    for item in stages:
        allowed_stages = ancestors(item.stage.id)
        for location, text in [
            (f"stage {item.stage.id} task", item.task),
            *[(f"stage {item.stage.id} context.{key}", value) for key, value in item.context.items()],
        ]:
            stripped = BINDING.sub("", text)
            if "${" in stripped:
                raise TemplateValidationError(f"{location} contains an invalid dynamic binding")
            for match in BINDING.finditer(text):
                input_name, stage_id = match.group(2), match.group(3)
                if input_name is not None and input_name not in inputs:
                    raise TemplateValidationError(f"{location} references undeclared input {input_name!r}")
                if stage_id is not None and stage_id not in allowed_stages:
                    raise TemplateValidationError(
                        f"{location} references non-dependency stage output {stage_id!r}"
                    )


def _validate_schema_definition(schema: Any) -> None:
    if not isinstance(schema, dict) or schema.get("type") != "object":
        raise TemplateValidationError("output schema must be an object JSON schema")
    if not isinstance(schema.get("properties"), dict):
        raise TemplateValidationError("output schema requires properties")
    if not isinstance(schema.get("required"), list):
        raise TemplateValidationError("output schema requires a required list")
    if schema.get("additionalProperties") is not False:
        raise TemplateValidationError("output schema must forbid additionalProperties")
    if set(schema["required"]) - set(schema["properties"]):
        raise TemplateValidationError("output schema requires undeclared properties")


def validate_output(contract: OutputContract, value: Any) -> None:
    """Validate the strict JSON-object subset accepted by workflow templates."""
    schema = contract.schema
    if not isinstance(value, dict):
        raise TemplateValidationError("stage output must be a JSON object")
    properties = schema["properties"]
    missing = sorted(set(schema["required"]) - set(value))
    extra = sorted(set(value) - set(properties))
    if missing or extra:
        raise TemplateValidationError(f"output fields mismatch: missing={missing}, extra={extra}")
    types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "array": list,
        "object": dict,
    }
    for key, item in value.items():
        expected = properties[key].get("type")
        python_type = types.get(expected)
        if python_type is None:
            raise TemplateValidationError(f"unsupported output schema type: {expected!r}")
        if not isinstance(item, python_type) or expected in {"integer", "number"} and isinstance(item, bool):
            raise TemplateValidationError(f"output field {key!r} must be {expected}")


def load_output_file(contract: OutputContract, root: Path) -> Any:
    path = (Path(root) / contract.path).resolve()
    base = Path(root).resolve()
    if path != base and base not in path.parents:
        raise TemplateValidationError("output path escapes artifact root")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TemplateValidationError(f"cannot read declared output: {exc}") from exc
    validate_output(contract, value)
    return value
