"""Engine-owned file partitions for isolated write workers."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from pathlib import PurePosixPath


class InvalidPartition(ValueError):
    pass


class OwnershipConflict(RuntimeError):
    pass


class OwnershipViolation(RuntimeError):
    pass


def normalize_partition(value: str) -> str:
    raw = str(value).replace("\\", "/").strip()
    directory = raw.endswith("/")
    path = PurePosixPath(raw.rstrip("/"))
    if not raw or path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise InvalidPartition(f"unsafe ownership path: {value!r}")
    normalized = path.as_posix()
    return normalized + "/" if directory else normalized


def owns_path(partitions: Iterable[str], path: str) -> bool:
    target = normalize_partition(path)
    if target.endswith("/"):
        target = target.rstrip("/")
    for partition in partitions:
        owned = normalize_partition(partition)
        if owned.endswith("/"):
            prefix = owned.rstrip("/")
            if target == prefix or target.startswith(prefix + "/"):
                return True
        elif target == owned:
            return True
    return False


def partitions_overlap(left: str, right: str) -> bool:
    a = normalize_partition(left)
    b = normalize_partition(right)
    if a == b:
        return True
    if a.endswith("/"):
        prefix = a.rstrip("/")
        if b.rstrip("/") == prefix or b.startswith(prefix + "/"):
            return True
    if b.endswith("/"):
        prefix = b.rstrip("/")
        if a.rstrip("/") == prefix or a.startswith(prefix + "/"):
            return True
    return False


class OwnershipLease:
    def __init__(self, table: FileOwnershipTable, owner: str, partitions: tuple[str, ...]):
        self._table = table
        self.owner = owner
        self.partitions = partitions
        self._released = False

    def release(self) -> None:
        if not self._released:
            self._table._release(self.owner, self.partitions)
            self._released = True

    def __enter__(self) -> OwnershipLease:
        return self

    def __exit__(self, *_exc) -> None:
        self.release()


class FileOwnershipTable:
    """Thread-safe exclusive leases for exact paths and directory prefixes."""

    def __init__(self):
        self._lock = threading.RLock()
        self._held: dict[str, set[str]] = {}

    def acquire(self, owner: str, partitions: Iterable[str]) -> OwnershipLease:
        normalized = tuple(dict.fromkeys(normalize_partition(item) for item in partitions))
        if not normalized:
            raise InvalidPartition("write worker requires at least one owned path")
        with self._lock:
            for held_owner, held in self._held.items():
                if held_owner == owner:
                    continue
                for requested in normalized:
                    if any(partitions_overlap(requested, current) for current in held):
                        raise OwnershipConflict(
                            f"{owner!r} partition {requested!r} overlaps owner {held_owner!r}"
                        )
            self._held.setdefault(str(owner), set()).update(normalized)
        return OwnershipLease(self, str(owner), normalized)

    def _release(self, owner: str, partitions: tuple[str, ...]) -> None:
        with self._lock:
            held = self._held.get(owner)
            if held is None:
                return
            held.difference_update(partitions)
            if not held:
                self._held.pop(owner, None)

    def snapshot(self) -> dict[str, list[str]]:
        with self._lock:
            return {owner: sorted(paths) for owner, paths in sorted(self._held.items())}
