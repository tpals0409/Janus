from __future__ import annotations

import asyncio
import threading
import unittest

from janus_server.event_bus import EventBus


class EventBusTests(unittest.IsolatedAsyncioTestCase):
    async def test_background_thread_publish_reaches_async_subscriber(self):
        bus = EventBus()
        subscription = bus.subscribe()

        thread = threading.Thread(
            target=lambda: bus.publish("terminal", "output", terminal_id="terminal_1")
        )
        thread.start()
        thread.join()

        event = await asyncio.wait_for(subscription.queue.get(), timeout=1)
        self.assertEqual("terminal", event["topic"])
        self.assertEqual("output", event["event"])
        self.assertEqual("terminal_1", event["terminal_id"])
        bus.unsubscribe(subscription.id)

    async def test_slow_subscriber_is_bounded_and_keeps_latest_event(self):
        bus = EventBus(queue_size=2)
        subscription = bus.subscribe()
        for index in range(4):
            bus.publish("workspace", progress=index)
        await asyncio.sleep(0)

        first = subscription.queue.get_nowait()
        second = subscription.queue.get_nowait()
        self.assertEqual(2, first["progress"])
        self.assertEqual(3, second["progress"])


if __name__ == "__main__":
    unittest.main()
