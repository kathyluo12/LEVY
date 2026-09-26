"""test_jobs: two workers never process the same job key; leases + idempotency."""

from __future__ import annotations

import asyncio

import pytest

from levy.core.jobs import JobQueue, idempotency_key


def test_idempotency_key_is_order_independent():
    a = idempotency_key("forecast", "Q1", ["e2", "e1", "e3"])
    b = idempotency_key("forecast", "Q1", ["e1", "e2", "e3"])
    assert a == b
    c = idempotency_key("forecast", "Q1", ["e1", "e2"])
    assert a != c


@pytest.mark.asyncio
async def test_enqueue_is_idempotent(repo):
    q = JobQueue(repo)
    j1 = await q.enqueue("forecast", "Q1", ["e1", "e2"])
    j2 = await q.enqueue("forecast", "Q1", ["e2", "e1"])
    assert j1.key == j2.key
    count = await repo.count("jobs", {"key": j1.key})
    assert count == 1


@pytest.mark.asyncio
async def test_two_workers_never_share_a_job(repo):
    q = JobQueue(repo)
    # enqueue many distinct jobs
    for i in range(50):
        await q.enqueue("forecast", f"Q{i}", [f"e{i}"])

    claimed_by: dict[str, str] = {}
    conflicts: list[str] = []

    async def worker(name: str):
        while True:
            job = await q.claim(name, job_type="forecast")
            if job is None:
                # nothing left to claim right now
                if await q.depth() == 0:
                    return
                await asyncio.sleep(0)
                continue
            if job.key in claimed_by:
                conflicts.append(job.key)
            else:
                claimed_by[job.key] = name
            await q.complete(job.key)

    await asyncio.gather(*[worker(f"w{i}") for i in range(4)])
    assert conflicts == []
    assert len(claimed_by) == 50


@pytest.mark.asyncio
async def test_lease_expiry_allows_retry(repo):
    q = JobQueue(repo, lease_seconds=-1)  # already-expired leases
    await q.enqueue("forecast", "QX", ["e1"])
    j1 = await q.claim("w1", job_type="forecast")
    assert j1 is not None
    # lease is immediately expired -> another worker can reclaim
    j2 = await q.claim("w2", job_type="forecast")
    assert j2 is not None
    assert j2.key == j1.key
