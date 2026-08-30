"""오케스트레이터-워커 런타임 회귀 테스트 — 전부 FakeClient, MLX 불필요."""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")
os.environ.setdefault("JANUS_ALLOWED_ORIGINS", "http://localhost:5173")

from janus_server import agent, runtime
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient

ORIGIN = "http://localhost:5173"
SPEC = {"name": "Orch", "model": "qwen3.8-27b", "system_prompt": "orchestrate",
        "tools": ["echo"], "approval": "auto", "max_steps": 6,
        "allow_autonomous_workers": True}


def worker_args(name="helper", tools=None, task="do it"):
    return json.dumps({"name": name, "system_prompt": "work", "task": task,
                       "role": "researcher",
                       "tools": tools if tools is not None else ["echo"]})


class RuntimeTests(unittest.TestCase):
    def test_skills_are_catalogued_then_loaded_lazily_with_capability_gate(self):
        fake = FakeClient([])
        loaded: list[tuple[str, str, int]] = []
        events: list[dict] = []
        skill = {
            "skill_id": "skill_review", "skill_version_id": "skill_version_review",
            "namespace": "claude", "name": "review", "description": "Review changes",
            "version": 1, "activation_mode": "auto", "compatibility": "native",
            "compiled": {
                "instructions": "Review {{input}} in {{workspace_root}}.",
                "capabilities": {"required": ["echo"]},
                "execution": {"context": "inline"},
                "resources": [{"path": "checklist.md", "binary": False, "content": "Check tests."}],
            },
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
        ):
            orch = runtime.Orchestration(
                {**SPEC, "skills": [skill]}, send=events.append, approver=None,
                workspace_context=WorkspaceContext(
                    root=Path(tmp), task_id="task_skill", workspace_id="workspace_skill",
                ),
                session_id="session_skill",
                on_skill_loaded=lambda version, reason, tokens: loaded.append((version, reason, tokens)),
            )

            self.assertIn("claude:review [auto]: Review changes", orch.session.system_prompt)
            self.assertNotIn("Review {{input}}", orch.session.system_prompt)
            resource = next(tool for tool in orch.skill_tools if tool["name"] == "read_skill_resource")
            self.assertIn("error", resource["handler"](name="review", path="checklist.md"))
            orch.current_user_text = "review this patch"
            loader = next(tool for tool in orch.skill_tools if tool["name"] == "load_skill")
            result = loader["handler"](name="review", reason="code changed")
            self.assertIn("review this patch", result["instructions"])
            self.assertIn(str(Path(tmp)), result["instructions"])
            self.assertEqual("Check tests.", resource["handler"](
                name="review", path="checklist.md",
            )["content"])
            self.assertEqual("skill_version_review", loaded[0][0])
            self.assertEqual("skill_loaded", events[0]["type"])
            self.assertTrue(loader["handler"](name="review")["already_loaded"])

    def test_manual_skill_requires_explicit_user_name_and_missing_capability_is_rejected(self):
        fake = FakeClient([])
        base = {
            "skill_id": "skill_deploy", "skill_version_id": "skill_version_deploy",
            "namespace": "claude", "name": "deploy", "description": "Deploy app",
            "version": 1, "activation_mode": "manual", "compatibility": "native",
            "compiled": {"instructions": "Deploy.", "capabilities": {"required": ["run_bash"]}},
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
        ):
            events = []
            orch = runtime.Orchestration(
                {**SPEC, "skills": [base]}, send=events.append, approver=None,
                workspace_context=WorkspaceContext(
                    root=Path(tmp), task_id="task_manual", workspace_id="workspace_manual",
                ),
            )
            loader = next(tool for tool in orch.skill_tools if tool["name"] == "load_skill")
            orch.current_user_text = "ship this"
            self.assertIn("수동 스킬", loader["handler"](name="deploy")["error"])
            self.assertEqual("skill_load_failed", events[-1]["type"])
            self.assertEqual("deploy", events[-1]["requested"])
            orch.current_user_text = "redeployment plan"
            self.assertIn("수동 스킬", loader["handler"](name="deploy")["error"])
            orch.current_user_text = "use /deploy"
            self.assertIn("필요한 capability", loader["handler"](name="deploy")["error"])
            self.assertIn("run_bash", events[-1]["reason"])

    def test_loaded_session_skill_is_not_injected_twice_after_resume(self):
        fake = FakeClient([])
        skill = {
            "skill_id": "skill_review", "skill_version_id": "skill_version_review",
            "namespace": "claude", "name": "review", "description": "Review changes",
            "version": 1, "activation_mode": "auto", "compatibility": "native",
            "loaded_at": "2026-08-23T00:00:00Z",
            "compiled": {
                "instructions": "Long review instructions.",
                "capabilities": {"required": []}, "resources": [],
            },
        }
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
        ):
            orch = runtime.Orchestration(
                {**SPEC, "skills": [skill]}, send=lambda _event: None, approver=None,
                workspace_context=WorkspaceContext(
                    root=Path(tmp), task_id="task_resume", workspace_id="workspace_resume",
                ),
            )
            loader = next(tool for tool in orch.skill_tools if tool["name"] == "load_skill")
            result = loader["handler"](name="review")
            self.assertTrue(result["already_loaded"])
            self.assertNotIn("Long review instructions", result["instructions"])

    def test_worker_status_violation_flags_terminal_overwrites(self):
        # 라이브 상태 간 전이와 후속(followup) 재기동은 합법이다.
        self.assertIsNone(runtime.worker_status_violation(None, "queued"))
        self.assertIsNone(runtime.worker_status_violation("running", "stopping"))
        self.assertIsNone(runtime.worker_status_violation("completed", "queued"))
        self.assertIsNone(runtime.worker_status_violation("failed", "failed"))
        # 종료 기록을 덮는 늦은 스레드는 레이스다 — 텔레메트리에 보여야 한다.
        self.assertEqual("completed->failed",
                         runtime.worker_status_violation("completed", "failed"))
        self.assertEqual("cancelled->queued",
                         runtime.worker_status_violation("cancelled", "queued"))

    def test_read_only_narrowing_is_visible_to_the_model(self):
        fake = FakeClient([{"text": "report"}, {"text": "edited"}])
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
        ):
            orch = runtime.Orchestration(
                {**SPEC, "tools": ["read_file", "write_file"]},
                send=lambda _event: None, approver=None,
                workspace_context=WorkspaceContext(
                    root=Path(tmp), task_id="task_narrow",
                    workspace_id="workspace_narrow",
                ),
            )
            orch.turn("코드 구조를 살펴봐 줘")
            narrowed = fake.captured[0]
            names = {t["function"]["name"] for t in narrowed["tools"]}
            self.assertNotIn("write_file", names)
            self.assertIn("read_file", names)
            # 축소 사실이 모델에게 보인다 — 어휘 오판이 조용한 실패 대신
            # 사용자에게 보고되는 실패가 되게 하는 계약.
            note = next(m["content"] for m in reversed(narrowed["messages"])
                        if m["role"] == "user")
            self.assertIn("read-only tools", note)
            self.assertIn("write tools were withheld", note)

            orch.turn("확인하고 바꿔줘")  # 변형 동사 → 전체 도구, 노트 없음
            full = fake.captured[1]
            names = {t["function"]["name"] for t in full["tools"]}
            self.assertIn("write_file", names)
            note = next(m["content"] for m in reversed(full["messages"])
                        if m["role"] == "user")
            self.assertNotIn("read-only tools", note)

    @staticmethod
    def saved_run(runs: Path) -> dict:
        files = list((runs / "orch").glob("*.json"))
        assert len(files) == 1, f"실행 파일 1개 기대, 실제 {len(files)}"
        return json.loads(files[0].read_text(encoding="utf-8"))

class SessionContextTests(unittest.TestCase):
    def test_reasoning_only_empty_response_is_retried(self):
        fake = FakeClient([{}, {"text": "completed after retry"}])
        events = []
        with tempfile.TemporaryDirectory() as tmp:
            result, _ = agent.run(
                client=fake, model="fake", system_prompt="system", task="do work",
                tool_names=[],
                workspace_context=WorkspaceContext(
                    Path(tmp), "task-empty", "workspace-empty", "dispatch-empty",
                ),
                approve=lambda _name, _args: True,
                emit=lambda kind, **data: events.append({"kind": kind, **data}),
            )

        self.assertEqual("completed after retry", result)
        self.assertEqual(1, len([
            event for event in events if event["kind"] == "empty_response"
        ]))
        second_messages = fake.captured[1]["messages"]
        self.assertIn("Do not write a placeholder", second_messages[-1]["content"])
        self.assertIn("return an explicit failure", second_messages[-1]["content"])
        self.assertEqual(
            agent.DEFAULT_REASONING_BUDGET_TOKENS, fake.captured[0]["max_tokens"]
        )
        self.assertIs(False, fake.captured[1]["extra_body"]["enable_thinking"])
        self.assertIn(agent.REASONING_BUDGET_MESSAGE, second_messages[-1]["content"])

    def test_compaction_preserves_recent_objective_and_tool_pairs(self):
        session = agent.Session(
            "system", context_max_chars=4_000, context_recent_blocks=4,
        )
        for index in range(12):
            call_id = f"call-{index}"
            session.append("user", content=f"objective {index} " + "u" * 240)
            session.append("assistant", content=f"decision {index}", tool_calls=[{
                "id": call_id, "type": "function",
                "function": {"name": "echo", "arguments": "{}"},
            }])
            session.append(
                "tool_result", tool_call_id=call_id, name="echo",
                value={"text": "r" * 240},
            )
        session.append("user", content="current objective must survive")

        baseline = session.derive_messages(compact=False)
        baseline_chars = session._chars(baseline)
        compacted = session.derive_messages()
        stats = session.context_stats

        self.assertTrue(stats["compacted"])
        self.assertEqual([0], [
            index for index, message in enumerate(compacted)
            if message["role"] == "system"
        ])
        self.assertLess(stats["sent_chars"], baseline_chars)
        self.assertGreater(stats["saved_chars"], 0)
        self.assertIn("current objective must survive", [
            message["content"] for message in compacted if message["role"] == "user"
        ])
        call_ids = {
            call["id"] for message in compacted if message["role"] == "assistant"
            for call in message.get("tool_calls") or []
        }
        self.assertTrue(all(
            message["tool_call_id"] in call_ids
            for message in compacted if message["role"] == "tool"
        ))

    def test_compaction_never_removes_the_only_user_query_during_a_tool_loop(self):
        session = agent.Session(
            "system", context_max_chars=2_000, context_recent_blocks=3,
        )
        session.append("user", content="build the complete service")
        for index in range(10):
            call_id = f"call-{index}"
            session.append("assistant", content="", tool_calls=[{
                "id": call_id, "type": "function",
                "function": {"name": "echo", "arguments": "{}"},
            }])
            session.append(
                "tool_result", tool_call_id=call_id, name="echo",
                value={"text": "result " + "x" * 300},
            )

        compacted = session.derive_messages()

        self.assertTrue(session.context_stats["compacted"])
        self.assertIn("build the complete service", [
            message["content"] for message in compacted
            if message["role"] == "user"
        ])

    def test_stable_prefix_probe_reports_reuse_without_claiming_cache_hit(self):
        session = agent.Session("stable system")
        session.append("user", content="one")
        session.derive_messages()
        self.assertFalse(session.context_stats["prefix_reused"])
        session.append("assistant", content="answer")
        session.append("user", content="two")
        session.derive_messages()
        self.assertTrue(session.context_stats["prefix_reused"])

    def test_compaction_keeps_acceptance_result_with_less_input(self):
        compact = agent.Session("system", context_max_chars=3_000)
        baseline = agent.Session("system", context_max_chars=None)
        for index in range(20):
            for session in (compact, baseline):
                session.append("user", content=f"old request {index} " + "u" * 220)
                session.append("assistant", content=f"old result {index} " + "a" * 220)
        compact_client = FakeClient([{"text": "ACCEPTED"}])
        baseline_client = FakeClient([{"text": "ACCEPTED"}])
        with tempfile.TemporaryDirectory() as tmp:
            workspace = WorkspaceContext(
                Path(tmp), "task-context", "workspace-context", "dispatch-context",
            )
            common = {
                "model": "fake", "system_prompt": "", "task": "finish current task",
                "tool_names": [], "workspace_context": workspace,
                "approve": lambda _name, _args: True,
                "emit": lambda _kind, **_data: None,
            }
            compact_result, _ = agent.run(
                client=compact_client, session=compact, **common,
            )
            baseline_result, _ = agent.run(
                client=baseline_client, session=baseline, **common,
            )

        self.assertEqual("ACCEPTED", compact_result)
        self.assertEqual(compact_result, baseline_result)
        compact_chars = agent.Session._chars(compact_client.captured[0]["messages"])
        baseline_chars = agent.Session._chars(baseline_client.captured[0]["messages"])
        self.assertLess(compact_chars, baseline_chars)
        self.assertGreater((baseline_chars - compact_chars) / baseline_chars, 0.4)

    def test_worker_spawn_pressure_uses_model_queue_state(self):
        snapshot = {
            "closed": False,
            "resources": {"model_generation": {"active": 1, "queued": 1, "cap": 1}},
        }
        self.assertEqual(
            "model_queue_backpressure", runtime.worker_spawn_pressure(snapshot)
        )
        snapshot["resources"]["model_generation"]["queued"] = 0
        self.assertIsNone(runtime.worker_spawn_pressure(snapshot))

    def test_worker_room_degrades_instead_of_collapsing_as_a_chat_grows(self):
        """dispatch step 예산은 세션 전체에 누적된다 — 대화가 길어져도 절벽이 없어야 한다."""
        one_slot = {"resources": {"model_generation": {"cap": 1}}}
        rooms = [
            runtime.effective_worker_step_limit(
                8, 8, {"limits": {"step_limit": 60}, "usage": {"steps": used}}, one_slot
            )
            for used in (0, 15, 30, 45, 58)
        ]
        # 단조 감소하되 바닥값 아래로는 내려가지 않는다.
        self.assertEqual(rooms, sorted(rooms, reverse=True))
        self.assertTrue(all(room >= runtime.MIN_WORKER_STEPS for room in rooms))
        # 예전 계산식(전체에서 60% 예비)은 여기서 이미 0으로 떨어져 바닥값에 눌러앉았다.
        self.assertGreater(rooms[1], runtime.MIN_WORKER_STEPS)

    def test_worker_never_gets_a_step_budget_that_cannot_answer(self):
        """1 step짜리 worker는 tool call만 하고 budget_exhausted로 끝난다 — 반드시 실패한다."""
        one_slot = {"resources": {"model_generation": {"cap": 1}}}
        # 실제로 실패했던 상황: 한도 15, 이미 6 사용 → 부모 몫 9를 떼면 남는 게 0.
        drained = {"limits": {"step_limit": 15}, "usage": {"steps": 6}}
        self.assertEqual(
            runtime.MIN_WORKER_STEPS,
            runtime.effective_worker_step_limit(5, 8, drained, one_slot),
        )
        # 프로필이나 요청이 1을 지정해도 답을 낼 수 있는 최소치는 지킨다.
        roomy = {"limits": {"step_limit": 30}, "usage": {"steps": 1}}
        self.assertGreaterEqual(
            runtime.effective_worker_step_limit(1, 1, roomy, one_slot),
            runtime.MIN_WORKER_STEPS,
        )

    def test_single_model_slot_reserves_tight_dispatch_steps_for_parent(self):
        dispatch = {
            "limits": {"step_limit": 10},
            "usage": {"steps": 1},
        }
        one_slot = {
            "resources": {"model_generation": {"cap": 1}},
        }
        two_slots = {
            "resources": {"model_generation": {"cap": 2}},
        }

        # 남은 9 step에서 60%를 부모가 갖고 나머지가 worker 몫 — 바닥값 아래로는 안 내려간다.
        self.assertEqual(
            4,
            runtime.effective_worker_step_limit(14, 8, dispatch, one_slot),
        )
        self.assertEqual(
            8,
            runtime.effective_worker_step_limit(14, 8, dispatch, two_slots),
        )

        roomy = {
            "limits": {"step_limit": 30},
            "usage": {"steps": 1},
        }
        self.assertEqual(
            8,
            runtime.effective_worker_step_limit(14, 8, roomy, one_slot),
        )

        self.assertEqual(
            ("scout", "single_slot_tight_dispatch_scout"),
            runtime.effective_worker_role("fixed_one", "implementer", 10, one_slot),
        )
        self.assertEqual(
            ("scout", "single_slot_tight_dispatch_scout"),
            runtime.effective_worker_role("fixed_one", "implementer", 15, one_slot),
        )
        self.assertEqual(
            ("implementer", None),
            runtime.effective_worker_role("fixed_one", "implementer", 30, one_slot),
        )



if __name__ == "__main__":
    unittest.main()
