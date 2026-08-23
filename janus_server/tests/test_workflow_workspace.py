"""Write-worker worktree lifecycle integration tests."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from janus_server.workflow_workspace import WriteWorkspaceManager
from janus_server.workflow import (
    CheckpointStore,
    ExecutionLimits,
    HumanInterventionRequired,
    Stage,
    WorkerExecutionError,
    WorkflowEngine,
)
from janus_server.ownership import OwnershipViolation


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def make_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.email", "janus@example.test")
    git(repo, "config", "user.name", "Janus Test")
    (repo / "base.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", "base.txt")
    git(repo, "commit", "-m", "initial")
    return repo


def isolated_write_worker(_stage, context):
    root = Path(context.workspace_root)
    (root / "worker.txt").write_text("written in isolated worktree\n", encoding="utf-8")
    return {"root": context.workspace_root}


def isolated_outside_write_worker(_stage, context):
    root = Path(context.workspace_root)
    (root / "outside.txt").write_text("bypass\n", encoding="utf-8")
    return {"root": context.workspace_root}


def tool_outside_write_worker(_stage, context):
    return context.call_tool("write_file", {"path": "outside.txt", "content": "x"})


class MustNotRunDispatcher:
    def __init__(self, marker: str):
        self.marker = marker

    def __call__(self, _name, _arguments):
        Path(self.marker).write_text("dispatched", encoding="utf-8")
        return {"unexpected": True}


def conflicting_write_worker(stage, context):
    Path(context.workspace_root, "conflict.txt").write_text(
        f"{stage.id}\n", encoding="utf-8"
    )
    return {"stage": stage.id}


def resolving_fixer(_stage, context):
    Path(context.workspace_root, "conflict.txt").write_text(
        "resolved\n", encoding="utf-8"
    )
    return {"resolved": True}


def outside_fixer(_stage, context):
    root = Path(context.workspace_root)
    (root / "conflict.txt").write_text("resolved\n", encoding="utf-8")
    (root / "outside.txt").write_text("not allowed\n", encoding="utf-8")
    return {"resolved": True}


def test_write_attempt_is_recoverable_then_committed_and_worktree_removed(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/one",
    )
    workspace = manager.provision("implement", 1)
    root = Path(workspace["root_path"])
    (root / "feature.txt").write_text("isolated\n", encoding="utf-8")

    recovered = manager.provision("implement", 1)
    assert recovered["recovered"]
    assert recovered["root_path"] == workspace["root_path"]
    assert recovered["branch_name"] == workspace["branch_name"]

    completed = manager.complete(recovered, stage_id="implement", attempt=1)
    assert completed["commit_sha"]
    assert completed["archived"]
    assert completed["branch_preserved"]
    assert not root.exists()
    assert git(repo, "status", "--porcelain") == ""
    assert not (repo / "feature.txt").exists()


def test_failed_write_attempt_discards_changes_and_deletes_temporary_branch(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/two",
    )
    workspace = manager.provision("implement", 1)
    root = Path(workspace["root_path"])
    branch = workspace["branch_name"]
    (root / "partial.txt").write_text("discard me\n", encoding="utf-8")

    failed = manager.fail(workspace)

    assert failed["removed"]
    assert failed["branch_deleted"]
    assert not root.exists()
    branches = git(repo, "branch", "--format=%(refname:short)").splitlines()
    assert branch not in branches
    assert git(repo, "status", "--porcelain") == ""


def test_engine_provisions_write_stage_and_archives_committed_worktree(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/engine",
    )
    engine = WorkflowEngine(
        [
            Stage(
                "implement",
                write="worktree",
                owns=("worker.txt",),
                check="test -f worker.txt",
            )
        ],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    result = engine.run_isolated(
        isolated_write_worker,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        workspace_manager=manager,
        integration_command="test -f worker.txt",
    )

    record = result["worktrees"]["implement"][0]
    assert record["status"] == "completed"
    assert record["commit_sha"]
    assert record["worker_verification"]["exit_code"] == 0
    assert record["archived"]
    assert not Path(record["root_path"]).exists()
    assert not (repo / "worker.txt").exists()
    assert git(repo, "status", "--porcelain") == ""
    assert result["integration"]["status"] == "verified"
    assert result["integration"]["verification"]["exit_code"] == 0
    assert result["integration"]["merged_branches"] == [record["branch_name"]]
    manager.service.delete_branch(
        repo_path=repo, branch_name=record["branch_name"]
    )
    manager.service.delete_branch(
        repo_path=repo, branch_name=result["integration"]["branch_name"]
    )


def test_failed_worker_check_blocks_commit_and_cleans_worktree(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/worker-check-failure",
    )
    engine = WorkflowEngine(
        [
            Stage(
                "implement",
                write="worktree",
                owns=("worker.txt",),
                check="test -f missing.txt",
            )
        ],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    with pytest.raises(WorkerExecutionError, match="worker check failed"):
        engine.run_isolated(
            isolated_write_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
            workspace_manager=manager,
        )

    record = engine.snapshot()["worktrees"]["implement"][0]
    assert record["worker_verification"]["exit_code"] != 0
    assert record["status"] == "failed_cleaned"
    assert not record.get("commit_sha")
    assert not Path(record["root_path"]).exists()
    assert git(repo, "branch", "--format=%(refname:short)") == "main"


def test_engine_rejects_and_cleans_direct_write_outside_owned_partition(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/engine-violation",
    )
    engine = WorkflowEngine(
        [Stage("implement", write="worktree", owns=("allowed.txt",))],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    with pytest.raises(OwnershipViolation, match="outside.txt"):
        engine.run_isolated(
            isolated_outside_write_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
            workspace_manager=manager,
        )

    record = engine.snapshot()["worktrees"]["implement"][0]
    assert record["status"] == "failed_cleaned"
    assert record["branch_deleted"]
    assert not Path(record["root_path"]).exists()


def test_tool_write_outside_partition_is_rejected_before_dispatch(tmp_path):
    repo = make_repo(tmp_path)
    marker = tmp_path / "dispatcher-ran"
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/tool-gate",
    )
    engine = WorkflowEngine(
        [Stage("implement", write="worktree", owns=("allowed.txt",))],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    with pytest.raises(OwnershipViolation, match="outside.txt"):
        engine.run_isolated(
            tool_outside_write_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=1, retries=0),
            tool_dispatcher=MustNotRunDispatcher(str(marker)),
            workspace_manager=manager,
        )

    assert not marker.exists()
    record = engine.snapshot()["worktrees"]["implement"][0]
    assert record["status"] == "failed_cleaned"


def _cleanup_integration_branches(manager, result):
    integration = result.get("integration") or {}
    if integration.get("root_path") and Path(integration["root_path"]).exists():
        manager.fail(integration)
    for records in result["worktrees"].values():
        for record in records:
            branch = record.get("branch_name")
            if branch:
                manager.service.delete_branch(
                    repo_path=manager.repo_path, branch_name=branch
                )
    branch = integration.get("branch_name")
    if branch:
        manager.service.delete_branch(
            repo_path=manager.repo_path, branch_name=branch
        )


def test_single_fixer_resolves_merge_conflict_then_verifies(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/fixer-success",
    )
    engine = WorkflowEngine(
        [
            Stage("write_a", write="worktree", owns=("conflict.txt",)),
            Stage("write_b", write="worktree", owns=("conflict.txt",)),
        ],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    result = engine.run_isolated(
        conflicting_write_worker,
        ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
        workspace_manager=manager,
        integration_command="grep -qx resolved conflict.txt",
        conflict_fixer=resolving_fixer,
        conflict_fixer_limits=ExecutionLimits(
            timeout_seconds=2, max_tool_calls=0, retries=9
        ),
    )

    assert result["integration"]["status"] == "verified"
    assert result["integration"]["fixer_attempts"] == 1
    assert result["integration"]["fixer_changed_paths"] == ["conflict.txt"]
    assert result["worker_spawns"] == 3
    _cleanup_integration_branches(manager, result)


def test_fixer_partition_violation_stops_at_human_handoff_after_one_attempt(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/fixer-handoff",
    )
    engine = WorkflowEngine(
        [
            Stage("write_a", write="worktree", owns=("conflict.txt",)),
            Stage("write_b", write="worktree", owns=("conflict.txt",)),
        ],
        CheckpointStore(tmp_path / "checkpoint.json"),
    )

    with pytest.raises(HumanInterventionRequired):
        engine.run_isolated(
            conflicting_write_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
            workspace_manager=manager,
            integration_command="grep -qx resolved conflict.txt",
            conflict_fixer=outside_fixer,
            conflict_fixer_limits=ExecutionLimits(
                timeout_seconds=2, max_tool_calls=0, retries=5
            ),
        )

    result = engine.snapshot()
    assert result["integration"]["status"] == "needs_human"
    assert result["integration"]["fixer_attempts"] == 1
    assert "outside.txt" in result["integration"]["handoff_reason"]
    with pytest.raises(HumanInterventionRequired, match="integration"):
        WorkflowEngine.resume(
            engine.stages,
            CheckpointStore(tmp_path / "checkpoint.json"),
        ).run_isolated(
            conflicting_write_worker,
            ExecutionLimits(timeout_seconds=2, max_tool_calls=0, retries=0),
            workspace_manager=manager,
            integration_command="grep -qx resolved conflict.txt",
            conflict_fixer=resolving_fixer,
        )
    _cleanup_integration_branches(manager, result)


def test_reconcile_removes_only_unrecorded_worktree_for_same_pipeline(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/reconcile",
    )
    orphan = manager.provision("implement", 1)
    other = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="other/pipeline",
    ).provision("implement", 1)

    recovered = manager.reconcile(set())

    assert [item["root_path"] for item in recovered] == [orphan["root_path"]]
    assert not Path(orphan["root_path"]).exists()
    assert Path(other["root_path"]).exists()
    WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="other/pipeline",
    ).fail(other)


def test_finalizing_recovery_preserves_archived_success_branch(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/finalize",
    )
    workspace = manager.provision("implement", 1)
    root = Path(workspace["root_path"])
    (root / "done.txt").write_text("done\n", encoding="utf-8")
    completed = manager.complete(workspace, stage_id="implement", attempt=1)
    stale_checkpoint = {
        **workspace,
        "status": "finalizing",
        "stage_id": "implement",
        "attempt": 1,
    }

    recovered = manager.recover_complete(
        stale_checkpoint, stage_id="implement", attempt=1
    )

    assert recovered["status"] == "completed_recovered"
    assert recovered["commit_sha"] == completed["commit_sha"]
    assert recovered["branch_preserved"]
    manager.service.delete_branch(
        repo_path=repo, branch_name=recovered["branch_name"]
    )


def test_git_changeset_rejects_direct_write_outside_partition(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/ownership",
    )
    workspace = manager.provision("implement", 1)
    root = Path(workspace["root_path"])
    (root / "allowed.txt").write_text("allowed\n", encoding="utf-8")
    (root / "outside.txt").write_text("violation\n", encoding="utf-8")

    with pytest.raises(OwnershipViolation, match="outside.txt"):
        manager.validate_ownership(workspace, ("allowed.txt",))

    manager.fail(workspace)
    assert not root.exists()


def test_write_branches_merge_in_order_then_verify_once_in_integration_worktree(tmp_path):
    repo = make_repo(tmp_path)
    manager = WriteWorkspaceManager(
        storage_root=tmp_path / "worktrees",
        repo_path=repo,
        base_ref="main",
        pipeline_id="pipeline/integration",
    )
    records = []
    for attempt, filename in enumerate(("one.txt", "two.txt"), 1):
        workspace = manager.provision(f"write-{attempt}", 1)
        Path(workspace["root_path"], filename).write_text(
            f"{attempt}\n", encoding="utf-8"
        )
        records.append(manager.complete(
            workspace, stage_id=f"write-{attempt}", attempt=1
        ))
    marker = tmp_path / "verified-once.txt"
    command = (
        "test -f one.txt && test -f two.txt && "
        f"printf verified > '{marker}'"
    )

    result = manager.integrate(records, verification_command=command)

    assert result["status"] == "verified"
    assert result["merged_branches"] == [record["branch_name"] for record in records]
    assert marker.read_text(encoding="utf-8") == "verified"
    assert result["verification"]["exit_code"] == 0
    assert result["archived"]
    assert result["source_branches_preserved"]
    assert not Path(result["root_path"]).exists()
    assert git(repo, "show", f"{result['branch_name']}:one.txt") == "1"
    assert git(repo, "show", f"{result['branch_name']}:two.txt") == "2"
    assert not (repo / "one.txt").exists()
    assert not (repo / "two.txt").exists()
    for record in records:
        manager.service.delete_branch(
            repo_path=repo, branch_name=record["branch_name"]
        )
    manager.service.delete_branch(
        repo_path=repo, branch_name=result["branch_name"]
    )
