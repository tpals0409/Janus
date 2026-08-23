"""P2 local ResourceScheduler policy and runtime integration tests."""

from __future__ import annotations

import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from janus_server import agent, verification
from janus_server.scheduler import (
    LeaseCancelled,
    LeaseTimeout,
    ResourceClass,
    ResourceScheduler,
    SchedulerClosed,
    assess_vram_sizing,
)
from janus_server.workspace import WorkspaceContext
from tests.fakes import FakeClient


def wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition timeout")
        time.sleep(0.005)


class MutableClock:
    def __init__(self):
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


class SchedulerTests(unittest.TestCase):
    def test_vram_sizing_starts_only_after_sustained_measured_bottleneck(self):
        sparse = [
            {"kind": "lease_acquired", "resource": "model_generation", "queue_wait_ms": 5000}
            for _ in range(3)
        ]
        healthy = [
            {"kind": "lease_acquired", "resource": "model_generation", "queue_wait_ms": 20}
            for _ in range(20)
        ]
        blocked = [
            {"kind": "lease_acquired", "resource": "model_generation", "queue_wait_ms": 1500}
            for _ in range(20)
        ]

        self.assertEqual("deferred", assess_vram_sizing(sparse)["status"])
        self.assertEqual("insufficient_samples", assess_vram_sizing(sparse)["reason"])
        self.assertEqual("deferred", assess_vram_sizing(healthy)["status"])
        self.assertEqual("model_slot_wait_below_threshold", assess_vram_sizing(healthy)["reason"])
        self.assertEqual("recommended", assess_vram_sizing(blocked)["status"])
        self.assertEqual("measured_model_slot_bottleneck", assess_vram_sizing(blocked)["reason"])

    def test_runtime_and_verification_exceptions_leave_no_active_lease(self):
        scheduler = ResourceScheduler()
        with tempfile.TemporaryDirectory() as tmp:
            context = WorkspaceContext(
                root=Path(tmp), task_id="task", workspace_id="workspace"
            ).for_dispatch("dispatch")

            def fail_generation():
                raise RuntimeError("generation failed")

            with self.assertRaisesRegex(RuntimeError, "generation failed"):
                agent.run(
                    client=FakeClient([fail_generation]), model="fake",
                    system_prompt="test", task="go", tool_names=[],
                    workspace_context=context, approve=lambda *_: True,
                    emit=lambda *_args, **_kwargs: None, scheduler=scheduler,
                )
            self.assertEqual(0, scheduler.snapshot()["active_leases"])

            with patch("janus_server.verification.subprocess.run", side_effect=OSError("spawn")):
                with self.assertRaisesRegex(OSError, "spawn"):
                    verification.run("test", context, scheduler=scheduler)
            self.assertEqual(0, scheduler.snapshot()["active_leases"])

    def test_timeout_removes_waiter_and_release_returns_to_zero(self):
        scheduler = ResourceScheduler({ResourceClass.MODEL_GENERATION: 1})
        held = scheduler.acquire(ResourceClass.MODEL_GENERATION)

        with self.assertRaises(LeaseTimeout):
            scheduler.acquire(ResourceClass.MODEL_GENERATION, timeout=0.02)

        snapshot = scheduler.snapshot()
        self.assertEqual(1, snapshot["active_leases"])
        self.assertEqual(0, snapshot["resources"]["model_generation"]["queued"])
        held.release()
        self.assertEqual(0, scheduler.snapshot()["active_leases"])

    def test_cancelled_waiter_reports_reason_and_is_removed(self):
        scheduler = ResourceScheduler({ResourceClass.MODEL_GENERATION: 1})
        held = scheduler.acquire(ResourceClass.MODEL_GENERATION)
        cancel = threading.Event()
        wait_reports: list[dict] = []
        errors: list[BaseException] = []

        def wait_for_lease() -> None:
            try:
                scheduler.acquire(
                    ResourceClass.MODEL_GENERATION,
                    cancel=cancel,
                    on_wait=wait_reports.append,
                )
            except BaseException as error:
                errors.append(error)

        waiting = threading.Thread(target=wait_for_lease)
        waiting.start()
        wait_until(lambda: bool(wait_reports))
        cancel.set()
        waiting.join(2)

        self.assertIsInstance(errors[0], LeaseCancelled)
        self.assertEqual("capacity_exhausted", wait_reports[0]["reason"])
        self.assertEqual(1, wait_reports[0]["position"])
        self.assertEqual(0, scheduler.snapshot()["resources"]["model_generation"]["queued"])
        held.release()
        self.assertEqual(0, scheduler.snapshot()["active_leases"])

    def test_context_exception_always_releases_lease(self):
        scheduler = ResourceScheduler()
        with self.assertRaisesRegex(RuntimeError, "boom"):
            with scheduler.acquire(ResourceClass.IO_TOOL):
                raise RuntimeError("boom")
        self.assertEqual(0, scheduler.snapshot()["active_leases"])

    def test_close_cancels_active_work_rejects_queue_and_waits_for_idle(self):
        scheduler = ResourceScheduler({ResourceClass.MODEL_GENERATION: 1})
        active_cancel = threading.Event()
        active_started = threading.Event()
        queued_started = threading.Event()
        queued_errors: list[BaseException] = []

        def active_work() -> None:
            with scheduler.acquire(
                ResourceClass.MODEL_GENERATION, cancel=active_cancel
            ):
                active_started.set()
                active_cancel.wait(2)

        def queued_work() -> None:
            queued_started.set()
            try:
                scheduler.acquire(ResourceClass.MODEL_GENERATION)
            except BaseException as error:
                queued_errors.append(error)

        active = threading.Thread(target=active_work)
        queued = threading.Thread(target=queued_work)
        active.start()
        self.assertTrue(active_started.wait(2))
        queued.start()
        self.assertTrue(queued_started.wait(2))
        wait_until(lambda: scheduler.snapshot()["resources"]["model_generation"]["queued"] == 1)

        scheduler.close()
        self.assertTrue(scheduler.wait_for_idle(2))
        active.join(2)
        queued.join(2)

        self.assertTrue(active_cancel.is_set())
        self.assertIsInstance(queued_errors[0], SchedulerClosed)
        snapshot = scheduler.snapshot()
        self.assertEqual(0, snapshot["active_leases"])
        self.assertEqual(0, snapshot["resources"]["model_generation"]["queued"])

    def test_each_resource_class_enforces_its_own_cap(self):
        scheduler = ResourceScheduler({resource: 1 for resource in ResourceClass})
        for resource in ResourceClass:
            held = scheduler.acquire(resource)
            acquired = threading.Event()

            def wait_for_same_resource(selected: ResourceClass = resource) -> None:
                with scheduler.acquire(selected):
                    acquired.set()

            waiting = threading.Thread(target=wait_for_same_resource)
            waiting.start()
            wait_until(
                lambda: scheduler.snapshot()["resources"][resource.value]["queued"] == 1
            )
            self.assertFalse(acquired.is_set())
            held.release()
            self.assertTrue(acquired.wait(2))
            waiting.join(2)

        self.assertEqual(0, scheduler.snapshot()["active_leases"])

    def test_priority_queue_runs_high_priority_first(self):
        scheduler = ResourceScheduler({ResourceClass.MODEL_GENERATION: 1})
        held = scheduler.acquire(ResourceClass.MODEL_GENERATION)
        order: list[str] = []

        def run(name: str, priority: int) -> None:
            with scheduler.acquire(ResourceClass.MODEL_GENERATION, priority=priority):
                order.append(name)

        low = threading.Thread(target=run, args=("low", 1))
        high = threading.Thread(target=run, args=("high", 5))
        low.start()
        wait_until(lambda: scheduler.snapshot()["resources"]["model_generation"]["queued"] == 1)
        high.start()
        wait_until(lambda: scheduler.snapshot()["resources"]["model_generation"]["queued"] == 2)
        held.release()
        low.join(2)
        high.join(2)

        self.assertEqual(["high", "low"], order)
        self.assertEqual(0, scheduler.snapshot()["active_leases"])

    def test_aging_prevents_old_low_priority_waiter_starvation(self):
        clock = MutableClock()
        scheduler = ResourceScheduler(
            {ResourceClass.MODEL_GENERATION: 1}, aging_interval=1, clock=clock
        )
        held = scheduler.acquire(ResourceClass.MODEL_GENERATION)
        order: list[str] = []

        def run(name: str, priority: int) -> None:
            with scheduler.acquire(ResourceClass.MODEL_GENERATION, priority=priority):
                order.append(name)

        low = threading.Thread(target=run, args=("aged-low", 0))
        low.start()
        wait_until(lambda: scheduler.snapshot()["resources"]["model_generation"]["queued"] == 1)
        clock.now = 10
        high = threading.Thread(target=run, args=("new-high", 5))
        high.start()
        wait_until(lambda: scheduler.snapshot()["resources"]["model_generation"]["queued"] == 2)
        held.release()
        low.join(2)
        high.join(2)

        self.assertEqual(["aged-low", "new-high"], order)

    def test_model_generation_and_verification_overlap_on_independent_caps(self):
        scheduler = ResourceScheduler({
            ResourceClass.MODEL_GENERATION: 1,
            ResourceClass.VERIFICATION: 1,
        })
        model_started = threading.Event()
        verification_started = threading.Event()
        timeline: list[tuple[str, float]] = []
        errors: list[BaseException] = []

        def model_turn():
            if not verification_started.wait(2):
                raise AssertionError("verification did not overlap model generation")
            return {"text": "done"}

        fake = FakeClient([model_turn])
        with tempfile.TemporaryDirectory() as tmp:
            context = WorkspaceContext(
                root=Path(tmp), task_id="task", workspace_id="workspace"
            ).for_dispatch("dispatch")

            def model_emit(kind: str, **_data) -> None:
                timeline.append((kind, time.monotonic()))
                if kind == "model_generation_start":
                    model_started.set()

            def run_model() -> None:
                try:
                    agent.run(
                        client=fake, model="fake", system_prompt="test", task="go",
                        tool_names=[], workspace_context=context,
                        approve=lambda *_: True, emit=model_emit,
                        scheduler=scheduler,
                    )
                except BaseException as error:
                    errors.append(error)

            model_thread = threading.Thread(target=run_model)
            model_thread.start()
            self.assertTrue(model_started.wait(2))

            def verification_emit(kind: str, **_data) -> None:
                timeline.append((kind, time.monotonic()))
                if kind == "verification_start":
                    verification_started.set()

            result = verification.run(
                "python -c 'print(42)'", context,
                scheduler=scheduler, emit=verification_emit,
            )
            model_thread.join(2)

        self.assertFalse(errors)
        self.assertEqual(0, result["exit_code"])
        times = {kind: at for kind, at in timeline}
        self.assertLess(times["model_generation_start"], times["verification_start"])
        self.assertLess(times["verification_start"], times["model_generation_end"])
        self.assertEqual(0, scheduler.snapshot()["active_leases"])


if __name__ == "__main__":
    unittest.main()
