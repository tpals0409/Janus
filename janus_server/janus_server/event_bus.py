"""Thread-safe, bounded invalidation stream for the Janus renderer."""

from __future__ import annotations

import asyncio
import threading
from contextlib import suppress
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Subscription:
    id: int
    queue: asyncio.Queue[dict[str, Any]]


class EventBus:
    """Fan out small domain events without making background jobs await the UI."""

    def __init__(self, *, queue_size: int = 128):
        if queue_size < 1:
            raise ValueError("queue_size must be positive")
        self.queue_size = queue_size
        self._lock = threading.Lock()
        self._next_id = 0
        self._sequence = 0
        self._subscribers: dict[int, tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = {}

    def subscribe(self) -> Subscription:
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=self.queue_size)
        with self._lock:
            self._next_id += 1
            subscription_id = self._next_id
            self._subscribers[subscription_id] = (loop, queue)
        return Subscription(subscription_id, queue)

    def unsubscribe(self, subscription_id: int) -> None:
        with self._lock:
            self._subscribers.pop(subscription_id, None)

    def publish(self, topic: str, event: str = "changed", **payload: Any) -> dict[str, Any]:
        if not topic:
            raise ValueError("topic is required")
        with self._lock:
            self._sequence += 1
            message = {
                "topic": topic,
                "event": event,
                "sequence": self._sequence,
                **payload,
            }
            subscribers = list(self._subscribers.items())
        dead: list[int] = []
        for subscription_id, (loop, queue) in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, message)
            except RuntimeError:
                # 루프가 닫힌 구독자다. 남겨두면 매 publish마다 다시 시도된다.
                dead.append(subscription_id)
        for subscription_id in dead:
            self.unsubscribe(subscription_id)
        return message

    @staticmethod
    def _offer(queue: asyncio.Queue, message: dict[str, Any]) -> None:
        if not queue.full():
            with suppress(asyncio.QueueFull):
                queue.put_nowait(message)
            return
        # 느린 구독자다. 조용히 오래된 것부터 버리면 UI가 무효화를 놓친 채
        # 낡은 화면을 계속 보여준다 — 실패 모드가 "재연결"이 아니라 "화면이 틀림"이다.
        # 큐를 비우고 resync 지시 하나만 남겨 클라이언트가 다시 읽게 한다.
        dropped = 0
        while True:
            try:
                queue.get_nowait()
                dropped += 1
            except asyncio.QueueEmpty:
                break
        with suppress(asyncio.QueueFull):
            queue.put_nowait({
                "topic": "system", "event": "resync",
                "dropped": dropped, "sequence": message.get("sequence"),
            })
