"""Pydantic domain schemas for all core LEVY documents.

Every model uses stable JSON serialization: datetimes are serialized to
RFC3339 UTC strings via :func:`iso_now` and ``model_dump(mode="json")``.
Documents carry a string ``_id`` (aliased to ``id``) so the same shapes work
in both MongoDB and the in-memory repository.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, ConfigDict, Field


def iso_now() -> datetime:
    return datetime.now(timezone.utc)


def new_id() -> str:
    return uuid.uuid4().hex


class LevyModel(BaseModel):
    """Base with stable JSON serialization and Mongo-friendly id aliasing."""

    model_config = ConfigDict(
        populate_by_name=True,
        use_enum_values=True,
        ser_json_timedelta="iso8601",
    )

    def to_doc(self) -> dict[str, Any]:
        """Serialize to a JSON-safe dict suitable for storage/transport."""
        return self.model_dump(mode="json", by_alias=True, exclude_none=False)


# --- Enums -----------------------------------------------------------------


class Authority(str, Enum):
    S301 = "s301"
    S232 = "s232"
    S338 = "s338"
    S122 = "s122"
    IEEPA = "ieepa"
    COURT = "court"
    DEAL = "deal"
    OTHER = "other"


class Direction(str, Enum):
    ESCALATION = "escalation"
    DE_ESCALATION = "de_escalation"
    DELAY = "delay"
    PROCEDURAL = "procedural"
    NEUTRAL = "neutral"


class QuestionStatus(str, Enum):
    DRAFT = "draft"
    ACTIVE = "active"
    RESOLVED = "resolved"


class JobStatus(str, Enum):
    PENDING = "pending"
    LEASED = "leased"
    DONE = "done"
    FAILED = "failed"


class ConfigStatus(str, Enum):
    CANDIDATE = "candidate"
    SHADOW = "shadow"
    ACTIVE = "active"
    RETIRED = "retired"


# --- Core documents --------------------------------------------------------


class RawItem(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    source: str
    url: str = ""
    published_at: datetime = Field(default_factory=iso_now)
    fetched_at: datetime = Field(default_factory=iso_now)
    title: str = ""
    text: str = ""
    hash: str = ""


class Classification(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    item_id: str
    question_key: Optional[str] = None
    raw_probs: dict[str, Any] = Field(default_factory=dict)
    model: str = ""
    classifier: str = "jev"  # "jev" | "fallback" | "offline"
    latency_ms: float = 0.0
    cost_usd: float = 0.0
    created_at: datetime = Field(default_factory=iso_now)


class Evidence(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    raw_item_id: str
    countries: list[str] = Field(default_factory=list)
    authority: str = Authority.OTHER.value
    direction: str = Direction.NEUTRAL.value
    materiality: int = 0
    specificity: int = 0
    question_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    published_at: datetime = Field(default_factory=iso_now)
    embedding: list[float] = Field(default_factory=list)


class EvidenceRejected(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    raw_item_id: str
    reason: str = ""
    jev_probs: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=iso_now)


class Question(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    key: str
    text: str
    country: str = ""
    authority: str = Authority.OTHER.value
    resolution_source: str = ""
    resolve_by: Optional[datetime] = None
    outcomes: list[str] = Field(default_factory=lambda: ["YES", "NO"])
    status: str = QuestionStatus.DRAFT.value
    budget: float = 5.0
    created_at: datetime = Field(default_factory=iso_now)


class Brief(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    question_id: str
    analyst: str
    version: int = 1
    text: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=iso_now)


class ForecastRun(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    question_id: str
    agent: str
    model: str = ""
    config_version: int = 1
    p_raw: float = 0.5
    buckets: dict[str, float] = Field(default_factory=dict)
    evidence_ids: list[str] = Field(default_factory=list)
    is_red_team: bool = False
    created_at: datetime = Field(default_factory=iso_now)


class Confidence(LevyModel):
    grade: str = "C"
    score: float = 50.0
    components: dict[str, float] = Field(default_factory=dict)


class Belief(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    question_id: str
    version: int = 1
    p_raw: float = 0.5
    p_calibrated: float = 0.5
    buckets: dict[str, float] = Field(default_factory=dict)
    confidence: Confidence = Field(default_factory=Confidence)
    rationale: str = ""
    evidence_ids: list[str] = Field(default_factory=list)
    trigger: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=iso_now)


class MarketTick(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    ts: datetime = Field(default_factory=iso_now)
    meta: dict[str, Any] = Field(default_factory=dict)  # source, instrument
    value: float = 0.0
    volume: float = 0.0


class Resolution(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    question_id: str
    outcome: str = ""  # "YES" | "NO"
    source_url: str = ""
    confirmed_by: str = ""
    proposed: bool = True
    resolved_at: datetime = Field(default_factory=iso_now)


class Score(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    question_id: str
    agent: str
    model: str = ""
    brier: float = 0.0
    log_score: float = 0.0
    created_at: datetime = Field(default_factory=iso_now)


class Lesson(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    text: str
    applies_to: dict[str, Any] = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    status: str = "active"
    uses: int = 0
    helpfulness: float = 0.0
    embedding: list[float] = Field(default_factory=list)
    expires_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=iso_now)


class AgentConfig(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    agent: str
    version: int = 1
    prompt: str = ""
    params: dict[str, Any] = Field(default_factory=dict)
    status: str = ConfigStatus.ACTIVE.value
    author: str = "system"
    reason: str = ""
    created_at: datetime = Field(default_factory=iso_now)


class CalibrationMap(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    version: int = 1
    method: str = "neutral"  # neutral | platt | isotonic
    points: list[list[float]] = Field(default_factory=list)  # [[x, y], ...]
    params: dict[str, Any] = Field(default_factory=dict)
    n_resolutions: int = 0
    created_at: datetime = Field(default_factory=iso_now)


class Event(LevyModel):
    """Base-rate reference event (historical tariff action)."""

    id: str = Field(default_factory=new_id, alias="_id")
    date: datetime = Field(default_factory=iso_now)
    country: str = ""
    authority: str = Authority.OTHER.value
    action: str = ""
    threatened_at: Optional[datetime] = None
    implemented: bool = False
    delay_days: int = 0


class Job(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    type: str
    key: str  # deterministic idempotency key
    payload: dict[str, Any] = Field(default_factory=dict)
    status: str = JobStatus.PENDING.value
    lease_until: Optional[datetime] = None
    attempts: int = 0
    max_attempts: int = 3
    worker: str = ""
    created_at: datetime = Field(default_factory=iso_now)
    updated_at: datetime = Field(default_factory=iso_now)


class WebCache(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    key: str
    provider: str = ""
    kind: str = ""  # search | read | research
    result: Any = None
    credits: float = 0.0
    fetched_at: datetime = Field(default_factory=iso_now)


class StreamOffset(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    consumer: str
    resume_token: Any = None
    updated_at: datetime = Field(default_factory=iso_now)


class Exposure(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    country: str
    question_id: str = ""
    sectors: list[dict[str, Any]] = Field(default_factory=list)
    fx: list[dict[str, Any]] = Field(default_factory=list)
    tickers: list[dict[str, Any]] = Field(default_factory=list)
    market_divergence: Optional[float] = None
    created_at: datetime = Field(default_factory=iso_now)


class LogEntry(LevyModel):
    id: str = Field(default_factory=new_id, alias="_id")
    level: str = "info"
    message: str = ""
    job_key: str = ""
    question_id: str = ""
    created_at: datetime = Field(default_factory=iso_now)


class StreamEvent(LevyModel):
    """In-process event bus / SSE payload."""

    event: str  # belief | evidence | job | lesson | resolution
    data: dict[str, Any] = Field(default_factory=dict)
    seq: int = 0
    created_at: datetime = Field(default_factory=iso_now)
