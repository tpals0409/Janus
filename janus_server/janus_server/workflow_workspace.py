"""Write-worker worktree lifecycle built on the guarded WorkspaceService."""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .workspace_service import WorkspaceService
from .ownership import OwnershipViolation, owns_path
from .workspace import WorkspaceContext
from . import verification


def _safe_id(value: str, limit: int = 24) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-") or "stage"
    digest = hashlib.sha256(str(value).encode("utf-8")).hexdigest()[:8]
    return f"{slug[:limit].rstrip('-')}-{digest}"


class WriteWorkspaceManager:
    """Provision exactly one owned worktree for each write-worker attempt."""

    def __init__(
        self,
        *,
        storage_root: str | Path,
        repo_path: str | Path,
        base_ref: str,
        pipeline_id: str,
    ):
        self.service = WorkspaceService(storage_root)
        validated = self.service.validate_repo(repo_path, base_ref)
        self.repo_path = Path(validated["repo_path"])
        self.base_ref = str(base_ref)
        self.pipeline_id = _safe_id(pipeline_id)

    def workspace_id(self, stage_id: str, attempt: int) -> str:
        if attempt <= 0:
            raise ValueError("attempt must be positive")
        return f"wf-{self.pipeline_id}-{_safe_id(stage_id)}-a{attempt}"

    def provision(self, stage_id: str, attempt: int) -> dict:
        workspace_id = self.workspace_id(stage_id, attempt)
        target = self.service._target(workspace_id)
        registered = self.service._registered(self.repo_path, target)
        existing_root = target if registered is not None else None
        existing_branch = (
            str(registered.get("branch_name") or "") if registered is not None else None
        )
        return self.service.prepare(
            workspace_id=workspace_id,
            task_id=f"{self.pipeline_id}-{stage_id}-{attempt}",
            title=f"workflow {stage_id} attempt {attempt}",
            repo_path=self.repo_path,
            base_ref=self.base_ref,
            existing_root=existing_root,
            existing_branch=existing_branch,
        )

    def complete(self, workspace: dict, *, stage_id: str, attempt: int) -> dict:
        root = workspace["root_path"]
        status = self.service.inspect(self.repo_path, root)
        commit = None
        if status["dirty"]:
            commit = self.service.commit_changes(
                repo_path=self.repo_path,
                root_path=root,
                message=f"janus workflow: {stage_id} attempt {attempt}",
            )
        archived = self.service.archive(repo_path=self.repo_path, root_path=root)
        return {
            **workspace,
            "status": "completed",
            "commit_sha": (commit or {}).get("commit_sha"),
            "archived": archived["removed"],
            "branch_preserved": archived["branch_preserved"],
        }

    def validate_ownership(self, workspace: dict, partitions: tuple[str, ...]) -> list[str]:
        changes = self.service.changeset(
            repo_path=self.repo_path,
            root_path=workspace["root_path"],
            base_ref=self.base_ref,
        )
        paths: set[str] = set()
        for entries in changes["sections"].values():
            for entry in entries:
                paths.add(str(entry["path"]))
                if entry.get("old_path"):
                    paths.add(str(entry["old_path"]))
        violations = sorted(path for path in paths if not owns_path(partitions, path))
        if violations:
            raise OwnershipViolation(
                f"write outside declared partition: {violations}"
            )
        return sorted(paths)

    def verify(
        self,
        workspace: dict,
        *,
        stage_id: str,
        attempt: int,
        command: str,
        timeout: float = 120,
    ) -> dict:
        context = WorkspaceContext(
            root=Path(workspace["root_path"]),
            task_id=f"{self.pipeline_id}-{stage_id}-{attempt}",
            workspace_id=self.workspace_id(stage_id, attempt),
        ).for_dispatch(f"{stage_id}-worker-check-{attempt}")
        return verification.run(command, context, timeout=timeout)

    def fail(self, workspace: dict) -> dict:
        root = workspace["root_path"]
        removed = self.service.force_remove(repo_path=self.repo_path, root_path=root)
        branch = str(removed.get("branch_name") or workspace.get("branch_name") or "")
        deleted = self.service.delete_branch(
            repo_path=self.repo_path, branch_name=branch
        ) if branch else {"deleted": False}
        return {
            **workspace,
            "status": "failed_cleaned",
            "removed": removed["removed"],
            "branch_deleted": deleted["deleted"],
        }

    def recover_complete(self, workspace: dict, *, stage_id: str, attempt: int) -> dict:
        """Finish or reconstruct a success interrupted during commit/archive."""
        root = Path(workspace["root_path"])
        registered = self.service._registered(self.repo_path, root)
        if registered is not None:
            return self.complete(workspace, stage_id=stage_id, attempt=attempt)
        branch = str(workspace.get("branch_name") or "")
        if branch and self.service._branch_exists(self.repo_path, branch):
            commit_sha = self.service._git(
                self.repo_path, "rev-parse", f"{branch}^{{commit}}"
            ).stdout.strip()
            return {
                **workspace,
                "status": "completed_recovered",
                "commit_sha": commit_sha,
                "archived": True,
                "branch_preserved": True,
            }
        raise RuntimeError("finalizing worktree and branch are both missing")

    def reconcile(self, known_roots: set[str]) -> list[dict]:
        """Remove only unrecorded registered worktrees owned by this pipeline."""
        recovered: list[dict] = []
        prefix = f"wf-{self.pipeline_id}-"
        known = {str(Path(root).resolve()) for root in known_roots}
        for item in self.service._worktrees(self.repo_path):
            root = Path(str(item.get("root_path") or "")).resolve()
            if (
                root.parent != self.service.storage_root
                or not root.name.startswith(prefix)
                or str(root) in known
            ):
                continue
            recovered.append(self.fail(item))
        return recovered

    def integrate(
        self,
        records: list[dict],
        *,
        verification_command: str,
        verification_timeout: float = 120,
    ) -> dict:
        """Merge proven workflow branches in order and verify exactly once."""
        if not records:
            raise ValueError("at least one completed write record is required")
        ordered: list[str] = []
        for record in records:
            branch = str(record.get("branch_name") or "")
            commit = str(record.get("commit_sha") or "")
            if record.get("status") not in {"completed", "completed_recovered"}:
                raise ValueError(f"write record is not completed: {record.get('status')!r}")
            if not branch.startswith("janus/") or not commit:
                raise ValueError("write record lacks a Janus branch or commit")
            actual = self.service._git(
                self.repo_path, "rev-parse", f"{branch}^{{commit}}"
            ).stdout.strip()
            if actual != commit:
                raise ValueError(f"write record commit does not match branch {branch!r}")
            if branch in ordered:
                raise ValueError(f"duplicate write branch: {branch}")
            ordered.append(branch)

        workspace = self.provision("integration", 1)
        root = Path(workspace["root_path"])
        merged: list[str] = []
        for branch in ordered:
            completed = self.service._git(
                root, "merge", "--no-ff", "--no-edit", branch, check=False
            )
            if completed.returncode != 0:
                unmerged = self.service.inspect(self.repo_path, root)["unmerged"]
                conflict_paths = sorted({
                    line.split(" ", 10)[-1]
                    for line in unmerged
                    if line.startswith("u ") and len(line.split(" ", 10)) == 11
                })
                return {
                    **workspace,
                    "status": "merge_conflict",
                    "merged_branches": merged,
                    "failed_branch": branch,
                    "stdout": completed.stdout,
                    "stderr": completed.stderr,
                    "unmerged": unmerged,
                    "conflict_paths": conflict_paths,
                    "ordered_branches": ordered,
                    "failed_index": len(merged),
                }
            merged.append(branch)

        context = WorkspaceContext(
            root=root,
            task_id=f"workflow-{self.pipeline_id}",
            workspace_id=self.workspace_id("integration", 1),
        ).for_dispatch("integration-verify")
        verified = verification.run(
            verification_command,
            context,
            timeout=verification_timeout,
        )
        if verified.get("exit_code") != 0:
            return {
                **workspace,
                "status": "verification_failed",
                "merged_branches": merged,
                "verification": verified,
            }
        head = self.service.current_head(repo_path=self.repo_path, root_path=root)
        archived = self.service.archive(repo_path=self.repo_path, root_path=root)
        return {
            **workspace,
            "status": "verified",
            "merged_branches": merged,
            "integration_commit": head["commit_sha"],
            "verification": verified,
            "archived": archived["removed"],
            "branch_preserved": archived["branch_preserved"],
            "source_branches_preserved": True,
        }

    def working_fingerprints(self, root_path: str | Path) -> dict[str, str]:
        root = Path(root_path).resolve()
        changed = self.service._git(
            root, "diff", "HEAD", "--name-only", "-z"
        ).stdout.split("\0")
        untracked = self.service._git(
            root, "ls-files", "--others", "--exclude-standard", "-z"
        ).stdout.split("\0")
        paths = sorted(set(item for item in [*changed, *untracked] if item))
        result: dict[str, str] = {}
        for path in paths:
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root):
                raise OwnershipViolation(f"fixer path escaped integration root: {path}")
            if candidate.is_file():
                result[path] = hashlib.sha256(candidate.read_bytes()).hexdigest()
            else:
                result[path] = "<missing>"
        return result

    @staticmethod
    def validate_fixer_changes(
        before: dict[str, str], after: dict[str, str], allowed: tuple[str, ...]
    ) -> list[str]:
        changed = sorted(
            path for path in set(before) | set(after)
            if before.get(path) != after.get(path)
        )
        violations = [path for path in changed if not owns_path(allowed, path)]
        if violations:
            raise OwnershipViolation(
                f"merge fixer changed files outside conflicts: {violations}"
            )
        return changed

    def continue_after_fix(
        self,
        integration: dict,
        *,
        verification_command: str,
        verification_timeout: float = 120,
    ) -> dict:
        root = Path(integration["root_path"])
        conflicts = tuple(str(path) for path in integration.get("conflict_paths") or ())
        if not conflicts:
            raise RuntimeError("merge conflict record has no conflict paths")
        self.service._git(root, "add", "--", *conflicts)
        if self.service.inspect(self.repo_path, root)["unmerged"]:
            raise RuntimeError("merge fixer left unmerged entries")
        self.service._git(root, "commit", "--no-edit")

        ordered = list(integration["ordered_branches"])
        failed_index = int(integration["failed_index"])
        merged = list(integration["merged_branches"])
        merged.append(ordered[failed_index])
        for index, branch in enumerate(ordered[failed_index + 1:], failed_index + 1):
            completed = self.service._git(
                root, "merge", "--no-ff", "--no-edit", branch, check=False
            )
            if completed.returncode != 0:
                unmerged = self.service.inspect(self.repo_path, root)["unmerged"]
                return {
                    **integration,
                    "status": "merge_conflict_again",
                    "merged_branches": merged,
                    "failed_branch": branch,
                    "failed_index": index,
                    "unmerged": unmerged,
                }
            merged.append(branch)

        context = WorkspaceContext(
            root=root,
            task_id=f"workflow-{self.pipeline_id}",
            workspace_id=self.workspace_id("integration", 1),
        ).for_dispatch("integration-verify")
        verified = verification.run(
            verification_command,
            context,
            timeout=verification_timeout,
        )
        if verified.get("exit_code") != 0:
            return {
                **integration,
                "status": "verification_failed",
                "merged_branches": merged,
                "verification": verified,
            }
        head = self.service.current_head(repo_path=self.repo_path, root_path=root)
        archived = self.service.archive(repo_path=self.repo_path, root_path=root)
        return {
            **integration,
            "status": "verified",
            "merged_branches": merged,
            "integration_commit": head["commit_sha"],
            "verification": verified,
            "archived": archived["removed"],
            "branch_preserved": archived["branch_preserved"],
            "source_branches_preserved": True,
        }
