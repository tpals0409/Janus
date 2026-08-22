"""Git worktree lifecycle for Task-owned ADE workspaces."""

from __future__ import annotations

import re
import subprocess
from collections.abc import Callable
from pathlib import Path


class WorkspaceServiceError(RuntimeError):
    pass


class InvalidRepository(WorkspaceServiceError):
    pass


class WorkspaceConflict(WorkspaceServiceError):
    pass


class UnsafeWorkspace(WorkspaceServiceError):
    pass


ProgressCallback = Callable[[str, dict], None]


def _slug(value: str, *, fallback: str, limit: int = 36) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")
    return (slug or fallback)[:limit].rstrip("-")


class WorkspaceService:
    """Create and remove only worktrees owned below ``storage_root``.

    Safe archive always preserves the Git branch. Force-removing a dirty
    worktree and deleting its branch are deliberately separate methods.
    """

    def __init__(self, storage_root: str | Path):
        self.storage_root = Path(storage_root).expanduser().resolve()
        self.storage_root.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _git(
        cwd: str | Path, *args: str, check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run(
            ["git", "-C", str(cwd), *args],
            capture_output=True,
            text=True,
            timeout=120,
        )
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise WorkspaceServiceError(
                f"git {' '.join(args)} 실패(exit {completed.returncode}): {detail}"
            )
        return completed

    def validate_repo(self, repo_path: str | Path, base_ref: str) -> dict:
        candidate = Path(repo_path).expanduser().resolve()
        if not candidate.is_dir():
            raise InvalidRepository(f"repo 디렉토리가 없습니다: {candidate}")
        top = self._git(candidate, "rev-parse", "--show-toplevel", check=False)
        if top.returncode != 0:
            raise InvalidRepository(f"Git repo가 아닙니다: {candidate}")
        repo = Path(top.stdout.strip()).resolve()
        bare = self._git(repo, "rev-parse", "--is-bare-repository").stdout.strip()
        if bare == "true":
            raise InvalidRepository(f"bare repo는 Task workspace 원본으로 쓸 수 없습니다: {repo}")
        ref = str(base_ref).strip()
        if not ref:
            raise InvalidRepository("base ref가 필요합니다")
        resolved = self._git(
            repo, "rev-parse", "--verify", f"{ref}^{{commit}}", check=False
        )
        if resolved.returncode != 0:
            raise InvalidRepository(f"없는 base ref: {ref}")
        return {"repo_path": str(repo), "base_ref": ref, "commit": resolved.stdout.strip()}

    def _owned_root(self, root_path: str | Path) -> Path:
        root = Path(root_path).expanduser().resolve()
        if root == self.storage_root or not root.is_relative_to(self.storage_root):
            raise UnsafeWorkspace(f"Janus 소유 저장 루트 밖의 경로입니다: {root}")
        return root

    def _target(self, workspace_id: str) -> Path:
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(workspace_id)):
            raise WorkspaceConflict(f"안전하지 않은 workspace id: {workspace_id}")
        return self._owned_root(self.storage_root / workspace_id)

    def _worktrees(self, repo: Path) -> list[dict]:
        records: list[dict] = []
        current: dict = {}
        for line in self._git(repo, "worktree", "list", "--porcelain").stdout.splitlines():
            if not line:
                if current:
                    records.append(current)
                    current = {}
                continue
            key, _, value = line.partition(" ")
            if key == "worktree":
                current["root_path"] = str(Path(value).resolve())
            elif key == "branch":
                current["branch_name"] = value.removeprefix("refs/heads/")
            elif key == "HEAD":
                current["commit"] = value
            elif key in {"bare", "detached", "prunable", "locked"}:
                current[key] = value or True
        if current:
            records.append(current)
        return records

    def _registered(self, repo: Path, root: Path) -> dict | None:
        resolved = str(root.resolve())
        return next(
            (item for item in self._worktrees(repo) if item.get("root_path") == resolved),
            None,
        )

    def _branch_exists(self, repo: Path, branch: str) -> bool:
        return self._git(
            repo, "show-ref", "--verify", "--quiet", f"refs/heads/{branch}", check=False
        ).returncode == 0

    def _allocate_branch(self, repo: Path, task_id: str, title: str) -> str:
        task_part = _slug(task_id, fallback="task", limit=16)
        title_part = _slug(title, fallback="work")
        base = f"janus/{task_part}-{title_part}"
        candidate = base
        suffix = 2
        while self._branch_exists(repo, candidate):
            candidate = f"{base}-{suffix}"
            suffix += 1
        return candidate

    def prepare(
        self, *, workspace_id: str, task_id: str, title: str,
        repo_path: str | Path, base_ref: str,
        existing_root: str | Path | None = None,
        existing_branch: str | None = None,
        progress: ProgressCallback | None = None,
    ) -> dict:
        report = progress or (lambda _stage, _details: None)
        report("validating", {})
        validated = self.validate_repo(repo_path, base_ref)
        repo = Path(validated["repo_path"])
        target = self._target(workspace_id)

        if existing_root:
            recorded_root = self._owned_root(existing_root)
            registered = self._registered(repo, recorded_root)
            if registered is not None:
                actual_branch = registered.get("branch_name")
                if existing_branch and actual_branch != existing_branch:
                    raise WorkspaceConflict(
                        f"recorded branch({existing_branch})와 worktree branch({actual_branch})가 다릅니다"
                    )
                report("recovered", {
                    "root_path": str(recorded_root), "branch_name": actual_branch,
                })
                return {
                    **validated, "root_path": str(recorded_root),
                    "branch_name": actual_branch, "recovered": True,
                }
            if recorded_root.exists():
                raise WorkspaceConflict(
                    f"등록되지 않은 기존 경로를 덮어쓸 수 없습니다: {recorded_root}"
                )

        branch = str(existing_branch or "")
        if branch:
            if not branch.startswith("janus/"):
                raise WorkspaceConflict(f"Janus 소유 branch가 아닙니다: {branch}")
            if not self._branch_exists(repo, branch):
                raise WorkspaceConflict(f"복구할 branch가 없습니다: {branch}")
            checked_out = next(
                (item for item in self._worktrees(repo) if item.get("branch_name") == branch),
                None,
            )
            if checked_out is not None:
                raise WorkspaceConflict(
                    f"branch가 다른 worktree에서 사용 중입니다: {checked_out['root_path']}"
                )
        else:
            branch = self._allocate_branch(repo, task_id, title)

        if target.exists():
            registered = self._registered(repo, target)
            if registered is not None and registered.get("branch_name") == branch:
                report("recovered", {"root_path": str(target), "branch_name": branch})
                return {
                    **validated, "root_path": str(target), "branch_name": branch,
                    "recovered": True,
                }
            raise WorkspaceConflict(f"새 worktree로 덮어쓸 수 없는 기존 경로: {target}")

        report("allocating", {"root_path": str(target), "branch_name": branch})
        if existing_branch:
            args = ("worktree", "add", str(target), branch)
        else:
            args = ("worktree", "add", "-b", branch, str(target), validated["commit"])
        report("creating", {"root_path": str(target), "branch_name": branch})
        self._git(repo, *args)
        report("ready", {"root_path": str(target), "branch_name": branch})
        return {
            **validated, "root_path": str(target), "branch_name": branch,
            "recovered": bool(existing_branch),
        }

    def inspect(self, repo_path: str | Path, root_path: str | Path) -> dict:
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        root = self._owned_root(root_path)
        registered = self._registered(repo, root)
        if registered is None:
            raise WorkspaceConflict(f"등록된 worktree가 아닙니다: {root}")
        lines = self._git(
            root, "status", "--porcelain=v2", "--untracked-files=all"
        ).stdout.splitlines()
        unmerged = [line for line in lines if line.startswith("u ")]
        untracked = [line for line in lines if line.startswith("? ")]
        tracked = [line for line in lines if line.startswith(("1 ", "2 "))]
        return {
            **registered,
            "dirty": bool(lines),
            "tracked_changes": tracked,
            "untracked": untracked,
            "unmerged": unmerged,
            "porcelain": lines,
        }

    def archive(self, *, repo_path: str | Path, root_path: str | Path) -> dict:
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        root = self._owned_root(root_path)
        if not root.exists() and self._registered(repo, root) is None:
            return {"removed": False, "branch_preserved": True}
        status = self.inspect(repo, root)
        if status["dirty"]:
            kinds = []
            if status["tracked_changes"]:
                kinds.append("tracked")
            if status["untracked"]:
                kinds.append("untracked")
            if status["unmerged"]:
                kinds.append("unmerged")
            raise UnsafeWorkspace(
                f"변경이 있어 safe archive를 거부합니다: {', '.join(kinds)}"
            )
        self._git(repo, "worktree", "remove", str(root))
        self._git(repo, "worktree", "prune")
        return {
            "removed": True,
            "branch_name": status.get("branch_name"),
            "branch_preserved": self._branch_exists(repo, str(status.get("branch_name"))),
        }

    def force_remove(self, *, repo_path: str | Path, root_path: str | Path) -> dict:
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        root = self._owned_root(root_path)
        registered = self._registered(repo, root)
        if registered is None:
            if root.exists():
                raise UnsafeWorkspace(f"등록되지 않은 경로는 강제 삭제하지 않습니다: {root}")
            return {"removed": False, "branch_preserved": True}
        self._git(repo, "worktree", "remove", "--force", str(root))
        self._git(repo, "worktree", "prune")
        branch = str(registered.get("branch_name") or "")
        return {
            "removed": True, "branch_name": branch,
            "branch_preserved": bool(branch and self._branch_exists(repo, branch)),
        }

    def delete_branch(self, *, repo_path: str | Path, branch_name: str) -> dict:
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        branch = str(branch_name)
        if not branch.startswith("janus/"):
            raise UnsafeWorkspace(f"Janus branch가 아닌 이름은 삭제하지 않습니다: {branch}")
        checked_out = next(
            (item for item in self._worktrees(repo) if item.get("branch_name") == branch),
            None,
        )
        if checked_out is not None:
            raise UnsafeWorkspace(f"사용 중인 branch는 삭제할 수 없습니다: {branch}")
        if not self._branch_exists(repo, branch):
            return {"deleted": False, "branch_name": branch}
        self._git(repo, "branch", "-D", branch)
        return {"deleted": True, "branch_name": branch}
