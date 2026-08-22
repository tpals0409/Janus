"""P2 Dispatch and RuntimeWorker budget enforcement tests."""

from __future__ import annotations

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from janus_server import agent, runtime
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
            second = handler(name="two", task="go", tools=[], max_steps=2)
            total = handler(name="three", task="go", tools=[], max_steps=2)

        self.assertEqual("done", first_result[0]["result"])
        self.assertEqual("done", second["result"])
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
            reused = orch.create_worker["handler"](
                name="limited", task="go", tools=[], max_steps=2
            )

        self.assertTrue(result["partial"])
        self.assertIn("token_limit", result["warning"])
        self.assertIn("Do not spawn another worker", result["result"])
        self.assertIn("do not invent undocumented behavior", result["result"])
        self.assertTrue(reused["reused"])
        self.assertEqual(result["result"], reused["result"])
        self.assertEqual(1, orch.worker_seq)
        self.assertIsNone(orch.dispatch_budget.exhausted_reason)


if __name__ == "__main__":
    unittest.main()
