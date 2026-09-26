"""test_pipeline: triage, forecast, exposure, resolution, learning (offline)."""

from __future__ import annotations

import pytest

from levy.agents.exposure import ExposureMapper
from levy.agents.forecast import ForecastCell, load_forecaster_weights
from levy.agents.learning import CalibrationRefit, Reflector
from levy.agents.pipeline import Pipeline
from levy.agents.resolution import ResolutionClerk
from levy.agents.triage import TriageService
from levy.core.collections import (
    BELIEFS,
    CALIBRATION_MAPS,
    EVIDENCE,
    EVIDENCE_REJECTED,
    QUESTIONS,
    SCORES,
)
from levy.schemas import RawItem


@pytest.mark.asyncio
async def test_triage_accepts_relevant(seeded_repo):
    svc = TriageService(seeded_repo)
    item = RawItem(
        source="ustr",
        title="USTR Section 301 tariff on China",
        text="new Section 301 tariff duty on Chinese imports, an escalation",
    )
    await seeded_repo.insert("raw_items", item.to_doc())
    outcome = await svc.triage_item(item.to_doc())
    assert outcome.accepted is True
    assert "CHN" in outcome.evidence.countries


@pytest.mark.asyncio
async def test_triage_rejects_irrelevant(seeded_repo):
    svc = TriageService(seeded_repo)
    item = RawItem(
        source="blog", title="Local sports update", text="the team won the game last night"
    )
    outcome = await svc.triage_item(item.to_doc())
    assert outcome.accepted is False
    rejected = await seeded_repo.count(EVIDENCE_REJECTED)
    assert rejected >= 1


@pytest.mark.asyncio
async def test_forecast_writes_belief(seeded_repo):
    q = await seeded_repo.find_one(QUESTIONS, {"key": "CHN-DEAL-TRUCE-EXT"})
    cell = ForecastCell(seeded_repo)
    belief = await cell.run(q, trigger={"type": "test"})
    assert 0.01 <= belief.p_calibrated <= 0.99
    assert belief.confidence.grade in ("A", "B", "C", "D")
    assert len(belief.confidence.components) == 6
    stored = await seeded_repo.find(BELIEFS, {"question_id": q["_id"]})
    assert len(stored) == 1


@pytest.mark.asyncio
async def test_belief_append_only_versions(seeded_repo):
    q = await seeded_repo.find_one(QUESTIONS, {"key": "CHN-DEAL-TRUCE-EXT"})
    cell = ForecastCell(seeded_repo)
    b1 = await cell.run(q)
    b2 = await cell.run(q)
    assert b2.version == b1.version + 1


@pytest.mark.asyncio
async def test_exposure_mapping(seeded_repo):
    q = await seeded_repo.find_one(QUESTIONS, {"key": "CAN-S338-REMOVE"})
    cell = ForecastCell(seeded_repo)
    belief = await cell.run(q)
    mapper = ExposureMapper(seeded_repo)
    exp = await mapper.map_belief(belief.to_doc(), q)
    assert exp is not None
    assert exp.country == "CAN"
    assert any(s["name"] == "autos" for s in exp.sectors)


@pytest.mark.asyncio
async def test_resolution_scores_and_refit(seeded_repo):
    q = await seeded_repo.find_one(QUESTIONS, {"key": "CHN-DEAL-TRUCE-EXT"})
    cell = ForecastCell(seeded_repo)
    await cell.run(q)
    clerk = ResolutionClerk(seeded_repo)
    result = await clerk.confirm("CHN-DEAL-TRUCE-EXT", outcome="YES")
    assert result["outcome"] == "YES"
    assert len(result["scores"]) >= 1
    scores = await seeded_repo.count(SCORES)
    assert scores >= 1

    cmap = await CalibrationRefit(seeded_repo).refit()
    assert cmap is not None
    maps = await seeded_repo.count(CALIBRATION_MAPS)
    assert maps >= 1


@pytest.mark.asyncio
async def test_reflector_writes_lesson(seeded_repo):
    q = await seeded_repo.find_one(QUESTIONS, {"key": "CAN-S338-REMOVE"})
    cell = ForecastCell(seeded_repo)
    await cell.run(q)
    reflector = Reflector(seeded_repo)
    lesson = await reflector.reflect(q, "YES")
    assert lesson is not None
    assert lesson.text


@pytest.mark.asyncio
async def test_forecaster_weights_sum_to_one(seeded_repo):
    weights = await load_forecaster_weights(seeded_repo)
    assert abs(sum(weights.values()) - 1.0) < 1e-6


@pytest.mark.asyncio
async def test_end_to_end_pipeline(seeded_repo):
    pipeline = Pipeline(seeded_repo)
    doc = {
        "source": "ustr",
        "title": "USTR Section 301 tariff on China semiconductors",
        "text": "new Section 301 tariff duty, an escalation with named rate and date",
        "url": "https://ustr.gov/x",
        "published_at": "2026-09-01T00:00:00Z",
    }
    result = await pipeline.process_document(doc)
    assert result["ingested"] is True
    assert result["accepted"] is True
    ev = await seeded_repo.count(EVIDENCE)
    assert ev >= 1
