"""API routes: exact endpoints from the TDD."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from levy.agents.learning import CalibrationRefit, leaderboard
from levy.agents.pipeline import Pipeline
from levy.agents.replay import SCENARIOS, ReplayEngine
from levy.agents.resolution import ResolutionClerk
from levy.agents.status import agents_status, desk_stats
from levy.api.deps import bus_dep, repo_dep
from levy.api.snapshot import parse_since
from levy.api.sse import event_stream
from levy.core.calibrate import model_from_doc
from levy.core.collections import (
    BELIEFS,
    CALIBRATION_MAPS,
    EVIDENCE,
    EXPOSURES,
    FORECAST_RUNS,
    LESSONS,
    QUESTIONS,
)
from levy.core.db import Repository
from levy.core.events import EventBus

router = APIRouter(prefix="/api")


async def _latest_belief(repo: Repository, question_id: str) -> Optional[dict]:
    b = await repo.find(BELIEFS, {"question_id": question_id}, sort=[("version", -1)], limit=1)
    return b[0] if b else None


@router.get("/health")
async def api_health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/board")
async def board(repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    questions = await repo.find(QUESTIONS, {})
    by_country: dict[str, list[dict]] = {}
    for q in questions:
        belief = await _latest_belief(repo, q["_id"])
        entry = {
            "key": q["key"],
            "text": q["text"],
            "country": q.get("country", ""),
            "authority": q.get("authority", ""),
            "status": q.get("status", ""),
            "p_calibrated": belief.get("p_calibrated") if belief else None,
            "confidence": belief.get("confidence") if belief else None,
            "version": belief.get("version") if belief else 0,
        }
        by_country.setdefault(q.get("country", "GLB"), []).append(entry)
    return {"countries": by_country}


@router.get("/countries/{iso}")
async def country_detail(iso: str, repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    iso = iso.upper()
    questions = await repo.find(QUESTIONS, {"country": iso})
    out_questions = []
    for q in questions:
        belief = await _latest_belief(repo, q["_id"])
        out_questions.append({"question": q, "belief": belief})
    exposures = await repo.find(EXPOSURES, {"country": iso})
    evidence = await repo.find(EVIDENCE, {"countries": iso}, sort=[("published_at", -1)], limit=25)
    if not questions and not exposures and not evidence:
        raise HTTPException(status_code=404, detail=f"no data for country {iso}")
    return {
        "country": iso,
        "questions": out_questions,
        "exposures": exposures,
        "recent_evidence": evidence,
    }


@router.get("/questions/{key}/timeline")
async def question_timeline(key: str, repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    q = await repo.find_one(QUESTIONS, {"key": key})
    if not q:
        raise HTTPException(status_code=404, detail=f"question {key} not found")
    beliefs = await repo.find(BELIEFS, {"question_id": q["_id"]}, sort=[("version", 1)])
    return {"key": key, "question": q, "beliefs": beliefs}


@router.get("/questions/{key}/runs/latest")
async def question_runs_latest(key: str, repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    q = await repo.find_one(QUESTIONS, {"key": key})
    if not q:
        raise HTTPException(status_code=404, detail=f"question {key} not found")
    belief = await _latest_belief(repo, q["_id"])
    runs = await repo.find(
        FORECAST_RUNS, {"question_id": q["_id"]}, sort=[("created_at", -1)], limit=20
    )
    ensemble = [r for r in runs if not r.get("is_red_team")]
    red_team = [r for r in runs if r.get("is_red_team")]
    return {
        "key": key,
        "ensemble": ensemble,
        "red_team": red_team,
        "supervisor": {"rationale": belief.get("rationale") if belief else "", "belief": belief},
    }


@router.get("/evidence")
async def evidence_feed(
    since: Optional[str] = Query(default=None), repo: Repository = Depends(repo_dep)
) -> dict[str, Any]:
    flt: dict[str, Any] = {}
    since_dt = parse_since(since)
    if since_dt is not None:
        # Atlas stores BSON datetimes, so compare against a timezone-aware
        # datetime rather than the raw ISO string.
        flt = {"published_at": {"$gt": since_dt}}
    items = await repo.find(EVIDENCE, flt, sort=[("published_at", -1)], limit=100)
    return {"evidence": items, "since": since}


@router.get("/agents/status")
async def agents_status_route(repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    return await agents_status(repo)


@router.get("/learning")
async def learning(repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    maps = await repo.find(CALIBRATION_MAPS, {}, sort=[("version", -1)], limit=2)
    curves = []
    for m in maps:
        model = model_from_doc(m)
        curve = [[round(x / 10, 2), round(model.apply(x / 10), 4)] for x in range(11)]
        curves.append({"version": m["version"], "method": m["method"], "curve": curve})
    lessons = await repo.find(LESSONS, {"status": "active"}, sort=[("created_at", -1)], limit=10)
    return {
        "calibration": {
            "current": curves[0] if curves else None,
            "previous": curves[1] if len(curves) > 1 else None,
        },
        "leaderboard": await leaderboard(repo),
        "lessons": lessons,
    }


@router.get("/exposures/{iso}")
async def exposures(iso: str, repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    iso = iso.upper()
    items = await repo.find(EXPOSURES, {"country": iso}, sort=[("created_at", -1)])
    if not items:
        raise HTTPException(status_code=404, detail=f"no exposures for {iso}")
    return {"country": iso, "exposures": items}


@router.get("/stats")
async def stats(repo: Repository = Depends(repo_dep)) -> dict[str, Any]:
    return await desk_stats(repo)


@router.get("/stream")
async def stream(request: Request, bus: EventBus = Depends(bus_dep)) -> EventSourceResponse:
    return EventSourceResponse(event_stream(bus))


# --- Write endpoints -------------------------------------------------------


class ConfirmBody(BaseModel):
    outcome: Optional[str] = None
    confirmed_by: str = "human"


@router.post("/resolutions/{key}/confirm")
async def confirm_resolution(
    key: str,
    body: ConfirmBody,
    repo: Repository = Depends(repo_dep),
    bus: EventBus = Depends(bus_dep),
) -> dict[str, Any]:
    clerk = ResolutionClerk(repo, bus=bus)
    result = await clerk.confirm(key, confirmed_by=body.confirmed_by, outcome=body.outcome)
    if result is None:
        raise HTTPException(status_code=404, detail="no proposed resolution and no outcome given")
    # Trigger calibration refit after a resolution.
    await CalibrationRefit(repo).refit()
    return result


class ReplayBody(BaseModel):
    scenario: str
    speed: float = 5.0


@router.post("/demo/replay")
async def demo_replay(body: ReplayBody, bus: EventBus = Depends(bus_dep)) -> dict[str, Any]:
    if body.scenario not in SCENARIOS:
        raise HTTPException(
            status_code=400, detail=f"unknown scenario; choose from {list(SCENARIOS)}"
        )
    engine = ReplayEngine(body.scenario, bus=bus)
    await engine.prepare()
    return await engine.run()


class InjectBody(BaseModel):
    document: Optional[dict[str, Any]] = None


@router.post("/demo/inject")
async def demo_inject(
    body: InjectBody,
    repo: Repository = Depends(repo_dep),
    bus: EventBus = Depends(bus_dep),
) -> dict[str, Any]:
    from levy.seed import CANNED_INJECT

    doc = body.document or CANNED_INJECT
    pipeline = Pipeline(repo, bus=bus)
    return await pipeline.process_document(doc)
