"""Scouts: roster + scheduler metadata + ingestion of raw items.

Scouts are stateless workers that write ``raw_items``. In offline mode they
ingest from provided document lists (used by replay and seed). The roster and
cadences drive APScheduler in the worker process.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from levy.core.collections import RAW_ITEMS
from levy.core.db import DuplicateKeyError, Repository
from levy.schemas import RawItem


@dataclass(frozen=True)
class ScoutSpec:
    name: str
    cadence_seconds: int
    sources: tuple[str, ...]
    writes: str = RAW_ITEMS
    uses_model: bool = False


SCOUT_ROSTER: list[ScoutSpec] = [
    ScoutSpec("register_scout", 15 * 60, ("federal_register", "ustr", "cbp")),
    ScoutSpec("principal_scout", 5 * 60, ("official_posts", "transcripts")),
    ScoutSpec("courts_scout", 60 * 60, ("courtlistener",)),
    ScoutSpec("hill_scout", 24 * 60 * 60, ("congress",)),
    ScoutSpec(
        "counterparty_scout", 60 * 60, ("mofcom", "canada_finance", "eu_dg_trade"), uses_model=True
    ),
    ScoutSpec(
        "market_scout", 10 * 60, ("kalshi", "polymarket", "fred", "fx"), writes="market_ticks"
    ),
]

SCOUT_BY_NAME = {s.name: s for s in SCOUT_ROSTER}


def content_hash(source: str, url: str, title: str, text: str) -> str:
    return hashlib.sha256(f"{source}|{url}|{title}|{text}".encode()).hexdigest()


class ScoutService:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def ingest(self, doc: dict[str, Any]) -> Optional[RawItem]:
        """Ingest a single document into raw_items, deduped by content hash."""
        source = doc.get("source", "unknown")
        url = doc.get("url", "")
        title = doc.get("title", "")
        text = doc.get("text", "")
        h = doc.get("hash") or content_hash(source, url, title, text)

        published = doc.get("published_at")
        item = RawItem(
            source=source,
            url=url,
            title=title,
            text=text,
            hash=h,
            published_at=_coerce_dt(published),
            fetched_at=datetime.now(timezone.utc),
        )
        try:
            await self.repo.insert(RAW_ITEMS, item.to_doc())
        except DuplicateKeyError:
            return None
        return item

    async def ingest_many(self, docs: list[dict[str, Any]]) -> list[RawItem]:
        out = []
        for d in docs:
            item = await self.ingest(d)
            if item is not None:
                out.append(item)
        return out


def _coerce_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
