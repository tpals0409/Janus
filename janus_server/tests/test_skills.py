"""Janus Skill Compiler와 GitHub 수입기의 결정성·안전 경계."""

from __future__ import annotations

import io
import tempfile
import unittest
import warnings
import zipfile
from pathlib import Path

from janus_server import skills


def archive(files: dict[str, str]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as bundle:
        for path, content in files.items():
            bundle.writestr(path, content)
    return output.getvalue()


class SkillCompilerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_skill(self) -> Path:
        root = self.root / "review"
        (root / "references").mkdir(parents=True)
        (root / "SKILL.md").write_text(
            """---
name: code-review
description: Review changes in the current project
allowed-tools: Read, Grep, Bash(git diff:*)
context: fork
---
Review $ARGUMENTS under ${CLAUDE_PROJECT_DIR}.
!`git diff --check`
""",
            encoding="utf-8",
        )
        (root / "references" / "checklist.md").write_text("Check tests.\n", encoding="utf-8")
        (root / "assets").mkdir()
        (root / "assets" / "sample.bin").write_bytes(b"\x00\x01\x02")
        (root / "LICENSE.txt").write_text("MIT License\n", encoding="utf-8")
        return root

    def test_claude_skill_compiles_to_stable_janus_ir(self):
        root = self.make_skill()
        first = skills.compile_skill_directory(
            root, source_kind="claude", source_locator=str(root), namespace="claude",
        )
        second = skills.compile_skill_directory(
            root, source_kind="claude", source_locator=str(root), namespace="claude",
        )

        self.assertEqual(first["content_hash"], second["content_hash"])
        self.assertEqual("code-review", first["compiled"]["name"])
        self.assertEqual("worker", first["compiled"]["execution"]["context"])
        self.assertEqual(
            ["read_file", "grep", "run_bash"],
            first["compiled"]["capabilities"]["required"],
        )
        self.assertEqual(["run_bash"], first["compiled"]["capabilities"]["approval_required"])
        self.assertIn("{{input}}", first["compiled"]["instructions"])
        self.assertIn("{{workspace_root}}", first["compiled"]["instructions"])
        self.assertIn("Janus 승인 필요", first["compiled"]["instructions"])
        self.assertEqual("partial", first["compatibility"])
        self.assertEqual("LICENSE.txt", first["report"]["license_file"])
        binary = next(
            item for item in first["original"]["files"]
            if item["path"] == "assets/sample.bin"
        )
        self.assertTrue(binary["binary"])
        self.assertEqual("AAEC", binary["content_base64"])

    def test_symlinked_skill_resource_is_rejected(self):
        root = self.make_skill()
        outside = self.root / "outside.txt"
        outside.write_text("secret", encoding="utf-8")
        (root / "references" / "outside.md").symlink_to(outside)

        with self.assertRaisesRegex(skills.SkillImportError, "심볼릭 링크"):
            skills.compile_skill_directory(
                root, source_kind="local", source_locator=str(root),
            )

    def test_github_repository_is_pinned_and_discovers_skills(self):
        revision = "a" * 40
        bundle = archive({
            "repo-snapshot/skills/review/SKILL.md": "---\nname: review\ndescription: Review code\n---\nRead files.\n",
        })

        def fetch_json(url: str) -> dict:
            if "/commits/" in url:
                return {"sha": revision}
            return {"default_branch": "main", "license": {"spdx_id": "MIT"}}

        result = skills.download_github_skills(
            "https://github.com/acme/skills/tree/main/skills",
            self.root / "download",
            fetch_json=fetch_json,
            fetch_bytes=lambda _url: bundle,
        )

        self.assertEqual(revision, result["revision"])
        self.assertEqual("MIT", result["license"])
        self.assertEqual(["review"], [path.name for path in result["skill_directories"]])

    def test_github_skill_blob_url_selects_its_parent_directory(self):
        parsed = skills.parse_github_url(
            "https://github.com/acme/skills/blob/main/catalog/review/SKILL.md"
        )
        self.assertEqual("main", parsed["ref"])
        self.assertEqual("catalog/review", parsed["subpath"])

        with self.assertRaisesRegex(skills.SkillImportError, "SKILL.md"):
            skills.parse_github_url(
                "https://github.com/acme/skills/blob/main/catalog/review/README.md"
            )

    def test_github_archive_path_escape_is_rejected(self):
        revision = "b" * 40
        bundle = archive({"../escape/SKILL.md": "bad"})

        def fetch_json(url: str) -> dict:
            return {"sha": revision} if "/commits/" in url else {"default_branch": "main"}

        with self.assertRaisesRegex(skills.SkillImportError, "경로 탈출"):
            skills.download_github_skills(
                "https://github.com/acme/skills",
                self.root / "escape",
                fetch_json=fetch_json,
                fetch_bytes=lambda _url: bundle,
            )

    def test_github_archive_duplicate_path_is_rejected(self):
        revision = "e" * 40
        output = io.BytesIO()
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with zipfile.ZipFile(output, "w") as bundle:
                bundle.writestr("repo/skill/SKILL.md", "first")
                bundle.writestr("repo/skill/SKILL.md", "second")

        def fetch_json(url: str) -> dict:
            return {"sha": revision} if "/commits/" in url else {"default_branch": "main"}

        with self.assertRaisesRegex(skills.SkillImportError, "중복 경로"):
            skills.download_github_skills(
                "https://github.com/acme/skills",
                self.root / "duplicate",
                fetch_json=fetch_json,
                fetch_bytes=lambda _url: output.getvalue(),
            )

    def test_invalid_github_zip_is_reported_as_import_error(self):
        revision = "f" * 40

        def fetch_json(url: str) -> dict:
            return {"sha": revision} if "/commits/" in url else {"default_branch": "main"}

        with self.assertRaisesRegex(skills.SkillImportError, "올바른 ZIP"):
            skills.download_github_skills(
                "https://github.com/acme/skills",
                self.root / "invalid",
                fetch_json=fetch_json,
                fetch_bytes=lambda _url: b"not-a-zip",
            )

    def test_github_source_without_skill_is_rejected(self):
        revision = "1" * 40
        bundle = archive({"repo/README.md": "No skill here."})

        def fetch_json(url: str) -> dict:
            return {"sha": revision} if "/commits/" in url else {"default_branch": "main"}

        with self.assertRaisesRegex(skills.SkillImportError, "SKILL.md"):
            skills.download_github_skills(
                "https://github.com/acme/skills",
                self.root / "empty",
                fetch_json=fetch_json,
                fetch_bytes=lambda _url: bundle,
            )


if __name__ == "__main__":
    unittest.main()
