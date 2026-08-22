"""Task 실행에 귀속된 불변 워크스페이스 컨텍스트."""

from __future__ import annotations

from dataclasses import dataclass, replace
from pathlib import Path


@dataclass(frozen=True, slots=True)
class WorkspaceContext:
    """A tool invocation's filesystem jail and persistent ownership IDs.

    ``dispatch_id`` is absent while a session is idle. The runtime creates a
    new immutable copy for each turn so concurrent Tasks never share mutable
    workspace state.
    """

    root: Path
    task_id: str
    workspace_id: str
    dispatch_id: str | None = None

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser().resolve()
        if not root.is_dir():
            raise ValueError(f"워크스페이스 디렉토리가 아닙니다: {self.root}")
        object.__setattr__(self, "root", root)
        for field in ("task_id", "workspace_id"):
            value = getattr(self, field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{field}가 필요합니다")
        if self.dispatch_id is not None and not str(self.dispatch_id).strip():
            raise ValueError("dispatch_id는 빈 문자열일 수 없습니다")

    def for_dispatch(self, dispatch_id: str) -> "WorkspaceContext":
        """Return the same Task/Workspace ownership bound to one Dispatch."""
        if not str(dispatch_id).strip():
            raise ValueError("dispatch_id가 필요합니다")
        return replace(self, dispatch_id=str(dispatch_id))

    def identifiers(self) -> dict[str, str | None]:
        return {
            "task_id": self.task_id,
            "workspace_id": self.workspace_id,
            "dispatch_id": self.dispatch_id,
        }
