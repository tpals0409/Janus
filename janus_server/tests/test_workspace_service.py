"""P1 Git WorkspaceService lifecycle integration tests."""

from __future__ import annotations

import subprocess
import tempfile
import unicodedata
import unittest
from pathlib import Path

from janus_server.workspace_service import (
    InvalidRepository,
    UnsafeWorkspace,
    WorkspaceService,
)


def git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(cwd), *args], capture_output=True, text=True, check=check
    )


class WorkspaceServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.repo = root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-b", "main")
        git(self.repo, "config", "user.email", "janus@example.test")
        git(self.repo, "config", "user.name", "Janus Test")
        (self.repo / "conflict.txt").write_text("base\n", encoding="utf-8")
        git(self.repo, "add", "conflict.txt")
        git(self.repo, "commit", "-m", "initial")
        self.main_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()
        self.service = WorkspaceService(root / "owned-workspaces")

    def tearDown(self):
        self.temp.cleanup()

    def assert_main_unchanged(self):
        self.assertEqual("main", git(self.repo, "branch", "--show-current").stdout.strip())
        self.assertEqual(self.main_head, git(self.repo, "rev-parse", "HEAD").stdout.strip())
        self.assertEqual("", git(self.repo, "status", "--porcelain").stdout.strip())

    def test_validates_repo_and_base_ref(self):
        validated = self.service.validate_repo(self.repo, "main")
        self.assertEqual(str(self.repo.resolve()), validated["repo_path"])
        self.assertEqual(self.main_head, validated["commit"])

        with self.assertRaises(InvalidRepository):
            self.service.validate_repo(self.repo, "missing-ref")
        with self.assertRaises(InvalidRepository):
            self.service.validate_repo(Path(self.temp.name) / "missing", "main")

    def test_create_recover_archive_force_remove_and_branch_delete(self):
        progress: list[tuple[str, dict]] = []
        first = self.service.prepare(
            workspace_id="workspace_one", task_id="task_one", title="Same title",
            repo_path=self.repo, base_ref="main",
            progress=lambda stage, detail: progress.append((stage, detail)),
        )
        first_root = Path(first["root_path"])
        self.assertTrue(first_root.is_dir())
        self.assertEqual(
            ["validating", "allocating", "creating", "ready"],
            [stage for stage, _ in progress],
        )
        self.assertFalse(first["recovered"])
        self.assert_main_unchanged()

        recovered = self.service.prepare(
            workspace_id="workspace_one", task_id="task_one", title="Same title",
            repo_path=self.repo, base_ref="main",
            existing_root=first_root, existing_branch=first["branch_name"],
        )
        self.assertTrue(recovered["recovered"])
        self.assertEqual(first["branch_name"], recovered["branch_name"])

        (first_root / "untracked.txt").write_text("keep me", encoding="utf-8")
        status = self.service.inspect(self.repo, first_root)
        self.assertTrue(status["dirty"])
        self.assertTrue(status["untracked"])
        with self.assertRaises(UnsafeWorkspace):
            self.service.archive(repo_path=self.repo, root_path=first_root)
        (first_root / "untracked.txt").unlink()

        archived = self.service.archive(repo_path=self.repo, root_path=first_root)
        self.assertTrue(archived["removed"])
        self.assertTrue(archived["branch_preserved"])
        self.assertFalse(first_root.exists())

        # A recorded branch without a worktree is attached instead of being recreated.
        restored = self.service.prepare(
            workspace_id="workspace_restored", task_id="task_one", title="Same title",
            repo_path=self.repo, base_ref="main", existing_branch=first["branch_name"],
        )
        restored_root = Path(restored["root_path"])
        self.assertTrue(restored["recovered"])
        self.service.force_remove(repo_path=self.repo, root_path=restored_root)

        deleted = self.service.delete_branch(
            repo_path=self.repo, branch_name=first["branch_name"]
        )
        self.assertTrue(deleted["deleted"])
        self.assert_main_unchanged()

    def test_unique_identity_unmerged_detection_and_force_preserves_branch(self):
        one = self.service.prepare(
            workspace_id="workspace_a", task_id="same_task", title="Duplicate",
            repo_path=self.repo, base_ref="main",
        )
        # Same Task slug cannot reuse a branch; workspace ID also prevents path reuse.
        two = self.service.prepare(
            workspace_id="workspace_b", task_id="same_task", title="Duplicate",
            repo_path=self.repo, base_ref="main",
        )
        self.assertNotEqual(one["root_path"], two["root_path"])
        self.assertNotEqual(one["branch_name"], two["branch_name"])

        two_root = Path(two["root_path"])
        (two_root / "conflict.txt").write_text("task branch\n", encoding="utf-8")
        git(two_root, "add", "conflict.txt")
        git(two_root, "commit", "-m", "task change")

        other_root = Path(self.temp.name) / "other-worktree"
        git(self.repo, "worktree", "add", "-b", "conflict-other", str(other_root), "main")
        (other_root / "conflict.txt").write_text("other branch\n", encoding="utf-8")
        git(other_root, "add", "conflict.txt")
        git(other_root, "commit", "-m", "other change")
        git(self.repo, "worktree", "remove", str(other_root))
        merge = git(two_root, "merge", "conflict-other", check=False)
        self.assertNotEqual(0, merge.returncode)

        status = self.service.inspect(self.repo, two_root)
        self.assertTrue(status["dirty"])
        self.assertTrue(status["unmerged"])
        with self.assertRaises(UnsafeWorkspace):
            self.service.discard_changes(repo_path=self.repo, root_path=two_root)
        forced = self.service.force_remove(repo_path=self.repo, root_path=two_root)
        self.assertTrue(forced["removed"])
        self.assertTrue(forced["branch_preserved"])

        self.service.force_remove(repo_path=self.repo, root_path=one["root_path"])
        self.assert_main_unchanged()

    def test_never_removes_paths_outside_owned_storage(self):
        with self.assertRaises(UnsafeWorkspace):
            self.service.archive(repo_path=self.repo, root_path=self.repo)
        with self.assertRaises(UnsafeWorkspace):
            self.service.force_remove(repo_path=self.repo, root_path=self.repo)
        self.assert_main_unchanged()

    def test_registered_worktree_identity_handles_unicode_normalization(self):
        prepared = self.service.prepare(
            workspace_id="unicode_workspace",
            task_id="unicode_task",
            title="Unicode",
            repo_path=self.repo,
            base_ref="main",
        )
        root = Path(prepared["root_path"])
        alternate = Path(unicodedata.normalize("NFC", str(root)))

        recovered = self.service.prepare(
            workspace_id="unicode_workspace",
            task_id="unicode_task",
            title="Unicode",
            repo_path=self.repo,
            base_ref="main",
            existing_root=alternate,
            existing_branch=prepared["branch_name"],
        )

        self.assertTrue(recovered["recovered"])
        self.service.force_remove(repo_path=self.repo, root_path=alternate)

    def test_changeset_is_git_derived_across_all_layers(self):
        (self.repo / "stage.txt").write_text("base\n", encoding="utf-8")
        (self.repo / "delete.txt").write_text("delete me\n", encoding="utf-8")
        git(self.repo, "add", "stage.txt", "delete.txt")
        git(self.repo, "commit", "-m", "fixtures")
        self.main_head = git(self.repo, "rev-parse", "HEAD").stdout.strip()

        prepared = self.service.prepare(
            workspace_id="workspace_changes", task_id="task_changes", title="Changes",
            repo_path=self.repo, base_ref="main",
        )
        root = Path(prepared["root_path"])
        git(root, "mv", "conflict.txt", "renamed.txt")
        git(root, "commit", "-m", "rename", "--", "conflict.txt", "renamed.txt")
        (root / "stage.txt").write_text("staged\n", encoding="utf-8")
        git(root, "add", "stage.txt")
        (root / "stage.txt").write_text("staged\nunstaged\n", encoding="utf-8")
        (root / "delete.txt").unlink()
        (root / "binary.dat").write_bytes(b"\x00\x01\x02")
        (root / "large.txt").write_text("x" * 80, encoding="utf-8")

        changes = self.service.changeset(
            repo_path=self.repo, root_path=root, base_ref="main", max_diff_bytes=32,
        )
        self.assertEqual("git", changes["source"])
        self.assertEqual(prepared["branch_name"], changes["branch_name"])
        committed = changes["sections"]["committed"]
        self.assertEqual("R100", committed[0]["status"])
        self.assertEqual("conflict.txt", committed[0]["old_path"])
        self.assertEqual("renamed.txt", committed[0]["path"])
        self.assertEqual(["stage.txt"], [item["path"] for item in changes["sections"]["staged"]])
        self.assertEqual(
            {"delete.txt", "stage.txt"},
            {item["path"] for item in changes["sections"]["unstaged"]},
        )
        untracked = {item["path"]: item for item in changes["sections"]["untracked"]}
        self.assertTrue(untracked["binary.dat"]["binary"])
        self.assertIsNone(untracked["binary.dat"]["diff"])
        self.assertTrue(untracked["large.txt"]["large"])
        self.assertTrue(untracked["large.txt"]["truncated"])

        # No cache: a second call reflects the current Git state immediately.
        (root / "fresh.txt").write_text("fresh\n", encoding="utf-8")
        refreshed = self.service.changeset(
            repo_path=self.repo, root_path=root, base_ref="main"
        )
        self.assertIn(
            "fresh.txt", {item["path"] for item in refreshed["sections"]["untracked"]}
        )
        self.assert_main_unchanged()


if __name__ == "__main__":
    unittest.main()
