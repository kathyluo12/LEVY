"""Collection names and documented index specifications.

This module is the single source of truth for the 18 collections listed in the
TDD, the additionally required ``exposures`` collection used by the documented
API and Exposure Mapper, plus the capped ``logs`` collection. Index metadata is consumed by the Mongo bootstrap
routine; the in-memory repository ignores index specs but uses the unique-key
declarations to enforce uniqueness.
"""

from __future__ import annotations

from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Optional

# --- Collection names ------------------------------------------------------

RAW_ITEMS = "raw_items"
EVIDENCE = "evidence"
EVIDENCE_REJECTED = "evidence_rejected"
CLASSIFICATIONS = "classifications"
QUESTIONS = "questions"
BRIEFS = "briefs"
FORECAST_RUNS = "forecast_runs"
BELIEFS = "beliefs"
MARKET_TICKS = "market_ticks"
RESOLUTIONS = "resolutions"
SCORES = "scores"
LESSONS = "lessons"
AGENT_CONFIGS = "agent_configs"
CALIBRATION_MAPS = "calibration_maps"
EVENTS = "events"
JOBS = "jobs"
WEB_CACHE = "web_cache"
STREAM_OFFSETS = "stream_offsets"
EXPOSURES = "exposures"
LOGS = "logs"  # capped

ALL_COLLECTIONS = [
    RAW_ITEMS,
    EVIDENCE,
    EVIDENCE_REJECTED,
    CLASSIFICATIONS,
    QUESTIONS,
    BRIEFS,
    FORECAST_RUNS,
    BELIEFS,
    MARKET_TICKS,
    RESOLUTIONS,
    SCORES,
    LESSONS,
    AGENT_CONFIGS,
    CALIBRATION_MAPS,
    EVENTS,
    JOBS,
    WEB_CACHE,
    STREAM_OFFSETS,
    EXPOSURES,
    LOGS,
]


@dataclass
class IndexSpec:
    keys: list[tuple[str, int]]
    unique: bool = False
    ttl_seconds: Optional[int] = None
    name: Optional[str] = None
    note: str = ""


@dataclass
class VectorIndexSpec:
    field: str
    filters: list[str] = dataclass_field(default_factory=list)
    dimensions: int = 256
    name: Optional[str] = None


@dataclass
class CollectionSpec:
    name: str
    indexes: list[IndexSpec] = dataclass_field(default_factory=list)
    vector_indexes: list[VectorIndexSpec] = dataclass_field(default_factory=list)
    # unique key field enforced by the in-memory repo (subset of a unique index)
    unique_key: Optional[str] = None
    time_series: Optional[dict] = None
    capped_bytes: Optional[int] = None


COLLECTION_SPECS: dict[str, CollectionSpec] = {
    RAW_ITEMS: CollectionSpec(
        RAW_ITEMS,
        indexes=[
            IndexSpec([("hash", 1)], unique=True, note="dedup scout output"),
            IndexSpec(
                [("fetched_at", 1)],
                ttl_seconds=30 * 24 * 3600,
                note="TTL 30 days on pre-triage items",
            ),
        ],
        unique_key="hash",
    ),
    EVIDENCE: CollectionSpec(
        EVIDENCE,
        indexes=[
            IndexSpec([("question_ids", 1)]),
            IndexSpec([("published_at", 1)]),
            IndexSpec([("materiality", 1)]),
        ],
        vector_indexes=[
            VectorIndexSpec(
                "embedding", filters=["countries", "authority"], name="evidence_summary_idx"
            ),
        ],
    ),
    EVIDENCE_REJECTED: CollectionSpec(
        EVIDENCE_REJECTED,
        indexes=[
            IndexSpec(
                [("created_at", 1)],
                ttl_seconds=7 * 24 * 3600,
                note="TTL 7 days on rejected items",
            ),
        ],
    ),
    CLASSIFICATIONS: CollectionSpec(
        CLASSIFICATIONS,
        indexes=[IndexSpec([("item_id", 1)])],
    ),
    QUESTIONS: CollectionSpec(
        QUESTIONS,
        indexes=[
            IndexSpec([("key", 1)], unique=True),
            IndexSpec([("country", 1)]),
            IndexSpec([("status", 1)]),
        ],
        unique_key="key",
    ),
    BRIEFS: CollectionSpec(
        BRIEFS,
        indexes=[IndexSpec([("question_id", 1), ("created_at", -1)])],
    ),
    FORECAST_RUNS: CollectionSpec(
        FORECAST_RUNS,
        indexes=[IndexSpec([("question_id", 1), ("created_at", -1)])],
    ),
    BELIEFS: CollectionSpec(
        BELIEFS,
        indexes=[
            IndexSpec(
                [("question_id", 1), ("version", 1)],
                unique=True,
                note="append-only belief log, one row per version",
            ),
        ],
    ),
    MARKET_TICKS: CollectionSpec(
        MARKET_TICKS,
        indexes=[IndexSpec([("ts", 1)])],
        time_series={"timeField": "ts", "metaField": "meta", "granularity": "minutes"},
    ),
    RESOLUTIONS: CollectionSpec(
        RESOLUTIONS,
        indexes=[IndexSpec([("question_id", 1)], unique=True, note="one resolution per question")],
        unique_key="question_id",
    ),
    SCORES: CollectionSpec(
        SCORES,
        indexes=[IndexSpec([("agent", 1)]), IndexSpec([("model", 1)])],
    ),
    LESSONS: CollectionSpec(
        LESSONS,
        indexes=[IndexSpec([("status", 1)])],
        vector_indexes=[
            VectorIndexSpec(
                "embedding", filters=["status", "applies_to.authority"], name="lessons_text_idx"
            ),
        ],
    ),
    AGENT_CONFIGS: CollectionSpec(
        AGENT_CONFIGS,
        indexes=[IndexSpec([("agent", 1), ("status", 1)])],
    ),
    CALIBRATION_MAPS: CollectionSpec(
        CALIBRATION_MAPS,
        indexes=[IndexSpec([("version", 1)], unique=True)],
    ),
    EVENTS: CollectionSpec(
        EVENTS,
        indexes=[IndexSpec([("country", 1)]), IndexSpec([("authority", 1)])],
    ),
    JOBS: CollectionSpec(
        JOBS,
        indexes=[
            IndexSpec([("key", 1)], unique=True, note="idempotency key"),
            IndexSpec([("status", 1), ("lease_until", 1)]),
        ],
        unique_key="key",
    ),
    WEB_CACHE: CollectionSpec(
        WEB_CACHE,
        indexes=[
            IndexSpec([("key", 1)], unique=True),
            IndexSpec([("fetched_at", 1)], ttl_seconds=24 * 3600, note="TTL 24h cache"),
        ],
        unique_key="key",
    ),
    STREAM_OFFSETS: CollectionSpec(
        STREAM_OFFSETS,
        indexes=[IndexSpec([("consumer", 1)], unique=True)],
        unique_key="consumer",
    ),
    EXPOSURES: CollectionSpec(
        EXPOSURES,
        indexes=[IndexSpec([("country", 1)])],
    ),
    LOGS: CollectionSpec(
        LOGS,
        capped_bytes=16 * 1024 * 1024,  # 16 MB capped collection
    ),
}
