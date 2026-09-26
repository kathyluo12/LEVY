"""Agent status + desk stats aggregation for /api/agents/status and /api/stats."""

from __future__ import annotations

from typing import Any

from levy.agents.analysts import ANALYSTS
from levy.agents.forecast import FORECASTER_ROLES
from levy.agents.scouts import SCOUT_ROSTER
from levy.core.collections import (
    BELIEFS,
    CLASSIFICATIONS,
    EVIDENCE,
    EVIDENCE_REJECTED,
    JOBS,
    RAW_ITEMS,
)
from levy.core.db import Repository
from levy.schemas import JobStatus

ALL_AGENTS = (
    [s.name for s in SCOUT_ROSTER]
    + ["triage"]
    + [f"{a}_analyst" for a in ANALYSTS]
    + FORECASTER_ROLES
    + [
        "red_team",
        "supervisor",
        "calibrator",
        "question_factory",
        "exposure_mapper",
        "resolution_clerk",
        "reflector",
    ]
)


async def agents_status(repo: Repository) -> dict[str, Any]:
    classifications = await repo.find(CLASSIFICATIONS, {})
    cost_today = round(sum(c.get("cost_usd", 0.0) for c in classifications), 6)
    pending = await repo.count(JOBS, {"status": JobStatus.PENDING.value})
    leased = await repo.count(JOBS, {"status": JobStatus.LEASED.value})

    agents = []
    for name in ALL_AGENTS:
        agents.append(
            {
                "agent": name,
                "status": "idle",
                "last_run": None,
                "queue_depth": pending if name in ("triage",) else 0,
                "cost_today": 0.0,
            }
        )
    return {
        "agents": agents,
        "queue": {"pending": pending, "leased": leased},
        "cost_today": cost_today,
    }


async def desk_stats(repo: Repository) -> dict[str, Any]:
    scanned = await repo.count(RAW_ITEMS)
    accepted = await repo.count(EVIDENCE)
    rejected = await repo.count(EVIDENCE_REJECTED)
    beliefs = await repo.count(BELIEFS)
    classifications = await repo.find(CLASSIFICATIONS, {})
    total_cost = round(sum(c.get("cost_usd", 0.0) for c in classifications), 6)
    total_triaged = accepted + rejected
    pct_filtered = round(100 * rejected / total_triaged, 1) if total_triaged else 0.0
    return {
        "items_scanned": scanned,
        "items_triaged": total_triaged,
        "filtered_by_jev": rejected,
        "pct_filtered": pct_filtered,
        "beliefs_updated": beliefs,
        "total_cost_usd": total_cost,
    }
