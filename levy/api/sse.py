"""Server-Sent Events endpoint helpers.

Streams named events (belief, evidence, job, lesson, resolution) with periodic
heartbeats. Subscribers are cleaned up on disconnect.
"""

from __future__ import annotations

import asyncio
import json
from typing import AsyncIterator

from levy.core.events import EventBus

HEARTBEAT_SECONDS = 15


async def event_stream(bus: EventBus, replay_last: int = 20) -> AsyncIterator[dict]:
    queue = await bus.subscribe(replay_last=replay_last)
    try:
        # Initial hello for immediate connection feedback.
        yield {"event": "hello", "data": json.dumps({"subscribers": bus.subscriber_count})}
        while True:
            try:
                evt = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
                yield {
                    "event": evt.event,
                    "id": str(evt.seq),
                    "data": json.dumps(evt.data, default=str),
                }
            except asyncio.TimeoutError:
                yield {"event": "heartbeat", "data": json.dumps({"ts": "keepalive"})}
    finally:
        await bus.unsubscribe(queue)
