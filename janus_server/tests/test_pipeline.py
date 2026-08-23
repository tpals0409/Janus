from pathlib import Path

import pytest

from janus_server.pipeline import (
    InvalidPlanSpec,
    PlanSpec,
    ReviewFeedback,
    ReviewLoop,
    ReviewPacket,
)


def task(task_id: str, owns: list[str], **updates) -> dict:
    value = {
        "id": task_id,
        "purpose": f"implement {task_id}",
        "output_format": "working code plus passing focused tests",
        "allowed_tools": ["read", "search", "edit", "test"],
        "boundaries": ["do not edit outside owned paths", "do not call the network"],
        "owns": owns,
        "check": "test -f expected.txt",
    }
    value.update(updates)
    return value


def test_plan_spec_validates_and_derives_isolated_implement_stages(tmp_path: Path):
    plan = PlanSpec.from_dict({"tasks": [
        task("api", ["src/api/"]),
        task("docs", ["README.md"]),
    ]})

    stages = plan.implement_stages()
    assert [(stage.id, stage.needs, stage.write, stage.owns, stage.check) for stage in stages] == [
        ("api", ("plan",), "worktree", ("src/api/",), "test -f expected.txt"),
        ("docs", ("plan",), "worktree", ("README.md",), "test -f expected.txt"),
    ]
    path = tmp_path / "plan.json"
    plan.save(path)
    assert PlanSpec.from_dict(__import__("json").loads(path.read_text())) == plan


@pytest.mark.parametrize(
    "payload, message",
    [
        ({"tasks": [task("api", ["src/"]), task("api", ["docs/"])]}, "ids"),
        ({"tasks": [task("api", ["src/"]), task("docs", ["src/api/"])]}, "overlap"),
        ({"tasks": [task("api", ["../secret"])]}, "unsafe ownership"),
        ({"tasks": [task("api", ["src/"], allowed_tools=["shell"])]}, "unpermitted"),
        ({"tasks": [task("api", ["src/"], boundaries=[])]}, "boundaries"),
        ({"tasks": [task("api", ["src/"], check="")]}, "worker check"),
    ],
)
def test_plan_spec_rejects_unsafe_or_incomplete_partitions(payload, message):
    with pytest.raises(InvalidPlanSpec, match=message):
        PlanSpec.from_dict(payload)


def test_plan_spec_rejects_extra_worker_context():
    value = task("api", ["src/"])
    value["conversation"] = "private coder transcript"
    with pytest.raises(InvalidPlanSpec, match="fields must be exactly"):
        PlanSpec.from_dict({"tasks": [value]})


def test_review_packet_accepts_only_plan_and_diff(tmp_path: Path):
    plan = {"tasks": [task("api", ["src/"])]}
    packet = ReviewPacket.from_dict({"plan": plan, "diff": "diff --git a/x b/x\n+ok"})
    path = tmp_path / "review-packet.json"
    packet.save(path)
    assert set(__import__("json").loads(path.read_text())) == {"plan", "diff"}

    with pytest.raises(InvalidPlanSpec, match="exactly plan and diff"):
        ReviewPacket.from_dict({
            "plan": plan,
            "diff": "diff",
            "coder_conversation": "secret transcript",
        })


def finding(finding_id: str = "bug") -> dict:
    return {
        "id": finding_id,
        "path": "src/api.py",
        "line": 4,
        "severity": "high",
        "message": "incorrect result",
    }


def test_review_feedback_contract_rejects_inconsistent_verdicts():
    assert ReviewFeedback.from_dict({"verdict": "approved", "findings": []}).verdict == "approved"
    with pytest.raises(InvalidPlanSpec, match="cannot contain"):
        ReviewFeedback.from_dict({"verdict": "approved", "findings": [finding()]})
    with pytest.raises(InvalidPlanSpec, match="requires findings"):
        ReviewFeedback.from_dict({"verdict": "changes_requested", "findings": []})


def test_review_loop_returns_structured_feedback_twice_then_needs_human(tmp_path: Path):
    loop = ReviewLoop(tmp_path)
    first = loop.record({"verdict": "changes_requested", "findings": [finding("round_one")]})
    second = loop.record({"verdict": "changes_requested", "findings": [finding("round_two")]})

    assert first == {"round": 1, "status": "revise", "path": "review-feedback-1.json"}
    assert second == {"round": 2, "status": "needs_human", "path": "review-feedback-2.json"}
    assert (tmp_path / first["path"]).is_file()
    assert (tmp_path / second["path"]).is_file()
    with pytest.raises(RuntimeError, match="already ended"):
        loop.record({"verdict": "approved", "findings": []})


def test_review_loop_can_approve_without_another_round(tmp_path: Path):
    loop = ReviewLoop(tmp_path)
    assert loop.record({"verdict": "approved", "findings": []})["status"] == "approved"
