"""실패한 노드를 성공으로 표시하지 않는지 검증한다."""

from __future__ import annotations

import unittest

from janus_server import compile as C
from janus_server import trace


class TraceStatusTests(unittest.IsolatedAsyncioTestCase):
    async def test_structured_tool_error_marks_span_as_error(self):
        spec = {
            "name": "tool-error",
            "nodes": [
                {"id": "start", "type": "start", "outputs": []},
                {
                    "id": "broken_tool",
                    "type": "tool",
                    "tool": "echo",
                    "inputs": {},  # echo의 필수 text 인자를 의도적으로 생략
                    "output": {"name": "result"},
                },
                {
                    "id": "end",
                    "type": "end",
                    "inputs": {"result": "{{ broken_tool.result }}"},
                },
            ],
            "edges": [
                {"from": "start", "to": "broken_tool"},
                {"from": "broken_tool", "to": "end"},
            ],
        }
        node_types = {node["id"]: node["type"] for node in spec["nodes"]}
        events = [
            event
            async for event in trace.run(
                C.build(spec), C.initial_state(spec, {}), node_types
            )
        ]

        span = next(
            event["span"]
            for event in events
            if event["type"] == "span_end" and event["span"]["node_id"] == "broken_tool"
        )
        self.assertEqual("error", span["status"])
        self.assertIn("인자 오류", span["output"]["result"]["error"])


if __name__ == "__main__":
    unittest.main()
