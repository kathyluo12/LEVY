"""Idempotent seed CLI + programmatic seeding.

Seeds questions (from questions.yaml), reference events (events.csv), exposure
fixtures and market ticks. Safe to run repeatedly: unique keys prevent
duplicates and existing rows are skipped.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

from levy.agents.exposure import COUNTRY_EXPOSURE
from levy.core.collections import EVENTS, EXPOSURES, MARKET_TICKS, QUESTIONS
from levy.core.db import DuplicateKeyError, Repository, make_repository
from levy.schemas import Event, Exposure, MarketTick, Question, QuestionStatus

DATA_DIR = Path(__file__).resolve().parent / "data"

# Canned "breaking news" document for /api/demo/inject.
CANNED_INJECT: dict[str, Any] = {
    "source": "ustr",
    "title": "USTR notice: new Section 301 tariff proposed on China semiconductors",
    "text": (
        "The U.S. Trade Representative proposes new Section 301 tariffs on Chinese "
        "semiconductor imports, an escalation with a named rate and effective date."
    ),
    "url": "https://ustr.gov/breaking-inject",
    "published_at": "2026-09-26T12:00:00Z",
}


def _coerce_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


async def seed_questions(repo: Repository) -> int:
    path = DATA_DIR / "questions.yaml"
    with path.open() as f:
        rows = yaml.safe_load(f) or []
    n = 0
    for row in rows:
        if await repo.find_one(QUESTIONS, {"key": row["key"]}):
            continue
        q = Question(
            key=row["key"],
            text=row["text"],
            country=row.get("country", ""),
            authority=row.get("authority", "other"),
            resolution_source=row.get("resolution_source", ""),
            outcomes=row.get("outcomes", ["YES", "NO"]),
            status=row.get("status", QuestionStatus.ACTIVE.value),
        )
        try:
            await repo.insert(QUESTIONS, q.to_doc())
            n += 1
        except DuplicateKeyError:
            continue
    return n


async def seed_events(repo: Repository) -> int:
    path = DATA_DIR / "events.csv"
    n = 0
    with path.open() as f:
        reader = csv.DictReader(f)
        for row in reader:
            date = _coerce_dt(row["date"])
            existing = await repo.find_one(
                EVENTS, {"country": row["country"], "action": row["action"]}
            )
            if existing:
                continue
            ev = Event(
                date=date,
                country=row["country"],
                authority=row["authority"],
                action=row["action"],
                threatened_at=_coerce_dt(row["threatened_at"])
                if row.get("threatened_at")
                else None,
                implemented=str(row.get("implemented", "")).lower() == "true",
                delay_days=int(row.get("delay_days", 0) or 0),
            )
            await repo.insert(EVENTS, ev.to_doc())
            n += 1
    return n


async def seed_exposures(repo: Repository) -> int:
    n = 0
    for country, base in COUNTRY_EXPOSURE.items():
        if await repo.find_one(EXPOSURES, {"country": country}):
            continue
        exp = Exposure(
            country=country,
            sectors=base["sectors"],
            fx=base["fx"],
            tickers=base["tickers"],
        )
        await repo.insert(EXPOSURES, exp.to_doc())
        n += 1
    return n


async def seed_market_ticks(repo: Repository) -> int:
    ticks = [
        ("CHN-DEAL-TRUCE-EXT", "kalshi", 0.62),
        ("CAN-S338-REMOVE", "polymarket", 0.30),
        ("CHN-S301-SEMI", "kalshi", 0.45),
    ]
    n = 0
    for key, source, value in ticks:
        existing = await repo.find_one(MARKET_TICKS, {"meta.question_key": key})
        if existing:
            continue
        tick = MarketTick(
            meta={"source": source, "question_key": key, "instrument": key}, value=value
        )
        await repo.insert(MARKET_TICKS, tick.to_doc())
        n += 1
    return n


async def seed_repository(repo: Repository) -> dict[str, int]:
    return {
        "questions": await seed_questions(repo),
        "events": await seed_events(repo),
        "exposures": await seed_exposures(repo),
        "market_ticks": await seed_market_ticks(repo),
    }


async def _run() -> None:
    repo = make_repository()
    await repo.bootstrap()
    result = await seed_repository(repo)
    await repo.close()
    print("Seed complete:", result)


def main() -> None:
    parser = argparse.ArgumentParser(description="LEVY idempotent seed CLI")
    parser.parse_args()
    asyncio.run(_run())


if __name__ == "__main__":
    main()
