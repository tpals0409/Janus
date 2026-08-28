"""문서가 코드와 어긋나는 것을 CI에서 잡는다.

문서 드리프트는 조용히 쌓인다. 오픈소스 공개 전 실사에서 20건 넘는 모순이 나왔고,
그중엔 제품이 지키지 않는 안전 약속과 존재하지 않는 스크립트 실행 안내가 있었다.
값이 바뀌면 문서도 같이 바뀌도록, 사람이 아니라 테스트가 기억하게 한다.
"""

from __future__ import annotations

import os
import pathlib
import re
import unittest
from pathlib import Path

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

ROOT = Path(__file__).resolve().parents[2]
DOCS = ["README.md", "PRODUCT.md", "ROADMAP.md", "CHECKLIST.md", "STATUS.md",
        "V1_AUDIT.md", "VERSIONING.md", "DESIGN_SYSTEM.md",
        "ORCHESTRATION_CHECKLIST.md"]


def doc(name: str) -> str:
    return (ROOT / name).read_text(encoding="utf-8")


class DocumentedCommandsExist(unittest.TestCase):
    def test_every_documented_script_is_present(self):
        """실사에서 나온 결함 — 삭제된 스크립트 2개를 실행하라고 안내하고 있었다."""
        pattern = re.compile(r"(?:scripts/)([a-z0-9_]+\.py)")
        missing: list[str] = []
        for name in [*DOCS, "janus_server/scripts/README.md", "janus_server/RECOVERY.md"]:
            text = (ROOT / name).read_text(encoding="utf-8")
            for script in set(pattern.findall(text)):
                if not ((ROOT / "scripts" / script).exists()
                        or (ROOT / "janus_server" / "scripts" / script).exists()):
                    missing.append(f"{name} -> scripts/{script}")
        self.assertEqual([], missing, "문서가 존재하지 않는 스크립트를 안내한다")

    def test_relative_doc_links_resolve(self):
        broken: list[str] = []
        for name in [*DOCS, "janus_server/RECOVERY.md", "janus_server/scripts/README.md"]:
            path = ROOT / name
            for match in re.finditer(r"\[[^\]]+\]\(([^)#:]+\.md)\)", path.read_text(encoding="utf-8")):
                if not (path.parent / match.group(1)).resolve().exists():
                    broken.append(f"{name} -> {match.group(1)}")
        self.assertEqual([], broken)


class DocumentedFactsMatchCode(unittest.TestCase):
    def test_model_slot_default_is_not_described_as_one(self):
        """기본값이 5→3으로 바뀌었는데 문서 네 곳이 '1-slot'으로 남아 있었다."""
        from janus_server import scheduler

        self.assertEqual(3, scheduler.MODEL_GENERATION_SLOTS)
        for name in DOCS:
            text = doc(name)
            self.assertFalse(
                "기본 1-slot" in text, f"{name}이 model slot 기본값을 1로 적고 있다")
            self.assertFalse(
                "one-slot model" in text, f"{name}이 one-slot model이라고 적고 있다")

    def test_providers_in_docs_match_the_schema_constraint(self):
        """구독형 CLI가 출하됐는데 문서는 '로컬이 유일한 실행 경로'라고 했다."""
        from janus_server import domain

        self.assertIn("'claude_code'", domain.MIGRATION_26)
        self.assertIn("'codex'", domain.MIGRATION_26)
        for phrase in (
            "외부 모델과 외부 코딩 에이전트 지원은 현재 제품 목표가 아니다",
            "현재 유일한 실행 경로",
            "한 구성만 지원한다",
        ):
            self.assertFalse(phrase in doc("PRODUCT.md"), f"PRODUCT.md: {phrase}")

    def test_docs_do_not_promise_worktree_isolation(self):
        """v1.0.28에서 격리를 걷어냈다. 지키지 않을 약속을 문서가 하면 안 된다."""
        from janus_server.routers import workspaces

        source = Path(workspaces.__file__).read_text(encoding="utf-8")
        self.assertNotIn(
            "get_workspace_service().prepare(", source,
            "prepare()가 다시 배선됐다면 이 테스트와 문서를 함께 되돌려야 한다",
        )
        for phrase in (
            "main 체크아웃을 수정하지 않고",
            "Task별 worktree와 branch를 기본값으로 사용한다",
        ):
            for name in DOCS:
                self.assertFalse(phrase in doc(name), f"{name}: {phrase}")

    def test_default_local_model_matches_the_catalog(self):
        from janus_server import model_setup

        default = model_setup.MODEL_CATALOG[model_setup.DEFAULT_MODEL_ID]["repo"]
        self.assertEqual("mlx-community/Qwen3.8-27B-4bit", default)
        self.assertIn(default, doc("README.md"))

    def test_evaluation_lab_is_not_described_as_removed(self):
        """화면은 살아 있는데 CHECKLIST가 제거됐다고 적어 두었다."""
        app = (ROOT / "janus/src/renderer/src/App.tsx").read_text(encoding="utf-8")
        self.assertIn("EvaluationLab", app)
        self.assertFalse(
            "Evaluation Lab 화면은 제거됐고" in doc("CHECKLIST.md"),
            "CHECKLIST가 살아 있는 화면을 제거됐다고 적고 있다")

    def test_orchestration_checklist_is_marked_historical(self):
        """설명하는 모듈이 전부 삭제된 문서다. 현재 보증으로 읽히면 안 된다."""
        self.assertFalse((ROOT / "janus_server/janus_server/workflow.py").exists())
        self.assertFalse((ROOT / "janus_server/janus_server/airgap.py").exists())
        text = doc("ORCHESTRATION_CHECKLIST.md")
        self.assertIn("역사 기록", text.splitlines()[0])
        self.assertIn("제거됐다", text)


if __name__ == "__main__":
    unittest.main()


class OpenSourceRequirements(unittest.TestCase):
    """공개 레포가 갖춰야 하는 것. 없으면 법적으로 아무도 쓸 수 없거나
    취약점을 알릴 곳이 없다."""

    def test_license_exists_and_is_declared_everywhere(self):
        license_text = (ROOT / "LICENSE").read_text(encoding="utf-8")
        self.assertIn("Apache License", license_text)
        self.assertIn("Version 2.0", license_text)
        # 자리표시자를 그대로 두면 저작권 주장이 성립하지 않는다.
        self.assertNotIn("[name of copyright owner]", license_text)

        import json
        import tomllib

        package = json.loads((ROOT / "janus/package.json").read_text(encoding="utf-8"))
        self.assertEqual("Apache-2.0", package.get("license"))
        for manifest in ("janus_server/pyproject.toml", "qwen3.8mlx/pyproject.toml"):
            data = tomllib.loads((ROOT / manifest).read_text(encoding="utf-8"))
            self.assertEqual("Apache-2.0", data["project"].get("license"), manifest)

    def test_backend_dependencies_survive_manifest_edits(self):
        """pyproject를 손대다 [project.urls] 아래로 dependencies가 밀려
        잠금에서 uvicorn까지 사라진 적이 있다."""
        import tomllib

        data = tomllib.loads((ROOT / "janus_server/pyproject.toml").read_text(encoding="utf-8"))
        declared = {dep.split(">")[0].split("=")[0] for dep in data["project"]["dependencies"]}
        self.assertEqual({"fastapi", "openai", "pyyaml", "uvicorn"}, declared)
        lock = (ROOT / "janus_server/uv.lock").read_text(encoding="utf-8")
        for name in declared:
            self.assertIn(f'name = "{name}"', lock, f"{name}이 잠금에서 빠졌다")

    def test_contributing_commands_actually_exist(self):
        """기여자가 그대로 복사해 돌리는 명령이다. 없는 스크립트를 적으면 첫인상에서 막힌다."""
        import json

        text = (ROOT / "CONTRIBUTING.md").read_text(encoding="utf-8")
        scripts = json.loads((ROOT / "janus/package.json").read_text(encoding="utf-8"))["scripts"]
        for name in re.findall(r"pnpm ([a-z:]+)", text):
            if name in {"install", "test", "audit", "dev"}:
                continue  # pnpm 내장 또는 별도 검증
            self.assertIn(name, scripts, f"CONTRIBUTING이 없는 스크립트를 안내한다: pnpm {name}")

    def test_third_party_notices_ship_with_the_app(self):
        """MIT·BSD·ISC·Apache는 고지를 배포물과 함께 제공할 것을 요구한다."""
        config = (ROOT / "janus/electron-builder.config.cjs").read_text(encoding="utf-8")
        self.assertIn("THIRD-PARTY-NOTICES.md", config)
        self.assertIn('from: "../LICENSE"', config)
        notices = ROOT / "janus/THIRD-PARTY-NOTICES.md"
        self.assertTrue(notices.exists(), "고지 파일이 없다 — pnpm notices")
        self.assertIn("Apache-2.0", notices.read_text(encoding="utf-8")[:2000])

    def test_security_policy_names_the_gaps(self):
        text = (ROOT / "SECURITY.md").read_text(encoding="utf-8")
        self.assertIn("security/advisories", text, "신고 경로가 없다")
        # 있는 경계만 적고 없는 경계를 숨기면 문서가 다시 거짓말을 시작한다.
        for gap in ("run_bash", "not path-jailed", "Tasks are not isolated"):
            self.assertIn(gap, text, gap)

    def test_tracked_artifacts_stay_minimal(self):
        """벤치마크 산출물이 추적 파일의 46%를 차지하고 개인 경로 54개를 싣고 있었다.

        v1 감사가 실제로 읽는 것만 추적한다. audit_v1.py가 새 파일을 읽기 시작하면
        이 목록도 같이 늘려야 한다 — 조용히 되돌아가지 않게.
        """
        import subprocess

        tracked = subprocess.run(
            ["git", "ls-files", "janus_server/artifacts"],
            cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split()
        audit = (ROOT / "scripts/audit_v1.py").read_text(encoding="utf-8")
        required = set(re.findall(r'"(janus_server/artifacts/[^"]+)"', audit))
        self.assertEqual(
            required, set(tracked),
            "추적 중인 artifacts가 v1 감사가 읽는 집합과 다르다",
        )
        for name in tracked:
            self.assertNotIn(
                "/Users/", (ROOT / name).read_text(encoding="utf-8"),
                f"{name}에 로컬 경로가 남아 있다",
            )

    def test_no_personal_paths_in_tracked_source(self):
        """남의 홈 경로를 glob하면 clone한 사람에게서 실패한다.

        게시되는 것만 본다 — git 추적 파일 중 소스만. 낡은 worktree나 이 테스트
        자신(문자열을 담고 있다)은 대상이 아니다.
        """
        import subprocess

        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, text=True, check=True,
        ).stdout.split("\0")
        suffixes = {".py", ".ts", ".tsx", ".mts", ".cjs", ".mjs", ".sh", ".swift"}
        offenders: list[str] = []
        for name in listed:
            if not name or pathlib.Path(name).suffix not in suffixes:
                continue
            if name == "janus_server/tests/test_docs_match_code.py":
                continue  # 이 검사 자신이 찾는 문자열을 담고 있다
            path = ROOT / name
            if not path.exists():
                continue
            if "/Users/kimsemin" in path.read_text(encoding="utf-8", errors="ignore"):
                offenders.append(name)
        self.assertEqual([], offenders, "추적 중인 소스에 개인 홈 경로가 있다")
