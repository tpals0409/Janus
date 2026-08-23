"""File ownership partition and contention tests."""

from __future__ import annotations

import threading
import time

import pytest

from janus_server.ownership import (
    FileOwnershipTable,
    InvalidPartition,
    OwnershipConflict,
    owns_path,
)


def test_exact_and_directory_partitions_match_only_owned_paths():
    partitions = ("README.md", "src/components/")
    assert owns_path(partitions, "README.md")
    assert owns_path(partitions, "src/components/Button.tsx")
    assert not owns_path(partitions, "README.md.bak")
    assert not owns_path(partitions, "src/component.ts")
    assert not owns_path(partitions, "src/other/Button.tsx")


@pytest.mark.parametrize("path", ["", "/tmp/file", "../secret", "src/../secret"])
def test_unsafe_partitions_are_rejected(path):
    table = FileOwnershipTable()
    with pytest.raises(InvalidPartition):
        table.acquire("worker", [path])


def test_overlapping_exact_and_directory_leases_cannot_coexist():
    table = FileOwnershipTable()
    first = table.acquire("worker-a", ["src/"])
    with pytest.raises(OwnershipConflict):
        table.acquire("worker-b", ["src/shared.py"])
    with pytest.raises(OwnershipConflict):
        table.acquire("worker-c", ["src/shared.py", "docs/"])
    assert table.snapshot() == {"worker-a": ["src/"]}

    first.release()
    second = table.acquire("worker-b", ["src/shared.py"])
    assert table.snapshot() == {"worker-b": ["src/shared.py"]}
    second.release()
    assert table.snapshot() == {}


def test_simultaneous_same_file_contention_allows_exactly_one_owner():
    table = FileOwnershipTable()
    start = threading.Barrier(3)
    results: list[tuple[str, str]] = []

    def contend(owner: str) -> None:
        start.wait()
        try:
            with table.acquire(owner, ["shared.txt"]):
                results.append((owner, "acquired"))
                time.sleep(0.1)
        except OwnershipConflict:
            results.append((owner, "conflict"))

    workers = [threading.Thread(target=contend, args=(f"worker-{index}",)) for index in (1, 2)]
    for worker in workers:
        worker.start()
    start.wait()
    for worker in workers:
        worker.join(2)

    assert sorted(status for _owner, status in results) == ["acquired", "conflict"]
    assert table.snapshot() == {}
