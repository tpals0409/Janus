"""대화 세션에서 스킬을 만들고 가져오는 경로의 계약.

핵심 불변식: 채팅에서 만든 스킬도 설정 화면 import와 같은 컴파일·버전 파이프라인을
타고, 만든 즉시 그 세션에서 쓸 수 있어야 한다 (재접속을 요구하면 대화형으로 만드는
의미가 없다). 승인 게이트는 로컬·구독형 양쪽에서 동일하게 걸린다.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import mcp_bridge, shared, skill_authoring
from janus_server import tools as T

PROCEDURE = (
    "## Steps\n\n1. Run the acceptance command.\n2. Read the failing output.\n"
    "3. Fix the narrowest cause.\n4. Re-run before reporting.\n"
)


@contextmanager
def store_at(tmp: Path):
    with (
        patch.dict(os.environ, {"JANUS_DB_FILE": str(tmp / "janus.sqlite3")}),
        patch.object(shared, "_DOMAIN_STORE", None),
        patch.object(shared, "_DOMAIN_STORE_PATH", None),
    ):
        yield shared.get_domain_store()


def seeded_context(store) -> SimpleNamespace:
    """기본 프로필로 task→workspace→dispatch→session을 만들고 컨텍스트를 돌려준다."""
    project = store.create_project(name="p", repo_path="/tmp/p")
    task = store.create_task(
        project_id=project["id"], title="t", objective="o",
        acceptance_command="true", base_ref="main",
    )
    workspace = store.create_workspace(
        task_id=task["id"], repo_path="/tmp/p", base_ref="main", owned=False,
    )
    dispatch = store.create_dispatch(
        task_id=task["id"], workspace_id=workspace["id"],
        agent_profile_id="agent_default",
    )
    session = store.create_session(
        task_id=task["id"], dispatch_id=dispatch["id"],
        agent_profile_id="agent_default",
    )
    return SimpleNamespace(
        root=Path("/tmp/p"), task_id=task["id"], workspace_id=workspace["id"],
        dispatch_id=dispatch["id"], session_id=session["id"],
    )


class SkillAuthoringTests(unittest.TestCase):
    def test_authored_skill_is_versioned_activated_and_usable_immediately(self):
        with tempfile.TemporaryDirectory() as tmp, store_at(Path(tmp)) as store:
            context = seeded_context(store)
            # 실행 중인 오케스트레이션을 흉내낸다 — 살아 있는 세션에 얹히는지 본다.
            live = SimpleNamespace(skill_snapshots=[])
            with patch.dict(shared._TASK_RUNTIMES, {context.session_id: live}):
                result = skill_authoring.create_skill(
                    name="Verify Before Reporting", description="검증 후 보고하는 절차",
                    instructions=PROCEDURE, activation_mode="auto", _context=context,
                )

            self.assertTrue(result["created"], result)
            self.assertEqual("project:verify-before-reporting", result["skill"])
            self.assertEqual("auto", result["activation_mode"])
            # 재접속 없이 이 턴에서 바로 load_skill 대상이 된다.
            self.assertTrue(result["usable_now"], result)
            self.assertEqual(
                ["verify-before-reporting"], [item["name"] for item in live.skill_snapshots],
            )
            self.assertIn("Re-run before reporting", live.skill_snapshots[0]["compiled"]["instructions"])

            # 프로필에 실제로 붙었고, 설정 화면이 읽는 것과 같은 목록에 보인다.
            attached = {
                item["name"]: item["activation_mode"]
                for item in store.list_agent_profile_skills("agent_default")
            }
            self.assertEqual("auto", attached["verify-before-reporting"])

            # 같은 이름으로 다시 만들면 충돌이 아니라 개정이다.
            again = skill_authoring.create_skill(
                name="verify-before-reporting", description="개정판",
                instructions=PROCEDURE + "\n5. Record the outcome.\n",
                activation_mode="manual", _context=context,
            )
            self.assertEqual(2, again["version"], again)

    def test_thin_input_is_rejected_before_anything_is_stored(self):
        with tempfile.TemporaryDirectory() as tmp, store_at(Path(tmp)) as store:
            context = seeded_context(store)
            for bad in (
                {"name": "", "description": "d", "instructions": PROCEDURE},
                {"name": "ok", "description": "", "instructions": PROCEDURE},
                {"name": "ok", "description": "d", "instructions": "too short"},
                {"name": "ok", "description": "d", "instructions": PROCEDURE,
                 "activation_mode": "sometimes"},
            ):
                result = skill_authoring.create_skill(**bad, _context=context)
                self.assertIn("error", result, bad)
            # 패키지 라이브러리 스킬은 시드로 이미 있다 — 저작된 것만 없어야 한다.
            self.assertEqual([], [
                item for item in store.list_skills()
                if item["namespace"] == skill_authoring.NAMESPACE
            ])

    def test_import_reads_a_local_folder_through_the_shared_pipeline(self):
        with tempfile.TemporaryDirectory() as tmp, store_at(Path(tmp)) as store:
            context = seeded_context(store)
            source = Path(tmp) / "pack" / "handover"
            source.mkdir(parents=True)
            (source / "SKILL.md").write_text(
                "---\nname: handover\ndescription: Hand work over\n---\n\n" + PROCEDURE,
                encoding="utf-8",
            )
            result = skill_authoring.import_skill(
                str(Path(tmp) / "pack"), activation_mode="manual", _context=context,
            )
            self.assertEqual(1, result["imported"], result)
            self.assertIn("handover", result["skills"][0]["skill"])
            self.assertIn("handover", [item["name"] for item in store.list_skills()])

    def test_missing_source_fails_as_a_tool_error_not_a_dead_turn(self):
        with tempfile.TemporaryDirectory() as tmp, store_at(Path(tmp)) as store:
            context = seeded_context(store)
            self.assertIn("error", skill_authoring.import_skill("", _context=context))
            self.assertIn(
                "error",
                skill_authoring.import_skill(str(Path(tmp) / "nope"), _context=context),
            )


class SkillToolWiringTests(unittest.TestCase):
    def test_skill_tools_are_approval_gated_on_both_runtimes(self):
        # 승인 필요 = MCP 브리지 대상. 구독형 CLI 세션도 같은 게이트를 지난다.
        self.assertIn("create_skill", T.DANGEROUS)
        self.assertIn("import_skill", T.DANGEROUS)
        self.assertNotIn("create_skill", T.READ_ONLY)
        self.assertEqual(
            ["create_skill", "import_skill"],
            mcp_bridge.bridged_tools(["read_file", "create_skill", "import_skill"]),
        )

    def test_dispatch_refuses_without_approval(self):
        denied = T.dispatch(
            "create_skill",
            {"name": "x", "description": "d", "instructions": PROCEDURE},
            approve=lambda _name, _args: False,
            context=SimpleNamespace(dispatch_id="d", task_id="t"),
        )
        self.assertIn("승인", denied["error"])

    def test_guidance_tells_the_agent_to_interview_first(self):
        guidance = T.REGISTRY["create_skill"]["guidance"]
        self.assertIn("Interview the user", guidance)
        self.assertIn("input_required", guidance)
