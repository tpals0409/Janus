import json
from pathlib import Path

import pytest

from janus_server.workflow_template import (
    TemplateValidationError,
    WorkflowTemplate,
    load_output_file,
    validate_output,
)
from janus_server.workflow import CheckpointStore, ExecutionLimits, WorkflowEngine


SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
    "additionalProperties": False,
}


class InvalidThenValidOutput:
    def __init__(self, marker: Path):
        self.marker = marker

    def __call__(self, _stage, _context):
        if not self.marker.exists():
            self.marker.write_text("attempted", encoding="utf-8")
            return {"summary": 3}
        return {"summary": "validated"}


class FanoutSummaryOutput:
    def __call__(self, _stage, context):
        return {"summary": f"worker-{context.fanout_index}"}


def stage(stage_id="explore", **updates):
    value = {
        "id": stage_id,
        "role": "summarizer",
        "needs": [],
        "fanout": {"max": 1},
        "write": "none",
        "context": {"request": "${inputs.request}"},
        "task": "Explore ${inputs.request}",
        "output": {"path": f"outputs/{stage_id}.json", "schema": SCHEMA},
        "check": None,
        "on_fail": "human",
    }
    value.update(updates)
    return value


def template(*stages, **updates):
    value = {"version": 1, "inputs": {"request": "demo"}, "stages": list(stages)}
    value.update(updates)
    return value


def test_declarative_template_compiles_strict_stages():
    explore = stage(fanout={"max": 3})
    plan = stage(
        "plan",
        role="coder",
        needs=["explore"],
        context={"research": "${stages.explore.output}"},
        task="Plan from ${stages.explore.output}",
    )
    loaded = WorkflowTemplate.from_dict(template(explore, plan))
    assert [item.stage.id for item in loaded.stages] == ["explore", "plan"]
    assert loaded.stages[0].stage.fanout == 3


def test_checked_in_standard_template_compiles_all_five_stages():
    path = Path(__file__).parents[1] / "config" / "workflows" / "standard.yaml"
    loaded = WorkflowTemplate.load(path)
    assert [item.stage.id for item in loaded.stages] == [
        "explore", "plan", "implement", "review", "verify"
    ]


@pytest.mark.parametrize(
    "payload, message",
    [
        (template(stage(), slots=2), "template fields"),
        (template({**stage(), "model": "local"}), "engine-owned"),
        (template(stage(fanout={})), "required max"),
        (template(stage(fanout={"max": 4})), "1..3"),
        (template(stage(task="${env.SECRET}")), "invalid dynamic binding"),
        (template(stage(task="${inputs.missing}")), "undeclared input"),
        (template(stage("plan", task="${stages.explore.output}")), "non-dependency"),
    ],
)
def test_template_rejects_engine_fields_and_invalid_bindings(payload, message):
    with pytest.raises(TemplateValidationError, match=message):
        WorkflowTemplate.from_dict(payload)


def test_template_rejects_cycles_and_unknown_stage_outputs():
    left = stage("left", needs=["right"])
    right = stage("right", needs=["left"])
    with pytest.raises(TemplateValidationError, match="cycle"):
        WorkflowTemplate.from_dict(template(left, right))


def test_output_contract_validates_file_and_rejects_mismatch(tmp_path: Path):
    loaded = WorkflowTemplate.from_dict(template(stage()))
    contract = loaded.stages[0].output
    target = tmp_path / contract.path
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({"summary": "ok"}), encoding="utf-8")
    assert load_output_file(contract, tmp_path) == {"summary": "ok"}

    with pytest.raises(TemplateValidationError, match="extra"):
        validate_output(contract, {"summary": "ok", "transcript": "leak"})
    with pytest.raises(TemplateValidationError, match="must be string"):
        validate_output(contract, {"summary": 3})


def test_worktree_output_becomes_the_owned_file_and_requires_check():
    implement = stage(
        "implement",
        role="coder",
        write="worktree",
        output={"path": "src/result.json", "schema": SCHEMA},
        check="test -f src/result.json",
    )
    loaded = WorkflowTemplate.from_dict(template(implement))
    assert loaded.stages[0].stage.owns == ("src/result.json",)
    assert loaded.stages[0].stage.check == "test -f src/result.json"


def test_engine_retries_schema_mismatch_and_atomically_persists_valid_output(tmp_path: Path):
    loaded = WorkflowTemplate.from_dict(template(stage()))
    item = loaded.stages[0]
    engine = WorkflowEngine(
        loaded.engine_stages(),
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    result = engine.run_isolated(
        InvalidThenValidOutput(tmp_path / "attempt-marker"),
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=1),
        output_contracts={item.stage.id: item.output},
        output_root=tmp_path,
    )

    assert result["attempts"] == {"explore": 2}
    assert result["outputs"] == {"explore": {"path": "outputs/explore.json"}}
    assert json.loads((tmp_path / "outputs/explore.json").read_text()) == {
        "summary": "validated"
    }
    assert result["attempt_errors"]["explore"][0]["type"] == "WorkerExecutionError"


def test_engine_validates_each_fanout_summary_file_against_contract(tmp_path: Path):
    loaded = WorkflowTemplate.from_dict(template(stage(fanout={"max": 2})))
    item = loaded.stages[0]
    engine = WorkflowEngine(
        loaded.engine_stages(),
        CheckpointStore(tmp_path / "checkpoint.json"),
    )
    result = engine.run_isolated(
        FanoutSummaryOutput(),
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        output_contracts={item.stage.id: item.output},
        output_root=tmp_path,
    )
    assert len(result["outputs"]["explore"]["summaries"]) == 2
