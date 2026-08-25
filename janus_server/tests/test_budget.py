"""P2 Dispatch and RuntimeWorker budget enforcement tests."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from janus_server import agent, budget, runtime
from janus_server import scheduler as scheduler_mod
from janus_server.budget import BudgetTracker, normalize_budget
from janus_server.scheduler import ResourceScheduler
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient


class FakeClock:
    def __init__(self):
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, milliseconds: int) -> None:
        self.now_ns += milliseconds * 1_000_000


class BudgetTests(unittest.TestCase):
    def test_token_step_and_active_time_limits_are_independent(self):
        clock = FakeClock()
        steps = BudgetTracker(
            "dispatch", {"token_limit": 100, "time_limit_ms": 100, "step_limit": 2},
            clock=clock,
        )
        steps.begin_active()
        self.assertTrue(steps.claim_step())
        self.assertTrue(steps.claim_step())
        self.assertFalse(steps.claim_step())
        self.assertEqual("dispatch:step_limit", steps.exhausted_reason)

        tokens = BudgetTracker(
            "worker:w1", {"token_limit": 3, "time_limit_ms": 100, "step_limit": 3},
            clock=clock,
        )
        tokens.begin_active()
        self.assertTrue(tokens.add_tokens(1, 1))
        self.assertFalse(tokens.add_tokens(1, 0))
        self.assertEqual("worker:w1:token_limit", tokens.exhausted_reason)

        timed = BudgetTracker(
            "dispatch", {"token_limit": 100, "time_limit_ms": 10, "step_limit": 3},
            clock=clock,
        )
        timed.begin_active()
        clock.advance(10)
        self.assertFalse(timed.available())
        self.assertEqual("dispatch:time_limit", timed.exhausted_reason)

    def test_one_dispatch_exhaustion_does_not_consume_another_budget(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            first_context = WorkspaceContext(
                root=root, task_id="task-a", workspace_id="workspace-a"
            ).for_dispatch("dispatch-a")
            second_context = WorkspaceContext(
                root=root, task_id="task-b", workspace_id="workspace-b"
            ).for_dispatch("dispatch-b")
            first = BudgetTracker(
                "dispatch", {"token_limit": 1, "time_limit_ms": 1000, "step_limit": 3}
            )
            second = BudgetTracker(
                "dispatch", {"token_limit": 100, "time_limit_ms": 1000, "step_limit": 3}
            )
            first.begin_active()
            second.begin_active()

            agent.run(
                client=FakeClient([{"text": "first"}]), model="fake",
                system_prompt="test", task="go", tool_names=[],
                workspace_context=first_context, approve=lambda *_: True,
                emit=lambda *_args, **_kwargs: None,
                scheduler=ResourceScheduler(), budget_trackers=[first],
            )
            agent.run(
                client=FakeClient([{"text": "second"}]), model="fake",
                system_prompt="test", task="go", tool_names=[],
                workspace_context=second_context, approve=lambda *_: True,
                emit=lambda *_args, **_kwargs: None,
                scheduler=ResourceScheduler(), budget_trackers=[second],
            )

        self.assertEqual("dispatch:token_limit", first.exhausted_reason)
        self.assertIsNone(second.exhausted_reason)
        self.assertEqual(2, first.snapshot()["usage"]["prompt_tokens"]
                         + first.snapshot()["usage"]["completion_tokens"])
        self.assertEqual(2, second.snapshot()["usage"]["prompt_tokens"]
                         + second.snapshot()["usage"]["completion_tokens"])

    def test_whitespace_only_answer_is_not_emitted(self):
        """개행뿐인 답은 화면에 빈 말풍선만 남긴다 — 이벤트로 내보내지 않는다."""
        with tempfile.TemporaryDirectory() as tmp:
            context = WorkspaceContext(Path(tmp), "task_blank", "workspace_blank")
            events: list[tuple[str, dict]] = []
            agent.run(
                client=FakeClient([{"text": "\n\n"}]), model="fake",
                system_prompt="test", task="go", tool_names=[],
                workspace_context=context, approve=lambda *_: True,
                emit=lambda kind, **data: events.append((kind, data)),
                scheduler=ResourceScheduler(),
            )
        answers = [data for kind, data in events if kind == "assistant"]
        self.assertEqual([], answers)
        # 조각 자체는 흘러야 한다 — 지우는 건 완결된 빈 답뿐이다.
        self.assertIn("text_delta", [kind for kind, _ in events])

    def test_mid_conversation_worker_request_beats_the_first_message_snapshot(self):
        """Dispatch 예산은 첫 메시지로 정해진다 — 대화 도중 시킨 일이 거부되면 안 된다."""
        budget = normalize_budget({
            "workers": {"total_limit": 4, "concurrent_limit": 1},
        })
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: FakeClient([])),
        ):
            context = WorkspaceContext(
                root=Path(tmp), task_id="task", workspace_id="workspace"
            ).for_dispatch("dispatch")
            orch = runtime.Orchestration(
                {"name": "test", "model": "qwen3.8-27b", "tools": ["echo"]},
                send=lambda _event: None, approver=None,
                workspace_context=context, scheduler=ResourceScheduler(), budget=budget,
            )
            # 세션 첫 메시지("너는 누구야")로 정해진 스냅샷은 1이다.
            orch.current_user_text = "너는 누구야"
            self.assertEqual(1, orch._concurrent_worker_limit())
            # 대화 도중의 명시 요청은 한글 수사로도 읽어야 한다.
            orch.current_user_text = "워커 두개 배치해서, 이 프로젝트 조사해"
            self.assertEqual(2, orch._concurrent_worker_limit())
            # total_limit은 넘지 않는다.
            orch.current_user_text = "워커 열개 만들어"
            self.assertEqual(4, orch._concurrent_worker_limit())

    def test_worker_total_and_concurrent_caps_are_enforced(self):
        budget = normalize_budget({
            "workers": {"total_limit": 2, "concurrent_limit": 1},
        })
        entered = threading.Event()
        release = threading.Event()

        def blocking_worker():
            entered.set()
            if not release.wait(2):
                raise AssertionError("worker release timeout")
            return {"text": "done"}

        fake = FakeClient([blocking_worker, {"text": "done"}])
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
        ):
            context = WorkspaceContext(
                root=Path(tmp), task_id="task", workspace_id="workspace"
            ).for_dispatch("dispatch")
            orch = runtime.Orchestration(
                {"name": "test", "model": "qwen3.8-27b", "tools": ["echo"]},
                send=lambda _event: None, approver=None,
                workspace_context=context, scheduler=ResourceScheduler(), budget=budget,
            )
            orch.current_dispatch_id = "dispatch"
            orch.active_workspace_context = context
            handler = orch.create_worker["handler"]
            first_result: list[dict] = []
            first = threading.Thread(target=lambda: first_result.append(handler(
                name="one", task="go", tools=[], max_steps=2
            )))
            first.start()
            self.assertTrue(entered.wait(2))
            concurrent = handler(name="two", task="go", tools=[], max_steps=2)
            self.assertIn("concurrent budget", concurrent["error"])
            release.set()
            first.join(2)
            waiter = next(tool for tool in orch.worker_control_tools
                          if tool["name"] == "wait_worker")["handler"]
            first_done = waiter(first_result[0]["worker"], 2)
            second = handler(name="two", task="go", tools=[], max_steps=2)
            second_done = waiter(second["worker"], 2)
            total = handler(name="three", task="go", tools=[], max_steps=2)

        self.assertEqual("done", first_done["result"])
        self.assertEqual("done", second_done["result"])
        self.assertIn("total budget", total["error"])
        usage = orch.snapshot_budget()["usage"]
        self.assertEqual(2, usage["workers_started"])
        self.assertEqual(1, usage["peak_concurrent_workers"])

    def test_runtime_worker_token_budget_returns_reusable_partial_result(self):
        budget = normalize_budget({
            "worker": {"token_limit": 1, "step_limit": 2},
            "workers": {"total_limit": 1, "concurrent_limit": 1},
        })
        fake = FakeClient([{"text": "too many tokens"}])
        with (
            tempfile.TemporaryDirectory() as tmp,
            patch.object(runtime, "resolve_local_model", lambda name: name),
            patch.object(runtime, "make_client", lambda: fake),
        ):
            context = WorkspaceContext(
                root=Path(tmp), task_id="task", workspace_id="workspace"
            ).for_dispatch("dispatch")
            orch = runtime.Orchestration(
                {"name": "test", "model": "qwen3.8-27b", "tools": []},
                send=lambda _event: None, approver=None,
                workspace_context=context, scheduler=ResourceScheduler(), budget=budget,
            )
            orch.current_dispatch_id = "dispatch"
            orch.active_workspace_context = context
            result = orch.create_worker["handler"](
                name="limited", task="go", tools=[], max_steps=2
            )
            waiter = next(tool for tool in orch.worker_control_tools
                          if tool["name"] == "wait_worker")["handler"]
            result = waiter(result["worker"], 2)
            reused = orch.create_worker["handler"](
                name="limited", task="go", tools=[], max_steps=2
            )

        self.assertEqual("completed_partial", result["status"])
        self.assertEqual(1, result["recovery_limits"]["file_reads"])
        self.assertEqual(1, result["recovery_limits"]["validation_commands"])
        self.assertIn("assigned workspace", result["recovery_instruction"])
        self.assertIn("Do not spawn another worker", result["result"])
        self.assertTrue(reused["reused"])
        self.assertEqual(result["result"], reused["result"])
        self.assertEqual(1, orch.worker_seq)
        self.assertIsNone(orch.dispatch_budget.exhausted_reason)



class SuppressionGuidanceTests(unittest.TestCase):
    """일시적 억제에 "직접 하라"고 시키면 위임이 조용히 사라진다."""

    def test_queue_backpressure_tells_the_orchestrator_to_wait_not_to_implement(self):
        guidance = runtime.suppression_guidance("model_queue_backpressure")
        self.assertIn("wait_worker", guidance)
        self.assertIn("Do not implement the work yourself", guidance)
        self.assertNotIn("complete the task directly", guidance)

    def test_a_duplicate_worker_is_integrated_not_reimplemented(self):
        guidance = runtime.suppression_guidance("duplicate_worker_running")
        self.assertIn("wait_worker", guidance)
        self.assertIn("Do not implement it again yourself", guidance)

    def test_a_structural_policy_still_allows_direct_completion(self):
        guidance = runtime.suppression_guidance("worker_policy_fixed_one")
        self.assertIn("complete the task directly", guidance)
        self.assertIn("suppressed", guidance)

class BudgetCancelEventProtocolTests(unittest.TestCase):
    def test_setting_a_budget_cancel_marks_it_cancelled(self):
        cancel = budget.BudgetCancel(None, [])
        self.assertFalse(cancel.is_set())
        cancel.set()
        self.assertTrue(cancel.is_set())

    def test_setting_a_budget_cancel_sets_the_external_event(self):
        external = threading.Event()
        cancel = budget.BudgetCancel(external, [])
        cancel.set()
        self.assertTrue(external.is_set())
        self.assertTrue(cancel.is_set())

    def test_scheduler_close_cancels_a_lease_held_with_a_budget_cancel(self):
        scheduler = scheduler_mod.ResourceScheduler()
        cancel = budget.BudgetCancel(None, [])
        lease = scheduler.acquire(
            scheduler_mod.ResourceClass.MODEL_GENERATION, cancel=cancel,
        )
        # 종료가 AttributeError로 죽으면 shutdown_local_resources가 완주하지 못한다.
        scheduler.close()
        self.assertTrue(cancel.is_set())
        lease.release()

if __name__ == "__main__":
    unittest.main()
