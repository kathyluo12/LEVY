"""Atlas-backed UI integration API: ``GET /v1/snapshot``.

Assembles the exact frontend ``LevySnapshot`` contract consumed by
``LEVY_UI/src/lib/levy-source.server.ts`` from the Atlas-backed repository:

* ``questions`` — Atlas ``questions`` joined with the latest append-only
  ``beliefs`` version and their linked ``evidence`` / ``raw_items`` rows,
  emitted in camelCase with a confidence grade in ``A``–``D``.
* ``replay`` — the Canada Section 338 replay fixture (deterministic timeline).
* ``calibration`` — the active ``calibration_maps`` curve, or a neutral curve
  before any data exists.
* ``resolved`` — resolved questions joined with supervisor ``scores``.
* ``brierScore`` — mean supervisor Brier across resolved questions.
* ``stats`` — dynamic desk metrics from ``desk_stats``.

The endpoint lives *outside* the ``/api`` prefix so the frontend can target a
stable public contract. Collections are bulk-loaded once to avoid N+1 access
patterns, and every value is coerced to a JSON-serializable, bounded form so the
response never leaks BSON types or unbounded payloads.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from levy.agents.status import desk_stats
from levy.api.deps import repo_dep
from levy.core.calibrate import model_from_doc, neutral_map
from levy.core.collections import (
    BELIEFS,
    CALIBRATION_MAPS,
    EVIDENCE,
    QUESTIONS,
    RAW_ITEMS,
    RESOLUTIONS,
    SCORES,
)
from levy.core.db import Repository

router = APIRouter()

# Canada Section 338 replay fixture powers the deterministic replay strip.
_REPLAY_FIXTURE = (
    Path(__file__).resolve().parent.parent / "data" / "replay" / "canada_338" / "scenario.json"
)

# ISO country code -> (display market name, flag emoji-code used by the UI).
_COUNTRY_DISPLAY: dict[str, tuple[str, str]] = {
    "CAN": ("Canada", "CA"),
    "CHN": ("China", "CN"),
    "EU": ("European Union", "EU"),
    "MEX": ("Mexico", "MX"),
    "KOR": ("South Korea", "KR"),
    "JPN": ("Japan", "JP"),
    "IND": ("India", "IN"),
    "BRA": ("Brazil", "BR"),
    "USA": ("United States", "US"),
    "GBR": ("United Kingdom", "GB"),
    "GLB": ("Global", "UN"),
}

_MAX_QUESTIONS = 200
_MAX_EVIDENCE_PER_QUESTION = 12
_MAX_FORECAST_POINTS = 24
_MAX_REPLAY_EVENTS = 40
_MAX_RESOLVED = 100


# --- Pydantic response models (documentation + shape guarantees) -----------


class ForecastPointModel(BaseModel):
    label: str
    probability: float
    lower: float
    upper: float


class EvidenceItemModel(BaseModel):
    id: str
    time: str
    source: str
    title: str
    summary: str
    reliability: str  # "High" | "Medium"
    stance: str  # "Raises" | "Lowers" | "Neutral"
    impact: float


class TariffQuestionModel(BaseModel):
    id: str
    key: str
    market: str
    flag: str
    question: str
    shortLabel: str
    probability: float
    change: float
    confidence: str  # "A" | "B" | "C" | "D"
    horizon: str
    status: str  # "Escalating" | "Monitoring" | "Stable"
    reviewed: int
    retained: int
    thesis: str
    forecast: list[ForecastPointModel]
    evidence: list[EvidenceItemModel]


class ReplayEventModel(BaseModel):
    date: str
    kicker: str
    headline: str
    detail: str
    probability: float
    confidence: str
    source: str
    impact: float


class CalibrationPointModel(BaseModel):
    forecast: float
    actual: float


class ResolvedForecastModel(BaseModel):
    event: str
    probability: float
    outcome: str
    score: float


class SnapshotStatsModel(BaseModel):
    itemsScanned: int = 0
    itemsTriaged: int = 0
    filteredByJev: int = 0
    pctFiltered: float = 0.0
    beliefsUpdated: int = 0
    totalCostUsd: float = 0.0
    questions: int = 0
    resolved: int = 0


class LevySnapshotModel(BaseModel):
    source: str = "atlas"
    questions: list[TariffQuestionModel] = Field(default_factory=list)
    replay: list[ReplayEventModel] = Field(default_factory=list)
    calibration: list[CalibrationPointModel] = Field(default_factory=list)
    resolved: list[ResolvedForecastModel] = Field(default_factory=list)
    brierScore: float = 0.0
    stats: Optional[SnapshotStatsModel] = None


# --- Coercion helpers ------------------------------------------------------


def _num(value: Any, default: float = 0.0) -> float:
    """Coerce to a finite float, else ``default``."""
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    if f != f or f in (float("inf"), float("-inf")):  # NaN / inf guard
        return default
    return f


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def _pct(prob01: Any) -> float:
    """A [0,1] probability -> bounded whole-number percentage."""
    return round(_clamp(_num(prob01, 0.5) * 100.0, 0.0, 100.0))


def _grade(belief: Optional[dict[str, Any]]) -> str:
    """Map a belief confidence to an A–D grade.

    Uses the stored grade when present (clamped to A–D), otherwise derives one
    from the confidence score so questions without a graded belief still get an
    honest, bounded value.
    """
    if belief:
        conf = belief.get("confidence") or {}
        grade = conf.get("grade")
        if isinstance(grade, str) and grade.upper() in {"A", "B", "C", "D"}:
            return grade.upper()
        score = _num(conf.get("score"), -1.0)
        if score >= 0:
            if score >= 75:
                return "A"
            if score >= 55:
                return "B"
            if score >= 35:
                return "C"
            return "D"
    return "D"


def _status(question: dict[str, Any], belief: Optional[dict[str, Any]], change: float) -> str:
    """Frontend status: Escalating / Monitoring / Stable.

    Resolved questions and flat/no-belief questions read as Stable; rising
    probability reads as Escalating; everything else is Monitoring.
    """
    if question.get("status") == "resolved" or belief is None:
        return "Stable"
    if change >= 5:
        return "Escalating"
    if change <= -5 or abs(change) < 1:
        return "Stable" if abs(change) < 1 else "Monitoring"
    return "Monitoring"


def _stance(direction: str) -> str:
    d = (direction or "").lower()
    if d in {"escalation"}:
        return "Raises"
    if d in {"de_escalation"}:
        return "Lowers"
    return "Neutral"


def _reliability(materiality: Any) -> str:
    return "High" if _num(materiality) >= 3 else "Medium"


def _evidence_time(published_at: Any) -> str:
    """Render an evidence timestamp as a short ``HH:MM`` label, safely."""
    dt = _parse_dt(published_at)
    if dt is None:
        return "—"
    return dt.astimezone(timezone.utc).strftime("%H:%M")


def _short_label(text: str, country_name: str) -> str:
    base = text.rstrip("?").strip()
    if len(base) <= 42:
        return base
    return f"{country_name} tariff outlook"


def parse_since(since: Optional[str]) -> Optional[datetime]:
    """Parse an ISO-8601 ``since`` value to a timezone-aware UTC datetime.

    Atlas stores BSON datetimes (timezone-aware), so a naive comparison against
    a string would silently drop matches. Returns ``None`` when the value is
    absent or unparseable.
    """
    if not since:
        return None
    return _parse_dt(since)


def _parse_dt(value: Any) -> Optional[datetime]:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return None


# --- Builders --------------------------------------------------------------


def _build_forecast_points(beliefs_for_q: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Derive forecast points from the ordered belief history for a question."""
    points: list[dict[str, Any]] = []
    for b in beliefs_for_q[-_MAX_FORECAST_POINTS:]:
        p = _pct(b.get("p_calibrated", b.get("p_raw", 0.5)))
        conf = b.get("confidence") or {}
        spread = _clamp(100.0 - _num(conf.get("score"), 50.0), 6.0, 40.0) / 2.0
        label = _forecast_label(b.get("created_at"), b.get("version"))
        points.append(
            {
                "label": label,
                "probability": p,
                "lower": round(_clamp(p - spread, 0.0, 100.0)),
                "upper": round(_clamp(p + spread, 0.0, 100.0)),
            }
        )
    return points


def _forecast_label(created_at: Any, version: Any) -> str:
    dt = _parse_dt(created_at)
    if dt is not None:
        return dt.astimezone(timezone.utc).strftime("%b %d")
    try:
        return f"v{int(version)}"
    except (TypeError, ValueError):
        return "v1"


def _build_question(
    question: dict[str, Any],
    beliefs_for_q: list[dict[str, Any]],
    evidence_by_id: dict[str, dict[str, Any]],
    raw_by_id: dict[str, dict[str, Any]],
    question_evidence: list[dict[str, Any]],
) -> dict[str, Any]:
    latest = beliefs_for_q[-1] if beliefs_for_q else None
    prev = beliefs_for_q[-2] if len(beliefs_for_q) > 1 else None

    country = str(question.get("country", "") or "").upper()
    market, flag = _COUNTRY_DISPLAY.get(country, (country or "Global", country[:2] or "UN"))

    probability = _pct(latest.get("p_calibrated", latest.get("p_raw", 0.5))) if latest else 50.0
    prev_p = _pct(prev.get("p_calibrated", prev.get("p_raw", 0.5))) if prev else probability
    change = round(probability - prev_p)

    thesis = ""
    if latest:
        thesis = str(latest.get("rationale") or "")
    if not thesis:
        thesis = "Awaiting data — no belief has been formed for this question yet."

    forecast = _build_forecast_points(beliefs_for_q)

    # Linked evidence: prefer evidence explicitly tied to this question.
    evidence_items: list[dict[str, Any]] = []
    for ev in question_evidence[:_MAX_EVIDENCE_PER_QUESTION]:
        raw = raw_by_id.get(ev.get("raw_item_id", ""), {})
        title = str(raw.get("title") or ev.get("summary") or "Evidence")
        source = str(raw.get("source") or ev.get("authority") or "desk")
        evidence_items.append(
            {
                "id": str(ev.get("_id", "")),
                "time": _evidence_time(ev.get("published_at")),
                "source": source,
                "title": title,
                "summary": str(ev.get("summary") or ""),
                "reliability": _reliability(ev.get("materiality")),
                "stance": _stance(str(ev.get("direction", ""))),
                "impact": round(
                    _num(ev.get("materiality"))
                    * (1 if _stance(str(ev.get("direction", ""))) != "Lowers" else -1)
                ),
            }
        )

    reviewed = len(question_evidence)
    retained = sum(1 for ev in question_evidence if _num(ev.get("materiality")) >= 3)

    return {
        "id": str(question.get("key") or question.get("_id", "")),
        "key": str(question.get("key", "")),
        "market": market,
        "flag": flag,
        "question": str(question.get("text", "")),
        "shortLabel": _short_label(str(question.get("text", "")), market),
        "probability": probability,
        "change": change,
        "confidence": _grade(latest),
        "horizon": _horizon(question),
        "status": _status(question, latest, change),
        "reviewed": reviewed,
        "retained": retained,
        "thesis": thesis,
        "forecast": forecast,
        "evidence": evidence_items,
    }


def _horizon(question: dict[str, Any]) -> str:
    resolve_by = _parse_dt(question.get("resolve_by"))
    if resolve_by is not None:
        days = (resolve_by - datetime.now(timezone.utc)).days
        if days >= 0:
            return f"{days} days"
    return "Open horizon"


def _build_replay() -> list[dict[str, Any]]:
    """Load the Canada replay fixture into the frontend ReplayEvent shape."""
    try:
        with _REPLAY_FIXTURE.open() as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    docs = sorted(
        data.get("documents", []),
        key=lambda d: _parse_dt(d.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc),
    )
    events: list[dict[str, Any]] = []
    prev_p: Optional[float] = None
    n = len(docs)
    for i, doc in enumerate(docs[:_MAX_REPLAY_EVENTS]):
        dt = _parse_dt(doc.get("published_at"))
        # Deterministic probability ramp derived from position in the timeline.
        prob = round(_clamp(30.0 + (i / max(n - 1, 1)) * 44.0, 0.0, 100.0))
        impact = 0 if prev_p is None else round(prob - prev_p)
        prev_p = prob
        if i == 0:
            kicker = "Baseline"
        elif i == n - 1:
            kicker = "Current view"
        else:
            kicker = f"Signal {i:02d}"
        events.append(
            {
                "date": (
                    dt.astimezone(timezone.utc).strftime("%b %d").upper() if dt else f"STEP {i}"
                ),
                "kicker": kicker,
                "headline": str(doc.get("title", "")),
                "detail": str(doc.get("text", "")),
                "probability": prob,
                "confidence": "A" if prob >= 70 else "B" if prob >= 50 else "C",
                "source": _source_host(str(doc.get("url", ""))) or "replay",
                "impact": impact,
            }
        )
    return events


def _source_host(url: str) -> str:
    if not url:
        return ""
    host = url.split("//", 1)[-1].split("/", 1)[0]
    return host


def _build_calibration(maps: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Active calibration curve (or neutral curve) as forecast/actual percents."""
    model = model_from_doc(maps[0]) if maps else neutral_map()
    points: list[dict[str, Any]] = []
    for i in range(1, 10):  # 10%..90%
        x = i / 10.0
        y = model.apply(x)
        points.append({"forecast": round(x * 100), "actual": round(_clamp(y, 0.0, 1.0) * 100)})
    return points


def _build_resolved(
    resolutions: list[dict[str, Any]],
    scores: list[dict[str, Any]],
    beliefs_by_q: dict[str, list[dict[str, Any]]],
    questions_by_id: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], float]:
    """Resolved questions joined with supervisor scores; also returns Brier mean."""
    supervisor_score: dict[str, dict[str, Any]] = {}
    for s in scores:
        if s.get("agent") == "supervisor":
            supervisor_score[str(s.get("question_id", ""))] = s

    resolved: list[dict[str, Any]] = []
    briers: list[float] = []
    for r in resolutions[:_MAX_RESOLVED]:
        if r.get("proposed", True):
            continue  # only confirmed resolutions
        qid = str(r.get("question_id", ""))
        question = questions_by_id.get(qid, {})
        beliefs_for_q = beliefs_by_q.get(qid, [])
        latest = beliefs_for_q[-1] if beliefs_for_q else None
        prob = _pct(latest.get("p_calibrated", 0.5)) if latest else 50.0
        outcome = "Occurred" if r.get("outcome") == "YES" else "Did not occur"
        score_doc = supervisor_score.get(qid)
        brier = _num(score_doc.get("brier"), None) if score_doc else None  # type: ignore[arg-type]
        if brier is None:
            # Fall back to computing Brier from the belief vs outcome.
            y = 1.0 if r.get("outcome") == "YES" else 0.0
            brier = round((prob / 100.0 - y) ** 2, 4)
        briers.append(brier)
        resolved.append(
            {
                "event": str(question.get("text") or qid or "Resolved question"),
                "probability": prob,
                "outcome": outcome,
                "score": round(_clamp(brier, 0.0, 1.0), 4),
            }
        )
    brier_score = round(sum(briers) / len(briers), 4) if briers else 0.0
    return resolved, brier_score


def _build_stats(desk: dict[str, Any], n_questions: int, n_resolved: int) -> dict[str, Any]:
    return {
        "itemsScanned": int(_num(desk.get("items_scanned"))),
        "itemsTriaged": int(_num(desk.get("items_triaged"))),
        "filteredByJev": int(_num(desk.get("filtered_by_jev"))),
        "pctFiltered": round(_num(desk.get("pct_filtered")), 1),
        "beliefsUpdated": int(_num(desk.get("beliefs_updated"))),
        "totalCostUsd": round(_num(desk.get("total_cost_usd")), 6),
        "questions": int(n_questions),
        "resolved": int(n_resolved),
    }


async def build_snapshot(repo: Repository) -> dict[str, Any]:
    """Assemble the full ``LevySnapshot`` payload from Atlas collections.

    Bulk-loads each collection once (no N+1) and groups in memory.
    """
    # Bulk load — one query per collection.
    questions = await repo.find(QUESTIONS, {}, sort=[("created_at", 1)], limit=_MAX_QUESTIONS)
    beliefs = await repo.find(BELIEFS, {}, sort=[("version", 1)])
    evidence = await repo.find(EVIDENCE, {})
    raw_items = await repo.find(RAW_ITEMS, {})
    resolutions = await repo.find(RESOLUTIONS, {})
    scores = await repo.find(SCORES, {})
    maps = await repo.find(CALIBRATION_MAPS, {}, sort=[("version", -1)], limit=1)

    # Group beliefs by question, preserving version order.
    beliefs_by_q: dict[str, list[dict[str, Any]]] = {}
    for b in beliefs:
        beliefs_by_q.setdefault(str(b.get("question_id", "")), []).append(b)
    for lst in beliefs_by_q.values():
        lst.sort(key=lambda b: _num(b.get("version"), 0.0))

    evidence_by_id = {str(ev.get("_id", "")): ev for ev in evidence}
    raw_by_id = {str(r.get("_id", "")): r for r in raw_items}
    questions_by_id = {str(q.get("_id", "")): q for q in questions}

    # Group evidence by question id (linked via question_ids), newest first.
    evidence_by_q: dict[str, list[dict[str, Any]]] = {}
    for ev in evidence:
        for qid in ev.get("question_ids", []) or []:
            evidence_by_q.setdefault(str(qid), []).append(ev)
    for lst in evidence_by_q.values():
        lst.sort(
            key=lambda e: _parse_dt(e.get("published_at"))
            or datetime.min.replace(tzinfo=timezone.utc),
            reverse=True,
        )

    out_questions: list[dict[str, Any]] = []
    for q in questions:
        qid = str(q.get("_id", ""))
        out_questions.append(
            _build_question(
                q,
                beliefs_by_q.get(qid, []),
                evidence_by_id,
                raw_by_id,
                evidence_by_q.get(qid, []),
            )
        )

    resolved, brier_score = _build_resolved(resolutions, scores, beliefs_by_q, questions_by_id)
    desk = await desk_stats(repo)

    return {
        "source": "atlas",
        "questions": out_questions,
        "replay": _build_replay(),
        "calibration": _build_calibration(maps),
        "resolved": resolved,
        "brierScore": brier_score,
        "stats": _build_stats(desk, len(out_questions), len(resolved)),
    }


@router.get("/v1/snapshot", response_model=LevySnapshotModel)
async def snapshot(repo: Repository = Depends(repo_dep)) -> LevySnapshotModel:
    """Public UI snapshot backed by MongoDB Atlas collections."""
    payload = await build_snapshot(repo)
    return LevySnapshotModel.model_validate(payload)
