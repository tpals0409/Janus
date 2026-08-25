"""GitHub CLI adapter scoped to one Task worktree.

No command here changes branches or merges pull requests.  Janus creates and reads
PR state, then leaves workspace archival as an explicit user decision.
"""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

MAX_FAILED_LOG_CHARS = 40_000


class GitHubServiceError(RuntimeError):
    pass


class GitHubService:
    def __init__(self, *, timeout: float = 120):
        self.timeout = timeout

    def _run(self, root: str | Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        try:
            completed = subprocess.run(
                ["gh", *args], cwd=str(Path(root).resolve()), capture_output=True,
                text=True, timeout=self.timeout,
            )
        except FileNotFoundError as error:
            raise GitHubServiceError("GitHub CLI(gh)가 설치되어 있지 않습니다") from error
        except subprocess.TimeoutExpired as error:
            raise GitHubServiceError(f"gh {' '.join(args[:3])} timeout") from error
        if check and completed.returncode != 0:
            detail = completed.stderr.strip() or completed.stdout.strip()
            raise GitHubServiceError(
                f"gh {' '.join(args[:3])} 실패(exit {completed.returncode}): {detail[:2000]}"
            )
        return completed

    def _json(self, root: str | Path, *args: str) -> object:
        completed = self._run(root, *args)
        try:
            return json.loads(completed.stdout)
        except json.JSONDecodeError as error:
            raise GitHubServiceError("gh가 올바른 JSON을 반환하지 않았습니다") from error

    @staticmethod
    def _branch(value: str, label: str) -> str:
        branch = str(value).strip()
        if not branch or not re.fullmatch(r"[A-Za-z0-9._/-]+", branch) or branch.startswith("-"):
            raise GitHubServiceError(f"안전하지 않은 {label} branch: {branch!r}")
        return branch

    def create_pull_request(
        self, *, root_path: str | Path, head: str, base: str,
        title: str, body: str, draft: bool = False,
    ) -> dict:
        head = self._branch(head, "head")
        base = self._branch(base, "base")
        title = str(title).strip()
        if not title:
            raise GitHubServiceError("PR title이 필요합니다")
        args = [
            "pr", "create", "--head", head, "--base", base,
            "--title", title, "--body", str(body),
        ]
        if draft:
            args.append("--draft")
        self._run(root_path, *args)
        return self.pull_request(root_path=root_path, branch=head)

    def pull_request(self, *, root_path: str | Path, branch: str) -> dict:
        branch = self._branch(branch, "head")
        raw = self._json(
            root_path, "pr", "view", branch, "--json",
            "number,url,state,isDraft,mergedAt,closedAt,mergeStateStatus,reviewDecision,"
            "title,headRefName,baseRefName",
        )
        if not isinstance(raw, dict):
            raise GitHubServiceError("gh pr view 응답 형식이 올바르지 않습니다")
        state = "merged" if raw.get("mergedAt") else str(raw.get("state") or "open").lower()
        return {
            "number": int(raw["number"]), "url": str(raw["url"]), "state": state,
            "draft": bool(raw.get("isDraft")), "merged_at": raw.get("mergedAt"),
            "closed_at": raw.get("closedAt"),
            "merge_state": raw.get("mergeStateStatus"),
            "review_decision": raw.get("reviewDecision"), "title": str(raw["title"]),
            "head_branch": str(raw["headRefName"]), "base_branch": str(raw["baseRefName"]),
        }

    def checks(self, *, root_path: str | Path, branch: str) -> dict:
        branch = self._branch(branch, "head")
        raw_checks = self._json(
            root_path, "pr", "checks", branch, "--json",
            "bucket,completedAt,description,event,link,name,startedAt,state,workflow",
        )
        checks = raw_checks if isinstance(raw_checks, list) else []
        raw_runs = self._json(
            root_path, "run", "list", "--branch", branch, "--limit", "10", "--json",
            "databaseId,name,displayTitle,status,conclusion,url,createdAt,updatedAt",
        )
        runs = raw_runs if isinstance(raw_runs, list) else []
        failed_logs: list[dict] = []
        for run in runs:
            if str(run.get("conclusion") or "").lower() not in {
                "failure", "timed_out", "cancelled", "action_required", "startup_failure",
            }:
                continue
            run_id = int(run["databaseId"])
            output = self._run(
                root_path, "run", "view", str(run_id), "--log-failed", check=False
            )
            combined = (output.stdout + ("\n" + output.stderr if output.stderr else "")).strip()
            truncated = len(combined) > MAX_FAILED_LOG_CHARS
            failed_logs.append({
                "run_id": run_id, "name": run.get("name"), "url": run.get("url"),
                "conclusion": run.get("conclusion"),
                "log": combined[:MAX_FAILED_LOG_CHARS], "truncated": truncated,
            })
        return {"checks": checks, "runs": runs, "failed_logs": failed_logs}

    def refresh(self, *, root_path: str | Path, branch: str) -> dict:
        return {
            "pull_request": self.pull_request(root_path=root_path, branch=branch),
            **self.checks(root_path=root_path, branch=branch),
        }
