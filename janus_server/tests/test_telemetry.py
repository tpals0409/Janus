"""P0 실행 계측 스키마의 시간 회계와 ID 귀속 테스트."""

from __future__ import annotations

import unittest

from janus_server.telemetry import ExecutionTelemetry


class FakeClock:
    def __init__(self):
        self.now_ns = 0

    def __call__(self) -> int:
        return self.now_ns

    def advance(self, milliseconds: int) -> None:
        self.now_ns += milliseconds * 1_000_000


class TelemetryTests(unittest.TestCase):
    def test_whole_task_time_is_explained_by_exclusive_top_level_intervals(self):
        clock = FakeClock()
        trace = ExecutionTelemetry(
            task_id="task-fixed", session_id="session-fixed", clock=clock
        )

        clock.advance(5)
        dispatch_id = trace.begin_turn()
        trace.record_event(
            "resource_queue_enter", node_id="orchestrator",
            dispatch_id=dispatch_id, worker_id=None,
            operation_id="model-1", resource="model_generation",
        )
        clock.advance(2)
        trace.record_event(
            "resource_lease_acquired", node_id="orchestrator",
            dispatch_id=dispatch_id, worker_id=None,
            operation_id="model-1", resource="model_generation",
        )
        trace.record_event(
            "model_generation_start", node_id="orchestrator",
            dispatch_id=dispatch_id, worker_id=None, operation_id="model-1",
        )
        clock.advance(10)
        trace.record_event(
            "model_generation_end", node_id="orchestrator",
            dispatch_id=dispatch_id, worker_id=None,
            operation_id="model-1", status="success",
        )
        trace.record_event(
            "resource_queue_enter", node_id="worker-1",
            dispatch_id=dispatch_id, worker_id="worker-1",
            operation_id="tool-1", resource="tool", tool="echo",
        )
        clock.advance(1)
        trace.record_event(
            "resource_lease_acquired", node_id="worker-1",
            dispatch_id=dispatch_id, worker_id="worker-1",
            operation_id="tool-1", resource="tool", tool="echo",
        )
        trace.record_event(
            "tool_run_start", node_id="worker-1",
            dispatch_id=dispatch_id, worker_id="worker-1",
            operation_id="tool-1", name="echo",
        )
        clock.advance(4)
        trace.record_event(
            "tool_run_end", node_id="worker-1",
            dispatch_id=dispatch_id, worker_id="worker-1",
            operation_id="tool-1", status="success",
        )
        trace.record_event(
            "verification_start", node_id="verifier",
            dispatch_id=dispatch_id, worker_id="verifier",
            operation_id="verify-1", command="pytest",
        )
        clock.advance(3)
        trace.record_event(
            "verification_end", node_id="verifier",
            dispatch_id=dispatch_id, worker_id="verifier",
            operation_id="verify-1", status="success",
        )
        trace.end_turn(dispatch_id, status="success")
        clock.advance(4)

        saved = trace.snapshot(
            usage={"orchestrator": {"prompt_tokens": 7, "completion_tokens": 3}},
            worker_count=1,
        )

        self.assertEqual("monotonic_ns", saved["clock"])
        self.assertEqual(29.0, saved["elapsed_ms"])
        self.assertEqual(29.0, saved["top_level_accounted_ms"])
        self.assertEqual(0.0, saved["top_level_unaccounted_ms"])
        self.assertEqual(20.0, saved["totals_ms"]["active_turn"])
        self.assertEqual(9.0, saved["totals_ms"]["user_wait"])
        self.assertEqual(3.0, saved["totals_ms"]["resource_queue"])
        self.assertEqual(10.0, saved["totals_ms"]["model_generation"])
        self.assertEqual(4.0, saved["totals_ms"]["tool_run"])
        self.assertEqual(3.0, saved["totals_ms"]["verification"])
        self.assertEqual({"prompt": 7, "completion": 3, "total": 10}, saved["tokens"])
        self.assertEqual(1, saved["worker_count"])
        self.assertGreater(saved["memory_snapshots"][0]["process_peak_rss_bytes"], 0)
        for event in saved["events"]:
            self.assertEqual("task-fixed", event["task_id"])
            self.assertEqual("session-fixed", event["session_id"])
            self.assertEqual(dispatch_id, event["dispatch_id"])


if __name__ == "__main__":
    unittest.main()
