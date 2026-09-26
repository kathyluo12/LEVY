"""Agents worker: claims leased jobs and runs the forecast pipeline.

Multiple instances can run concurrently; atomic leases guarantee no two workers
process the same job key. In offline mode this loop is fully functional against
the in-memory repository.
"""

from __future__ import annotations

import asyncio
import logging
import os

from levy.agents.pipeline import Pipeline
from levy.core.collections import QUESTIONS
from levy.core.db import make_repository
from levy.core.events import get_event_bus
from levy.core.jobs import JobQueue
from levy.settings import get_settings

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("levy.agents")

POLL_SECONDS = 1.0


async def _run() -> None:
    settings = get_settings()
    repo = make_repository(settings)
    await repo.bootstrap()
    bus = get_event_bus()
    queue = JobQueue(repo)
    pipeline = Pipeline(repo, bus=bus, settings=settings)
    worker_id = f"agents-{os.getpid()}"
    log.info("agents worker %s started (offline=%s)", worker_id, settings.offline)

    while True:
        job = await queue.claim(worker_id, job_type="forecast")
        if job is None:
            await asyncio.sleep(POLL_SECONDS)
            continue
        try:
            qid = job.payload.get("question_id")
            question = await repo.find_one(QUESTIONS, {"_id": qid})
            if question:
                await pipeline.run_forecast(question, trigger={"type": "job", "id": job.key})
            await queue.complete(job.key)
            log.info("job done: %s", job.key)
        except Exception:  # noqa: BLE001
            log.exception("job failed: %s", job.key)
            await queue.fail(job.key)


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
