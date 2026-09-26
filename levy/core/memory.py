"""Memory: deterministic embeddings, lesson recall, and event reference classes.

In offline mode we generate small deterministic embeddings from text so vector
recall behaves consistently without external embedding services. Recall pattern
mirrors the TDD: rank by cosine similarity, keep top-K (Jev relevance filtering
happens in the calling service).
"""

from __future__ import annotations

import hashlib
import math
from datetime import datetime, timezone
from typing import Any, Optional

from levy.core.collections import EVENTS, LESSONS

EMBED_DIM = 256


def embed(text: str, dim: int = EMBED_DIM) -> list[float]:
    """Deterministic bag-of-hashed-tokens embedding (unit-normalized)."""
    vec = [0.0] * dim
    for tok in _tokens(text):
        h = int(hashlib.sha256(tok.encode()).hexdigest(), 16)
        idx = h % dim
        sign = 1.0 if (h >> 8) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _tokens(text: str) -> list[str]:
    return [t for t in "".join(c.lower() if c.isalnum() else " " for c in text).split() if t]


def cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    return sum(x * y for x, y in zip(a, b))  # both unit-normalized


async def recall_lessons(
    repo, question_text: str, *, authority: Optional[str] = None, k: int = 5
) -> list[dict[str, Any]]:
    """Vector-recall top-K active, non-expired lessons for a question."""
    flt: dict[str, Any] = {"status": "active"}
    lessons = await repo.find(LESSONS, flt)
    now = datetime.now(timezone.utc)
    qvec = embed(question_text)
    scored = []
    for lesson in lessons:
        expires = lesson.get("expires_at")
        if expires and _as_dt(expires) < now:
            continue
        if authority:
            applies = lesson.get("applies_to", {}) or {}
            if applies.get("authority") and applies.get("authority") != authority:
                continue
        emb = lesson.get("embedding") or embed(lesson.get("text", ""))
        sim = cosine(qvec, emb)
        # rank by similarity blended with past helpfulness
        rank = sim + 0.1 * lesson.get("helpfulness", 0.0)
        scored.append((rank, lesson))
    scored.sort(key=lambda t: t[0], reverse=True)
    return [ls for _, ls in scored[:k]]


async def reference_class(
    repo, *, country: Optional[str] = None, authority: Optional[str] = None
) -> list[dict[str, Any]]:
    flt: dict[str, Any] = {}
    if country:
        flt["country"] = country
    if authority:
        flt["authority"] = authority
    return await repo.find(EVENTS, flt)


def base_rate(events: list[dict[str, Any]]) -> float:
    if not events:
        return 0.5
    implemented = sum(1 for e in events if e.get("implemented"))
    return implemented / len(events)


def _as_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
