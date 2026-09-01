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

    async def test_overflow_tells_the_subscriber_to_resync_instead_of_losing_silently(self):
        """조용히 오래된 것부터 버리면 실패 모드가 "재연결"이 아니라 "화면이 틀림"이다.

        무효화를 놓친 렌더러는 낡은 화면을 계속 보여주고, 클라이언트는 자기가
        무엇을 놓쳤는지조차 모른다.
        """
        bus = EventBus(queue_size=2)
        subscription = bus.subscribe()
        for index in range(4):
            bus.publish("workspace", progress=index)
        await asyncio.sleep(0)

        first = subscription.queue.get_nowait()
        self.assertEqual("resync", first["event"])
        self.assertEqual("system", first["topic"])
        self.assertEqual(2, first["dropped"])
        # resync 뒤로는 정상 전달이 이어진다 — 갭 표시는 한 번만 나온다.
        self.assertEqual(3, subscription.queue.get_nowait()["progress"])
        self.assertTrue(subscription.queue.empty())
        bus.unsubscribe(subscription.id)

    async def test_events_within_capacity_are_delivered_in_order(self):
        bus = EventBus(queue_size=8)
        subscription = bus.subscribe()
        for index in range(4):
            bus.publish("workspace", progress=index)
        await asyncio.sleep(0)

        received = [subscription.queue.get_nowait()["progress"] for _ in range(4)]
        self.assertEqual([0, 1, 2, 3], received)
        bus.unsubscribe(subscription.id)

    async def test_a_subscriber_on_a_closed_loop_is_dropped(self):
        """죽은 구독자를 남겨두면 매 publish마다 다시 시도된다."""
        bus = EventBus()
        dead_loop = asyncio.new_event_loop()
        queue: asyncio.Queue = asyncio.Queue()
        with bus._lock:
            bus._next_id += 1
            stale_id = bus._next_id
            bus._subscribers[stale_id] = (dead_loop, queue)
        dead_loop.close()

        live = bus.subscribe()
        bus.publish("workspace", progress=1)
        await asyncio.sleep(0)

        self.assertNotIn(stale_id, bus._subscribers)
        self.assertEqual(1, live.queue.get_nowait()["progress"])
        bus.unsubscribe(live.id)


if __name__ == "__main__":
    unittest.main()
