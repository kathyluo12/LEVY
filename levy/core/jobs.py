"""Leased job queue with atomic claims and deterministic idempotency keys.

* Idempotency key = ``type + question_id + sha(sorted evidence_ids)``.
* Claims are atomic via ``find_one_and_update`` (works in both repos).
* Lease expiry lets a crashed worker's job be retried by another.
* 10-minute debounce: enqueuing the same key within the window joins the
  existing pending/leased job rather than creating a duplicate.
* Budget checks are delegated to the caller via a provided predicate.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from levy.core.collections import JOBS
from levy.core.db import DuplicateKeyError, Repository
from levy.schemas import Job, JobStatus

LEASE_SECONDS = 300
DEBOUNCE = timedelta(minutes=10)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def idempotency_key(job_type: str, question_id: str, evidence_ids: list[str]) -> str:
    joined = ",".join(sorted(evidence_ids or []))
    digest = hashlib.sha256(joined.encode()).hexdigest()[:16]
    return f"{job_type}:{question_id}:{digest}"


def _parse_dt(v: Any) -> Optional[datetime]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


class JobQueue:
    def __init__(self, repo: Repository, *, lease_seconds: int = LEASE_SECONDS) -> None:
        self.repo = repo
        self.lease_seconds = lease_seconds

    async def enqueue(
        self,
        job_type: str,
        question_id: str,
        evidence_ids: Optional[list[str]] = None,
        *,
        payload: Optional[dict] = None,
        debounce: bool = True,
    ) -> Job:
        key = idempotency_key(job_type, question_id, evidence_ids or [])
        existing = await self.repo.find_one(JOBS, {"key": key})
        if existing is not None:
            if debounce and existing.get("status") in (
                JobStatus.PENDING.value,
                JobStatus.LEASED.value,
            ):
                created = _parse_dt(existing.get("created_at"))
                if created and (_now() - created) < DEBOUNCE:
                    return Job(**existing)
            # Reopen a done/failed job so new evidence forces a fresh run.
            reopened = await self.repo.update_one(
                JOBS,
                {"key": key},
                {"$set": {"status": JobStatus.PENDING.value, "updated_at": _now().isoformat()}},
            )
            return Job(**reopened) if reopened else Job(**existing)

        job = Job(
            type=job_type,
            key=key,
            payload={
                **(payload or {}),
                "question_id": question_id,
                "evidence_ids": evidence_ids or [],
            },
        )
        try:
            await self.repo.insert(JOBS, job.to_doc())
        except DuplicateKeyError:
            existing = await self.repo.find_one(JOBS, {"key": key})
            return Job(**existing) if existing else job
        return job

    async def claim(self, worker: str, *, job_type: Optional[str] = None) -> Optional[Job]:
        """Atomically claim one available job (pending or lease-expired)."""
        now = _now()
        lease_until = (now + timedelta(seconds=self.lease_seconds)).isoformat()

        candidates = await self.repo.find(JOBS, {}, sort=[("created_at", 1)])
        for c in candidates:
            if job_type and c.get("type") != job_type:
                continue
            status = c.get("status")
            claimable = status == JobStatus.PENDING.value
            if status == JobStatus.LEASED.value:
                exp = _parse_dt(c.get("lease_until"))
                if exp and exp < now:
                    claimable = True
            if not claimable:
                continue

            # Atomic compare-and-set on the exact status we observed.
            updated = await self.repo.find_one_and_update(
                JOBS,
                {"key": c["key"], "status": status},
                {
                    "$set": {
                        "status": JobStatus.LEASED.value,
                        "lease_until": lease_until,
                        "worker": worker,
                        "updated_at": now.isoformat(),
                    },
                    "$inc": {"attempts": 1},
                },
            )
            if updated is not None:
                return Job(**updated)
        return None

    async def complete(self, key: str) -> None:
        await self.repo.update_one(
            JOBS,
            {"key": key},
            {"$set": {"status": JobStatus.DONE.value, "updated_at": _now().isoformat()}},
        )

    async def fail(self, key: str) -> None:
        job = await self.repo.find_one(JOBS, {"key": key})
        if not job:
            return
        attempts = job.get("attempts", 0)
        max_attempts = job.get("max_attempts", 3)
        status = JobStatus.PENDING.value if attempts < max_attempts else JobStatus.FAILED.value
        await self.repo.update_one(
            JOBS,
            {"key": key},
            {"$set": {"status": status, "lease_until": None, "updated_at": _now().isoformat()}},
        )

    async def depth(self) -> int:
        return await self.repo.count(JOBS, {"status": JobStatus.PENDING.value})
