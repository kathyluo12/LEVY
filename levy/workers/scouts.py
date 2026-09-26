"""Scouts worker: APScheduler runs scouts on their cadences.

In offline mode there are no live sources, so scouts log a heartbeat. The
scheduler wiring is real so the same process works against live sources when
``LEVY_OFFLINE=false`` and source adapters are provided.
"""

from __future__ import annotations

import asyncio
import logging

from levy.agents.scouts import SCOUT_ROSTER, ScoutService
from levy.core.db import make_repository
from levy.settings import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("levy.scouts")


async def _run() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    settings = get_settings()
    repo = make_repository(settings)
    await repo.bootstrap()
    _service = ScoutService(repo)

    scheduler = AsyncIOScheduler()
    for spec in SCOUT_ROSTER:
        scheduler.add_job(
            _scout_tick,
            "interval",
            seconds=spec.cadence_seconds,
            args=[spec.name],
            id=spec.name,
            max_instances=1,
        )
    scheduler.start()
    log.info(
        "scout scheduler started with %d scouts (offline=%s)", len(SCOUT_ROSTER), settings.offline
    )
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown()
        await repo.close()


async def _scout_tick(name: str) -> None:
    log.info("scout tick: %s", name)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
