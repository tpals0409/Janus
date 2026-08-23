"""Thread-safe, bounded invalidation stream for the Janus renderer."""

from __future__ import annotations

import asyncio
import threading
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
            subscribers = list(self._subscribers.values())
        for loop, queue in subscribers:
            try:
                loop.call_soon_threadsafe(self._offer, queue, message)
            except RuntimeError:
                continue
        return message

    @staticmethod
    def _offer(queue: asyncio.Queue, message: dict[str, Any]) -> None:
        if queue.full():
            try:
                queue.get_nowait()
            except asyncio.QueueEmpty:
                pass
        try:
            queue.put_nowait(message)
        except asyncio.QueueFull:
            pass
