"""Replay clock, document cutoff, prompt cutoff metadata, and leak probe.

A :class:`SimClock` replaces ``now()`` during replay so scouts only see
documents whose ``published_at <= sim_now``. Replay writes to an isolated
database name ``levy_replay_<scenario>``. The leak probe checks a rationale for
references to events after the simulated date.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any, Optional


class SimClock:
    """Simulated clock. Advances ``step`` per ``tick`` (default 1 day)."""

    def __init__(self, start: datetime, step: timedelta = timedelta(days=1)) -> None:
        self._now = _as_utc(start)
        self.step = step

    def now(self) -> datetime:
        return self._now

    def advance(self, by: Optional[timedelta] = None) -> datetime:
        self._now = self._now + (by or self.step)
        return self._now

    def set(self, when: datetime) -> None:
        self._now = _as_utc(when)


def replay_db_name(scenario: str, base: str = "levy") -> str:
    safe = re.sub(r"[^a-z0-9_]+", "_", scenario.lower())
    return f"{base}_replay_{safe}"


def document_visible(published_at: Any, sim_now: datetime) -> bool:
    """A document is visible only if it was published at or before sim_now."""
    dt = _as_utc(_coerce_dt(published_at))
    return dt <= _as_utc(sim_now)


def filter_visible(docs: list[dict[str, Any]], sim_now: datetime) -> list[dict[str, Any]]:
    return [d for d in docs if document_visible(d.get("published_at"), sim_now)]


def prompt_cutoff_note(sim_now: datetime) -> str:
    """Prompt preamble stating the simulated date; forbids later knowledge."""
    date_str = _as_utc(sim_now).date().isoformat()
    return (
        f"SIMULATED DATE: {date_str}. You are reasoning as of this date only. "
        f"Do NOT use any knowledge of events after {date_str}. Base your answer "
        f"solely on the provided evidence dated on or before this date."
    )


# Month names + ISO date patterns used by the leak probe.
_MONTHS = {
    "january": 1,
    "february": 2,
    "march": 3,
    "april": 4,
    "may": 5,
    "june": 6,
    "july": 7,
    "august": 8,
    "september": 9,
    "october": 10,
    "november": 11,
    "december": 12,
}
_ISO_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_MONTH_RE = re.compile(r"\b(" + "|".join(_MONTHS) + r")\s+(\d{1,2})?,?\s*(\d{4})\b", re.IGNORECASE)


def leak_probe(rationale: str, sim_now: datetime) -> dict[str, Any]:
    """Detect references to dates after the simulated date in free text.

    This is a deterministic offline stand-in for the Jev leak-probe noul
    question. Returns ``{"leak": bool, "hits": [iso_date, ...]}``.
    """
    sim = _as_utc(sim_now).date()
    hits: list[str] = []

    for m in _ISO_RE.finditer(rationale or ""):
        try:
            d = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3))).date()
            if d > sim:
                hits.append(d.isoformat())
        except ValueError:
            continue

    for m in _MONTH_RE.finditer(rationale or ""):
        month = _MONTHS[m.group(1).lower()]
        day = int(m.group(2)) if m.group(2) else 1
        year = int(m.group(3))
        try:
            d = datetime(year, month, day).date()
            if d > sim:
                hits.append(d.isoformat())
        except ValueError:
            continue

    return {"leak": len(hits) > 0, "hits": sorted(set(hits))}


def _coerce_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
