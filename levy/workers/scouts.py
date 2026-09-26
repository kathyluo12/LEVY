"""Scouts worker: APScheduler runs scouts on their cadences.

In production (``LEVY_OFFLINE=false``) the ``register_scout`` tick runs the real
:class:`RegisterService` against the live Federal Register API over a small
lookback window. All other scout ticks remain heartbeat-only until their source
adapters are implemented. In offline mode every tick — including
``register_scout`` — logs a heartbeat and makes no network calls.

The scheduled register job is guarded against overlapping runs with an
``asyncio.Lock`` and APScheduler's ``max_instances=1``. Repo/settings/bus are
passed explicitly into the job via a bound method (no module globals).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone

from levy.agents.register import RegisterService
from levy.agents.scouts import SCOUT_ROSTER
from levy.core.db import Repository, make_repository
from levy.core.events import EventBus, get_event_bus
from levy.settings import Settings, get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("levy.scouts")

REGISTER_SCOUT = "register_scout"


class RegisterScoutJob:
    """Bound scheduled job that runs the register ingestion without overlap."""

    def __init__(self, repo: Repository, settings: Settings, bus: EventBus) -> None:
        self.repo = repo
        self.settings = settings
        self.bus = bus
        self._lock = asyncio.Lock()

    async def tick(self) -> None:
        if self.settings.offline:
            # Offline: no network, just a heartbeat.
            log.info("scout tick (offline heartbeat): %s", REGISTER_SCOUT)
            return
        if self._lock.locked():
            log.info("register_scout run already in progress; skipping this tick")
            return
        async with self._lock:
            await self._run_once()

    async def _run_once(self) -> None:
        end = datetime.now(timezone.utc).date()
        start = end - timedelta(days=self.settings.register_scout_lookback_days)
        service = RegisterService(self.repo, settings=self.settings, bus=self.bus)
        summary = await service.run(
            start=start,
            end=end,
            max_documents=self.settings.register_scout_max_documents,
            max_pages=3,
        )
        if summary.errors:
            log.error("register_scout run errors: %s", summary.errors)
        log.info("register_scout run summary: %s", summary.to_dict())


async def _scout_tick(name: str) -> None:
    """Heartbeat tick for scouts other than register_scout."""
    log.info("scout tick: %s", name)


async def _run() -> None:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler

    settings = get_settings()
    repo = make_repository(settings)
    await repo.bootstrap()
    bus = get_event_bus()
    register_job = RegisterScoutJob(repo, settings, bus)

    scheduler = AsyncIOScheduler()
    for spec in SCOUT_ROSTER:
        if spec.name == REGISTER_SCOUT:
            scheduler.add_job(
                register_job.tick,
                "interval",
                seconds=spec.cadence_seconds,
                id=spec.name,
                max_instances=1,
            )
        else:
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

    # Optionally run the register scout immediately at startup.
    if settings.register_scout_run_on_start:
        try:
            await register_job.tick()
        except Exception:  # noqa: BLE001
            log.exception("register_scout run-on-start failed")

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        scheduler.shutdown()
        await repo.close()


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
