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

    async def test_parallel_chat_events_follow_their_parent_span(self):
        class Chunk:
            def __init__(self, content):
                self.content = content

        class Output:
            def __init__(self, prompt, completion):
                self.usage_metadata = {
                    "input_tokens": prompt,
                    "output_tokens": completion,
                }

        class ParallelGraph:
            async def astream_events(self, *_args, **_kwargs):
                yield {
                    "name": "llm_a",
                    "event": "on_chain_start",
                    "run_id": "run-a",
                    "data": {"input": {"outputs": {}}},
                }
                yield {
                    "name": "llm_b",
                    "event": "on_chain_start",
                    "run_id": "run-b",
                    "data": {"input": {"outputs": {}}},
                }
                # B는 parent_ids, A는 metadata 경로를 각각 검증한다.
                yield {
                    "name": "ChatOpenAI",
                    "event": "on_chat_model_stream",
                    "run_id": "chat-b",
                    "parent_ids": ["root", "run-b"],
                    "data": {"chunk": Chunk("B-token")},
                }
                yield {
                    "name": "ChatOpenAI",
                    "event": "on_chat_model_end",
                    "run_id": "chat-b",
                    "parent_ids": ["root", "run-b"],
                    "data": {"output": Output(20, 2)},
                }
                yield {
                    "name": "ChatOpenAI",
                    "event": "on_chat_model_stream",
                    "run_id": "chat-a",
                    "metadata": {"langgraph_node": "llm_a"},
                    "data": {"chunk": Chunk("A-token")},
                }
                yield {
                    "name": "ChatOpenAI",
                    "event": "on_chat_model_end",
                    "run_id": "chat-a",
                    "metadata": {"langgraph_node": "llm_a"},
                    "data": {"output": Output(10, 1)},
                }
                for node, run_id in (("llm_b", "run-b"), ("llm_a", "run-a")):
                    yield {
                        "name": node,
                        "event": "on_chain_end",
                        "run_id": run_id,
                        "data": {"output": {"outputs": {node: {"text": node}}}},
                    }

        events = [
            event
            async for event in trace.run(
                ParallelGraph(), {}, {"llm_a": "llm", "llm_b": "llm"}
            )
        ]
        tokens = {
            event["text"]: event["node_id"]
            for event in events
            if event["type"] == "token"
        }
        spans = {
            event["span"]["node_id"]: event["span"]
            for event in events
            if event["type"] == "span_end"
        }

        self.assertEqual({"B-token": "llm_b", "A-token": "llm_a"}, tokens)
        self.assertEqual({"prompt_tokens": 20, "completion_tokens": 2}, spans["llm_b"]["usage"])
        self.assertEqual({"prompt_tokens": 10, "completion_tokens": 1}, spans["llm_a"]["usage"])

    async def test_repeated_node_runs_keep_sessions_and_usage_separate(self):
        class RepeatedAgentGraph:
            async def astream_events(self, *_args, **kwargs):
                sink = kwargs["config"]["configurable"]["event_sink"]
                for n in (1, 2):
                    run_id = f"agent-run-{n}"
                    yield {
                        "name": "worker",
                        "event": "on_chain_start",
                        "run_id": run_id,
                        "data": {"input": {"outputs": {}}},
                    }
                    sink("worker", "assistant", {"content": f"answer-{n}"})
                    sink(
                        "worker",
                        "usage",
                        {"prompt_tokens": n * 10, "completion_tokens": n},
                    )
                    yield {
                        "name": "worker",
                        "event": "on_chain_end",
                        "run_id": run_id,
                        "data": {"output": {"outputs": {"worker": {"result": n}}}},
                    }

        events = [
            event
            async for event in trace.run(
                RepeatedAgentGraph(), {}, {"worker": "agent"}
            )
        ]
        spans = [
            event["span"]
            for event in events
            if event["type"] == "span_end" and event["span"]["node_id"] == "worker"
        ]

        self.assertEqual(2, len(spans))
        self.assertEqual(
            [["answer-1"], ["answer-2"]],
            [
                [e["content"] for e in span["events"] if e["kind"] == "assistant"]
                for span in spans
            ],
        )
        self.assertEqual(
            [
                {"prompt_tokens": 10, "completion_tokens": 1},
                {"prompt_tokens": 20, "completion_tokens": 2},
            ],
            [span["usage"] for span in spans],
        )


if __name__ == "__main__":
    unittest.main()
