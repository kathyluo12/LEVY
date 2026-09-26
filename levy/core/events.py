"""In-process event bus for SSE fan-out.

Works fully offline. Publishers push :class:`StreamEvent`s; subscribers get an
``asyncio.Queue``. Subscribers are cleaned up on unsubscribe. Also persists
events to the ``events``-like ``stream`` history is kept short in-memory for the
``/api/stream`` replay-on-connect behaviour.
"""

from __future__ import annotations

import asyncio
from typing import Any, Optional

from levy.schemas import StreamEvent

EVENT_NAMES = ("belief", "evidence", "job", "lesson", "resolution")


class EventBus:
    def __init__(self, history: int = 200) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._seq = 0
        self._history: list[StreamEvent] = []
        self._history_max = history
        self._lock = asyncio.Lock()

    async def publish(self, event: str, data: dict[str, Any]) -> StreamEvent:
        async with self._lock:
            self._seq += 1
            evt = StreamEvent(event=event, data=data, seq=self._seq)
            self._history.append(evt)
            if len(self._history) > self._history_max:
                self._history = self._history[-self._history_max :]
            subscribers = list(self._subscribers)
        for q in subscribers:
            try:
                q.put_nowait(evt)
            except asyncio.QueueFull:
                pass
        return evt

    async def subscribe(self, replay_last: int = 0) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=1000)
        async with self._lock:
            self._subscribers.add(q)
            if replay_last:
                for evt in self._history[-replay_last:]:
                    try:
                        q.put_nowait(evt)
                    except asyncio.QueueFull:
                        break
        return q

    async def unsubscribe(self, q: asyncio.Queue) -> None:
        async with self._lock:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


_bus_singleton: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    global _bus_singleton
    if _bus_singleton is None:
        _bus_singleton = EventBus()
    return _bus_singleton


def set_event_bus(bus: Optional[EventBus]) -> None:
    global _bus_singleton
    _bus_singleton = bus
