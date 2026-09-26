"""Register service: fetch + process Federal Register documents.

Ties :class:`FederalRegisterClient` to the ingestion pipeline. Supports three
modes:

* ``fetch_only``  — fetch + prefilter only, no DB writes.
* ``ingest_only`` — write ``raw_items`` (deduped) but do not triage/forecast.
* full pipeline   — ingest, triage and forecast via :class:`Pipeline`.

Every run returns a structured :class:`RegisterRunSummary` for observability and
logs a structured summary line. Runs are bounded by ``max_documents``.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from levy.agents.pipeline import Pipeline
from levy.agents.scouts import ScoutService
from levy.core.db import Repository
from levy.core.events import EventBus
from levy.core.federal_register import (
    DEFAULT_MAX_DOCUMENTS,
    DEFAULT_MAX_PAGES,
    DEFAULT_PER_PAGE,
    FederalRegisterClient,
)

log = logging.getLogger("levy.register")


@dataclass
class RegisterRunSummary:
    start: str
    end: str
    query: str
    mode: str
    fetched: int = 0
    prefiltered: int = 0  # documents passing prefilter (== len(docs) returned)
    inserted: int = 0
    resumed: int = 0  # duplicate raw hash that resumed prior partial processing
    duplicates: int = 0  # duplicate raw hash that was already complete / no-op
    accepted: int = 0
    rejected: int = 0
    forecasts: int = 0
    beliefs: int = 0
    pages: int = 0
    request_count: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class RegisterService:
    def __init__(
        self,
        repo: Repository,
        *,
        settings: Any = None,
        bus: Optional[EventBus] = None,
        client: Optional[FederalRegisterClient] = None,
    ) -> None:
        self.repo = repo
        self.settings = settings
        self.bus = bus
        self.client = client or FederalRegisterClient(settings=settings)

    async def run(
        self,
        *,
        start: date,
        end: date,
        query: Optional[str] = None,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_documents: int = DEFAULT_MAX_DOCUMENTS,
        include_full_text: bool = False,
        fetch_only: bool = False,
        ingest_only: bool = False,
    ) -> RegisterRunSummary:
        query = query or (self.settings.federal_register_query if self.settings else "tariff")
        mode = "fetch_only" if fetch_only else ("ingest_only" if ingest_only else "pipeline")
        summary = RegisterRunSummary(
            start=start.isoformat(), end=end.isoformat(), query=query, mode=mode
        )

        self.client.request_count = 0
        self.client.pages_fetched = 0
        try:
            docs = await self.client.fetch_documents(
                start,
                end,
                query,
                per_page=per_page,
                max_pages=max_pages,
                max_documents=max_documents,
                include_full_text=include_full_text,
            )
        except Exception as exc:  # noqa: BLE001 — record and return, never crash caller
            summary.errors.append(str(exc))
            log.error("register fetch failed: %s", exc)
            return summary

        summary.fetched = len(docs)
        summary.prefiltered = len(docs)
        summary.request_count = self.client.request_count
        summary.pages = self.client.pages_fetched

        if fetch_only:
            log.info("register run (fetch_only): %s", summary.to_dict())
            return summary

        if ingest_only:
            await self._ingest_only(docs, summary)
            log.info("register run (ingest_only): %s", summary.to_dict())
            return summary

        await self._full_pipeline(docs, summary)
        log.info("register run (pipeline): %s", summary.to_dict())
        return summary

    async def _ingest_only(self, docs: list[dict[str, Any]], summary: RegisterRunSummary) -> None:
        scouts = ScoutService(self.repo)
        for doc in docs:
            try:
                item = await scouts.ingest(doc)
            except Exception as exc:  # noqa: BLE001
                summary.errors.append(str(exc))
                continue
            if item is None:
                summary.duplicates += 1
            else:
                summary.inserted += 1

    async def _full_pipeline(self, docs: list[dict[str, Any]], summary: RegisterRunSummary) -> None:
        pipeline = Pipeline(self.repo, bus=self.bus, settings=self.settings)
        for doc in docs:
            try:
                result = await pipeline.process_document(doc)
            except Exception as exc:  # noqa: BLE001
                # Unexpected failure *before* the pipeline could report a
                # result (e.g. triage crashed before any write). Record the
                # error; do not fabricate an insertion we cannot confirm.
                summary.errors.append(str(exc))
                log.exception("register pipeline failed for %s", doc.get("url"))
                continue

            if not result.get("ingested"):
                # True no-op duplicate: raw hash existed and there was nothing
                # to resume (already complete or unresumable).
                summary.duplicates += 1
                continue

            # The document produced (or already had) durable state. Count it as
            # a fresh insertion or a resumed one so a partial write is never
            # reported as "inserted=0".
            if result.get("resumed"):
                if result.get("complete"):
                    # Resumed but nothing left to do -> already-complete dup.
                    summary.duplicates += 1
                else:
                    summary.resumed += 1
            else:
                summary.inserted += 1

            # A downstream forecast error after insertion must still surface.
            if result.get("error"):
                summary.errors.append(str(result["error"]))

            if result.get("accepted"):
                summary.accepted += 1
                beliefs = result.get("beliefs") or []
                summary.beliefs += len(beliefs)
                if beliefs:
                    summary.forecasts += 1
            else:
                summary.rejected += 1


def resolve_window(
    *,
    start: Optional[date],
    end: Optional[date],
    days: Optional[int],
    now: Optional[datetime] = None,
) -> tuple[date, date]:
    """Resolve a date window from mutually-exclusive inputs.

    Raises ``ValueError`` if conflicting arguments are supplied.
    """
    today = (now or datetime.now(timezone.utc)).date()

    if days is not None and (start is not None or end is not None):
        raise ValueError("--days cannot be combined with --start/--end")
    if days is not None:
        if days < 1:
            raise ValueError("--days must be >= 1")
        return today - timedelta(days=days), today
    if start is not None and end is not None:
        if end < start:
            raise ValueError("--end must be on or after --start")
        return start, end
    if start is not None:
        return start, today
    if end is not None:
        raise ValueError("--end requires --start (or use --days)")
    # Default: a small recent window.
    return today - timedelta(days=7), today
