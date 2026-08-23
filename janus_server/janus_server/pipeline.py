"""Validated artifacts for the standard explore -> plan -> implement pipeline."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import json
import os
import re
import tempfile
from typing import Any, Iterable

from .ownership import InvalidPartition, normalize_partition, partitions_overlap
from .workflow import Stage


class InvalidPlanSpec(ValueError):
    pass


DEFAULT_ALLOWED_TOOLS = frozenset({"read", "search", "edit", "test"})
_TASK_ID = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_TASK_FIELDS = {
    "id",
    "purpose",
    "output_format",
    "allowed_tools",
    "boundaries",
    "owns",
    "check",
}
_REVIEW_FINDING_FIELDS = {"id", "path", "line", "severity", "message"}
_SEVERITIES = {"low", "medium", "high"}
MAX_REVIEW_DIFF_CHARS = 200_000


def _strings(value: Any, field: str, *, allow_empty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or (not value and not allow_empty):
        raise InvalidPlanSpec(f"{field} must be a non-empty list")
    items = tuple(str(item).strip() for item in value)
    if any(not item for item in items):
        raise InvalidPlanSpec(f"{field} contains an empty value")
    if len(set(items)) != len(items):
        raise InvalidPlanSpec(f"{field} contains duplicates")
    return items


@dataclass(frozen=True)
class TaskSpec:
    id: str
    purpose: str
    output_format: str
    allowed_tools: tuple[str, ...]
    boundaries: tuple[str, ...]
    owns: tuple[str, ...]
    check: str

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        permitted_tools: Iterable[str] = DEFAULT_ALLOWED_TOOLS,
    ) -> "TaskSpec":
        if not isinstance(value, dict) or set(value) != _TASK_FIELDS:
            actual = sorted(value) if isinstance(value, dict) else type(value).__name__
            raise InvalidPlanSpec(f"task fields must be exactly {sorted(_TASK_FIELDS)}; got {actual}")
        task_id = str(value["id"]).strip()
        if not _TASK_ID.fullmatch(task_id):
            raise InvalidPlanSpec(f"invalid task id: {task_id!r}")
        purpose = str(value["purpose"]).strip()
        output_format = str(value["output_format"]).strip()
        if not purpose or not output_format:
            raise InvalidPlanSpec(f"task {task_id!r} requires purpose and output_format")
        tools = _strings(value["allowed_tools"], f"task {task_id!r} allowed_tools")
        unknown = sorted(set(tools) - set(permitted_tools))
        if unknown:
            raise InvalidPlanSpec(f"task {task_id!r} uses unpermitted tools: {unknown}")
        boundaries = _strings(value["boundaries"], f"task {task_id!r} boundaries")
        check = str(value["check"]).strip()
        if not check:
            raise InvalidPlanSpec(f"task {task_id!r} requires a worker check")
        try:
            owns = tuple(
                normalize_partition(item)
                for item in _strings(value["owns"], f"task {task_id!r} owns")
            )
        except InvalidPartition as exc:
            raise InvalidPlanSpec(f"task {task_id!r} has unsafe ownership: {exc}") from exc
        return cls(task_id, purpose, output_format, tools, boundaries, owns, check)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "purpose": self.purpose,
            "output_format": self.output_format,
            "allowed_tools": list(self.allowed_tools),
            "boundaries": list(self.boundaries),
            "owns": list(self.owns),
            "check": self.check,
        }


@dataclass(frozen=True)
class PlanSpec:
    tasks: tuple[TaskSpec, ...]

    @classmethod
    def from_dict(
        cls,
        value: Any,
        *,
        permitted_tools: Iterable[str] = DEFAULT_ALLOWED_TOOLS,
    ) -> "PlanSpec":
        if not isinstance(value, dict) or set(value) != {"tasks"}:
            raise InvalidPlanSpec("plan output must contain exactly one tasks field")
        raw_tasks = value["tasks"]
        if not isinstance(raw_tasks, list) or not raw_tasks:
            raise InvalidPlanSpec("plan tasks must be a non-empty list")
        permitted = frozenset(permitted_tools)
        tasks = tuple(
            TaskSpec.from_dict(item, permitted_tools=permitted)
            for item in raw_tasks
        )
        ids = [task.id for task in tasks]
        if len(set(ids)) != len(ids):
            raise InvalidPlanSpec("plan task ids must be unique")
        for index, left in enumerate(tasks):
            for right in tasks[index + 1 :]:
                for left_path in left.owns:
                    for right_path in right.owns:
                        if partitions_overlap(left_path, right_path):
                            raise InvalidPlanSpec(
                                f"ownership overlap: {left.id}:{left_path} and "
                                f"{right.id}:{right_path}"
                            )
        return cls(tasks)

    def to_dict(self) -> dict[str, Any]:
        return {"tasks": [task.to_dict() for task in self.tasks]}

    def implement_stages(self, *, needs: tuple[str, ...] = ("plan",)) -> tuple[Stage, ...]:
        return tuple(
            Stage(
                task.id,
                needs=needs,
                write="worktree",
                owns=task.owns,
                role="coder",
                check=task.check,
            )
            for task in self.tasks
        )

    def save(self, path: Path) -> None:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        except BaseException:
            try:
                os.unlink(temporary)
            except FileNotFoundError:
                pass
            raise


@dataclass(frozen=True)
class ReviewPacket:
    """The complete and exclusive context visible to a clean reviewer."""

    plan: dict[str, Any]
    diff: str

    @classmethod
    def from_dict(cls, value: Any) -> "ReviewPacket":
        if not isinstance(value, dict) or set(value) != {"plan", "diff"}:
            raise InvalidPlanSpec("review packet must contain exactly plan and diff")
        plan = PlanSpec.from_dict(value["plan"])
        diff = str(value["diff"])
        if not diff.strip():
            raise InvalidPlanSpec("review diff must not be empty")
        if len(diff) > MAX_REVIEW_DIFF_CHARS:
            raise InvalidPlanSpec("review diff exceeds context limit")
        return cls(plan=plan.to_dict(), diff=diff)

    def to_dict(self) -> dict[str, Any]:
        return {"plan": self.plan, "diff": self.diff}

    def save(self, path: Path) -> None:
        _atomic_json(path, self.to_dict())


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    path: str
    line: int
    severity: str
    message: str

    @classmethod
    def from_dict(cls, value: Any) -> "ReviewFinding":
        if not isinstance(value, dict) or set(value) != _REVIEW_FINDING_FIELDS:
            raise InvalidPlanSpec(
                f"review finding fields must be exactly {sorted(_REVIEW_FINDING_FIELDS)}"
            )
        finding_id = str(value["id"]).strip()
        message = str(value["message"]).strip()
        severity = str(value["severity"]).strip()
        if not _TASK_ID.fullmatch(finding_id) or not message:
            raise InvalidPlanSpec("review finding requires a valid id and message")
        if severity not in _SEVERITIES:
            raise InvalidPlanSpec(f"invalid review severity: {severity!r}")
        try:
            path = normalize_partition(value["path"])
        except InvalidPartition as exc:
            raise InvalidPlanSpec(f"unsafe review finding path: {exc}") from exc
        line = value["line"]
        if not isinstance(line, int) or isinstance(line, bool) or line < 1:
            raise InvalidPlanSpec("review finding line must be a positive integer")
        return cls(finding_id, path, line, severity, message)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "path": self.path,
            "line": self.line,
            "severity": self.severity,
            "message": self.message,
        }


@dataclass(frozen=True)
class ReviewFeedback:
    verdict: str
    findings: tuple[ReviewFinding, ...]

    @classmethod
    def from_dict(cls, value: Any) -> "ReviewFeedback":
        if not isinstance(value, dict) or set(value) != {"verdict", "findings"}:
            raise InvalidPlanSpec("review output must contain exactly verdict and findings")
        verdict = str(value["verdict"]).strip()
        if verdict not in {"approved", "changes_requested"}:
            raise InvalidPlanSpec(f"invalid review verdict: {verdict!r}")
        raw = value["findings"]
        if not isinstance(raw, list):
            raise InvalidPlanSpec("review findings must be a list")
        findings = tuple(ReviewFinding.from_dict(item) for item in raw)
        if verdict == "approved" and findings:
            raise InvalidPlanSpec("approved review cannot contain findings")
        if verdict == "changes_requested" and not findings:
            raise InvalidPlanSpec("changes_requested review requires findings")
        ids = [finding.id for finding in findings]
        if len(set(ids)) != len(ids):
            raise InvalidPlanSpec("review finding ids must be unique")
        return cls(verdict, findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "findings": [finding.to_dict() for finding in self.findings],
        }


class ReviewLoop:
    """Engine-owned bounded review policy; models cannot extend the limit."""

    def __init__(self, artifact_dir: Path, *, max_rounds: int = 2):
        if max_rounds != 2:
            raise ValueError("standard review loop requires exactly two rounds")
        self.artifact_dir = Path(artifact_dir)
        self.max_rounds = max_rounds
        self.round = 0
        self.status = "reviewing"

    def record(self, value: Any) -> dict[str, Any]:
        if self.status != "reviewing":
            raise RuntimeError(f"review loop already ended as {self.status}")
        feedback = ReviewFeedback.from_dict(value)
        self.round += 1
        path = self.artifact_dir / f"review-feedback-{self.round}.json"
        _atomic_json(path, feedback.to_dict())
        if feedback.verdict == "approved":
            self.status = "approved"
        elif self.round >= self.max_rounds:
            self.status = "needs_human"
        else:
            self.status = "revise"
        result = {"round": self.round, "status": self.status, "path": path.name}
        if self.status == "revise":
            self.status = "reviewing"
        return result


def _atomic_json(path: Path, value: Any) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise
