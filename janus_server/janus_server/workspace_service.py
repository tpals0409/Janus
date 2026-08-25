"""Git worktree lifecycle for Task-owned ADE workspaces."""

from __future__ import annotations

import re
import subprocess
import hashlib
import json
import unicodedata
from datetime import datetime, timezone
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

    def _workspace_root(self, repo: Path, root_path: str | Path) -> Path:
        """Allow the project checkout for normal work; retain ownership checks elsewhere."""
        root = Path(root_path).expanduser().resolve()
        return root if root == repo.resolve() else self._owned_root(root)

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
        resolved = root.resolve()
        normalized = unicodedata.normalize("NFC", str(resolved))
        for item in self._worktrees(repo):
            candidate = Path(str(item.get("root_path") or "")).resolve()
            try:
                if candidate.exists() and resolved.exists() and candidate.samefile(resolved):
                    return item
            except OSError:
                pass
            if unicodedata.normalize("NFC", str(candidate)) == normalized:
                return item
        return None

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
        root = self._workspace_root(repo, root_path)
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

    @staticmethod
    def _parse_name_status(value: str) -> list[tuple[str, str | None, str]]:
        """Parse ``git diff --name-status -z`` without losing odd filenames."""
        fields = value.split("\0")
        if fields and fields[-1] == "":
            fields.pop()
        changes: list[tuple[str, str | None, str]] = []
        index = 0
        while index < len(fields):
            status = fields[index]
            index += 1
            if not status or index >= len(fields):
                raise WorkspaceServiceError("Git name-status 출력을 파싱할 수 없습니다")
            if status[0] in {"R", "C"}:
                if index + 1 >= len(fields):
                    raise WorkspaceServiceError("Git rename/copy 출력이 불완전합니다")
                old_path, path = fields[index], fields[index + 1]
                index += 2
            else:
                old_path, path = None, fields[index]
                index += 1
            changes.append((status, old_path, path))
        return changes

    def _diff_entries(
        self, root: Path, *, layer: str, diff_args: tuple[str, ...],
        max_diff_bytes: int,
    ) -> list[dict]:
        names = self._git(
            root, "diff", "--name-status", "-z", "-M", *diff_args
        ).stdout
        entries: list[dict] = []
        for status, old_path, path in self._parse_name_status(names):
            pathspecs = tuple(item for item in (old_path, path) if item is not None)
            raw_diff = self._git(
                root, "diff", "--no-ext-diff", "--no-color", "--unified=3",
                "-M", *diff_args, "--", *pathspecs,
            ).stdout
            diff_bytes = raw_diff.encode("utf-8", errors="replace")
            binary = "Binary files " in raw_diff or "GIT binary patch" in raw_diff
            large = len(diff_bytes) > max_diff_bytes
            rendered = diff_bytes[:max_diff_bytes].decode("utf-8", errors="replace")
            entries.append({
                "layer": layer,
                "status": status,
                "path": path,
                "old_path": old_path,
                "binary": binary,
                "large": large,
                "diff_bytes": len(diff_bytes),
                "diff": None if binary else rendered,
                "truncated": large,
            })
        return entries

    def changeset(
        self, *, repo_path: str | Path, root_path: str | Path, base_ref: str,
        max_diff_bytes: int = 512_000,
    ) -> dict:
        """Derive the complete Task change set directly from Git on every call."""
        if max_diff_bytes < 1:
            raise WorkspaceServiceError("max_diff_bytes는 1 이상이어야 합니다")
        repo = Path(self.validate_repo(repo_path, base_ref)["repo_path"])
        root = self._workspace_root(repo, root_path)
        registered = self._registered(repo, root)
        if registered is None:
            raise WorkspaceConflict(f"등록된 worktree가 아닙니다: {root}")

        base_commit = self._git(root, "rev-parse", f"{base_ref}^{{commit}}").stdout.strip()
        head_commit = self._git(root, "rev-parse", "HEAD^{commit}").stdout.strip()
        merge_base = self._git(root, "merge-base", base_commit, head_commit).stdout.strip()
        sections = {
            "committed": self._diff_entries(
                root, layer="committed", diff_args=(f"{base_commit}...{head_commit}",),
                max_diff_bytes=max_diff_bytes,
            ),
            "staged": self._diff_entries(
                root, layer="staged", diff_args=("--cached",),
                max_diff_bytes=max_diff_bytes,
            ),
            "unstaged": self._diff_entries(
                root, layer="unstaged", diff_args=(), max_diff_bytes=max_diff_bytes,
            ),
            "untracked": [],
        }

        untracked_output = self._git(
            root, "ls-files", "--others", "--exclude-standard", "-z"
        ).stdout
        for path in [item for item in untracked_output.split("\0") if item]:
            candidate = (root / path).resolve()
            if not candidate.is_relative_to(root) or not candidate.is_file():
                continue
            size = candidate.stat().st_size
            sample = candidate.read_bytes()[:max_diff_bytes + 1]
            binary = b"\0" in sample
            large = size > max_diff_bytes
            if binary:
                rendered = None
            else:
                text = sample[:max_diff_bytes].decode("utf-8", errors="replace")
                rendered = "".join(
                    [f"diff --git a/{path} b/{path}\n", "new file mode 100644\n",
                     "--- /dev/null\n", f"+++ b/{path}\n"]
                    + [f"+{line}" for line in text.splitlines(keepends=True)]
                )
            sections["untracked"].append({
                "layer": "untracked", "status": "?", "path": path,
                "old_path": None, "binary": binary, "large": large,
                "diff_bytes": size, "diff": rendered, "truncated": large,
            })

        status = self.inspect(repo, root)
        revision_payload = {
            "base_commit": base_commit, "head_commit": head_commit,
            "sections": sections, "unmerged": status["unmerged"],
        }
        revision = hashlib.sha256(
            json.dumps(
                revision_payload, sort_keys=True, ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()
        return {
            "source": "git",
            "derived_at": datetime.now(timezone.utc).isoformat(),
            "base_ref": base_ref,
            "base_commit": base_commit,
            "merge_base": merge_base,
            "head_commit": head_commit,
            "revision": revision,
            "branch_name": registered.get("branch_name"),
            "sections": sections,
            "counts": {name: len(items) for name, items in sections.items()},
            "dirty": status["dirty"],
            "unmerged": status["unmerged"],
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

    def discard_changes(self, *, repo_path: str | Path, root_path: str | Path) -> dict:
        """Discard only a registered Janus worktree; never erase an unmerged state."""
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        root = self._owned_root(root_path)
        status = self.inspect(repo, root)
        if status["unmerged"]:
            raise UnsafeWorkspace(
                "unmerged 변경은 자동 discard하지 않습니다. 충돌을 먼저 해결하세요"
            )
        self._git(root, "reset", "--hard", "HEAD")
        self._git(root, "clean", "-fd")
        refreshed = self.inspect(repo, root)
        return {
            "discarded": status["dirty"], "clean": not refreshed["dirty"],
            "branch_name": status.get("branch_name"),
        }

    def commit_changes(
        self, *, repo_path: str | Path, root_path: str | Path, message: str,
    ) -> dict:
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        root = self._workspace_root(repo, root_path)
        status = self.inspect(repo, root)
        branch = str(status.get("branch_name") or "")
        if not branch:
            raise UnsafeWorkspace("detached HEAD에서는 commit할 수 없습니다")
        if status["unmerged"]:
            raise UnsafeWorkspace("unmerged 변경은 commit할 수 없습니다")
        if not str(message).strip():
            raise WorkspaceConflict("commit message가 필요합니다")
        self._git(root, "add", "-A")
        staged = self._git(root, "diff", "--cached", "--quiet", check=False)
        if staged.returncode == 0:
            raise WorkspaceConflict("commit할 변경이 없습니다")
        if staged.returncode != 1:
            raise WorkspaceServiceError("staged diff를 확인할 수 없습니다")
        self._git(root, "commit", "-m", message.strip())
        commit_sha = self._git(root, "rev-parse", "HEAD^{commit}").stdout.strip()
        return {"commit_sha": commit_sha, "branch_name": branch, "message": message.strip()}

    def push_branch(
        self, *, repo_path: str | Path, root_path: str | Path, remote: str = "origin",
    ) -> dict:
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        root = self._workspace_root(repo, root_path)
        status = self.inspect(repo, root)
        branch = str(status.get("branch_name") or "")
        if not branch:
            raise UnsafeWorkspace("detached HEAD에서는 push할 수 없습니다")
        if status["dirty"]:
            raise UnsafeWorkspace("commit되지 않은 변경이 있어 push할 수 없습니다")
        remote_name = str(remote).strip()
        if not re.fullmatch(r"[A-Za-z0-9_.-]+", remote_name):
            raise WorkspaceConflict(f"안전하지 않은 remote 이름: {remote_name}")
        if self._git(root, "remote", "get-url", remote_name, check=False).returncode != 0:
            raise WorkspaceConflict(f"없는 Git remote: {remote_name}")
        self._git(root, "push", "-u", remote_name, branch)
        commit_sha = self._git(root, "rev-parse", "HEAD^{commit}").stdout.strip()
        return {
            "commit_sha": commit_sha, "branch_name": branch,
            "remote": remote_name, "pushed": True,
        }

    def current_head(self, *, repo_path: str | Path, root_path: str | Path) -> dict:
        repo = Path(self.validate_repo(repo_path, "HEAD")["repo_path"])
        root = self._workspace_root(repo, root_path)
        status = self.inspect(repo, root)
        return {
            "commit_sha": self._git(root, "rev-parse", "HEAD^{commit}").stdout.strip(),
            "branch_name": status.get("branch_name"), "dirty": status["dirty"],
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
