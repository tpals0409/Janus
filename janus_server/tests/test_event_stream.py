from __future__ import annotations

import os
import unittest

os.environ.setdefault("JANUS_AUTH_TOKEN", "test-token")

from fastapi.testclient import TestClient

from janus_server import server, shared


class EventStreamTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(server.app)

    def test_authenticated_subscriber_receives_published_event(self):
        with self.client.websocket_connect(
            "/events", subprotocols=["janus", server.AUTH_TOKEN]
        ) as websocket:
            self.assertEqual("ready", websocket.receive_json()["event"])
            shared._EVENT_BUS.publish("workspace", "ready", task_id="task_1")
            event = websocket.receive_json()
            self.assertEqual("workspace", event["topic"])
            self.assertEqual("ready", event["event"])
            self.assertEqual("task_1", event["task_id"])

    def test_invalid_token_is_rejected(self):
        try:
            with self.client.websocket_connect(
                "/events", subprotocols=["janus", "wrong-token"]
            ):
                pass
            self.fail("잘못된 토큰 연결이 수락되었다")
        except Exception:
            pass


if __name__ == "__main__":
    unittest.main()
