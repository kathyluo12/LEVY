"""test_backfill_resume: resumable/idempotent pipeline, LLM provider-error
fallback, question-factory linking and honest register summary counters.

All offline / in-memory — no network or Atlas access.
"""

from __future__ import annotations

import httpx
import pytest

from levy.agents.pipeline import Pipeline
from levy.agents.question_factory import QuestionFactory, make_key
from levy.core.collections import (
    BELIEFS,
    CLASSIFICATIONS,
    EVIDENCE,
    QUESTIONS,
    RAW_ITEMS,
)
from levy.core.db import InMemoryRepository
from levy.core.llm import LLMClient
from levy.schemas import Question, QuestionStatus
from levy.settings import Settings

DOC = {
    "source": "federal_register",
    "title": "USTR Section 301 tariff on China semiconductors",
    "text": "new Section 301 tariff duty, an escalation with named rate and date",
    "url": "https://www.federalregister.gov/documents/X1",
    "published_at": "2026-09-01T00:00:00Z",
    "hash": "resume-test-hash-1",
}


class _ForecastBoom(Exception):
    pass


# --- LLM provider-error fallback -------------------------------------------


@pytest.mark.asyncio
async def test_llm_http_400_returns_offline_fallback_no_exception(caplog):
    """A persistent HTTP 400 from the provider must degrade to a deterministic
    offline response flagged offline=True — never raise."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": {"code": 400, "message": "bad request"}})

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://openrouter.ai")
    settings = Settings(offline=False, openrouter_api_key="sk-test")
    llm = LLMClient(settings=settings, http_client=client, max_retries=2)

    with caplog.at_level("WARNING"):
        resp = await llm.chat_json("forecaster_a", "estimate this")

    assert resp.offline is True
    assert "probability" in resp.content
    # Warning logged, and it must not leak the key or payload.
    assert any("offline fallback" in r.message for r in caplog.records)
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert "sk-test" not in joined
    assert "estimate this" not in joined
    await client.aclose()


@pytest.mark.asyncio
async def test_llm_timeout_returns_offline_fallback():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("timed out", request=request)

    transport = httpx.MockTransport(handler)
    client = httpx.AsyncClient(transport=transport, base_url="https://openrouter.ai")
    settings = Settings(offline=False, openrouter_api_key="sk-test")
    llm = LLMClient(settings=settings, http_client=client, max_retries=2)

    resp = await llm.chat_json("supervisor", "reconcile")
    assert resp.offline is True
    assert "rationale" in resp.content
    await client.aclose()


@pytest.mark.asyncio
async def test_llm_budget_exceeded_still_raises():
    """BudgetExceeded is a real control signal raised before any provider call
    and must NOT be swallowed by the fallback path."""
    from levy.core.llm import BudgetExceeded, BudgetTracker

    settings = Settings(offline=False, openrouter_api_key="sk-test")
    llm = LLMClient(settings=settings, max_retries=1)
    tight = BudgetTracker(limit_usd=0.0)
    with pytest.raises(BudgetExceeded):
        await llm.chat_json("forecaster_a", "x" * 10_000, budget=tight)


# --- $addToSet operator ----------------------------------------------------


@pytest.mark.asyncio
async def test_addtoset_operator_matches_mongo_semantics():
    repo = InMemoryRepository()
    await repo.bootstrap()
    await repo.insert(EVIDENCE, {"_id": "e1", "raw_item_id": "r1", "question_ids": ["q1"]})
    # Adding an existing member is a no-op.
    await repo.update_one(EVIDENCE, {"_id": "e1"}, {"$addToSet": {"question_ids": "q1"}})
    doc = await repo.find_one(EVIDENCE, {"_id": "e1"})
    assert doc["question_ids"] == ["q1"]
    # Adding a new member appends it.
    await repo.update_one(EVIDENCE, {"_id": "e1"}, {"$addToSet": {"question_ids": "q2"}})
    doc = await repo.find_one(EVIDENCE, {"_id": "e1"})
    assert doc["question_ids"] == ["q1", "q2"]
    # $each adds multiple distinct values only.
    await repo.update_one(
        EVIDENCE, {"_id": "e1"}, {"$addToSet": {"question_ids": {"$each": ["q2", "q3"]}}}
    )
    doc = await repo.find_one(EVIDENCE, {"_id": "e1"})
    assert doc["question_ids"] == ["q1", "q2", "q3"]


# --- Question factory links an existing deterministic question -------------


@pytest.mark.asyncio
async def test_factory_links_existing_question(repo):
    # Pre-create the deterministic question the factory would derive.
    key = make_key("CHN", "s301", "escalation")
    q = Question(
        key=key,
        text="pre-existing",
        country="CHN",
        authority="s301",
        status=QuestionStatus.DRAFT.value,
    )
    await repo.insert(QUESTIONS, q.to_doc())
    await repo.insert(
        EVIDENCE,
        {
            "_id": "ev1",
            "raw_item_id": "r1",
            "countries": ["CHN"],
            "authority": "s301",
            "direction": "escalation",
            "question_ids": [],
            "summary": "s",
        },
    )
    factory = QuestionFactory(repo)
    ev = await repo.find_one(EVIDENCE, {"_id": "ev1"})
    result = await factory.maybe_create(ev)
    # Returns the EXISTING question (not None) and links the evidence to it.
    assert result is not None
    assert result.id == q.id
    linked = await repo.find_one(EVIDENCE, {"_id": "ev1"})
    assert linked["question_ids"] == [q.id]
    # No duplicate question created.
    assert await repo.count(QUESTIONS, {"key": key}) == 1


# --- Resumable / idempotent pipeline ---------------------------------------


async def _counts(repo, raw_item_id):
    raw = await repo.count(RAW_ITEMS)
    ev = await repo.count(EVIDENCE, {"raw_item_id": raw_item_id})
    cls = await repo.count(CLASSIFICATIONS)
    return raw, ev, cls


@pytest.mark.asyncio
async def test_resume_after_forecast_failure_then_success(repo):
    """Run 1: triage/evidence succeed but the forecast blows up -> partial
    state persisted. Run 2: resume -> belief appears, and raw/evidence/
    classification each remain exactly one."""
    pipeline = Pipeline(repo)

    # Force the forecast to fail on the first run only.
    calls = {"n": 0}
    original = pipeline.run_forecast

    async def flaky(question, *, trigger=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise _ForecastBoom("simulated forecast failure after triage")
        return await original(question, trigger=trigger)

    pipeline.run_forecast = flaky  # type: ignore[assignment]

    r1 = await pipeline.process_document(DOC)
    assert r1["ingested"] is True
    assert r1["accepted"] is True
    assert "error" in r1  # forecast failed but insertion is honest
    assert r1["beliefs"] == []

    raw_doc = await repo.find_one(RAW_ITEMS, {"hash": DOC["hash"]})
    raw_item_id = raw_doc["_id"]
    raw, ev, cls = await _counts(repo, raw_item_id)
    assert (raw, ev) == (1, 1)
    # Exactly one draft question was created by the factory (no seed matches).
    linked = (await repo.find_one(EVIDENCE, {"raw_item_id": raw_item_id}))["question_ids"]
    assert len(linked) == 1
    beliefs = await repo.count(BELIEFS)
    assert beliefs == 0

    # Run 2: resume. Same doc (duplicate hash) must resume, not no-op.
    r2 = await pipeline.process_document(DOC)
    assert r2["resumed"] is True
    assert r2["accepted"] is True
    assert len(r2["beliefs"]) == 1

    # Exactly one raw / evidence / classification for this item; belief exists.
    raw2, ev2, cls2 = await _counts(repo, raw_item_id)
    assert ev2 == 1, "evidence must not be duplicated on resume"
    assert cls2 == cls, "triage must not reclassify on resume"
    assert await repo.count(BELIEFS) == 1
    # No duplicate question_ids after resume.
    linked2 = (await repo.find_one(EVIDENCE, {"raw_item_id": raw_item_id}))["question_ids"]
    assert linked2 == linked

    # Run 3: fully complete -> no new belief, reported as duplicate/complete.
    r3 = await pipeline.process_document(DOC)
    assert r3["resumed"] is True
    assert r3["complete"] is True
    assert r3["beliefs"] == []
    assert await repo.count(BELIEFS) == 1
    assert await repo.count(EVIDENCE, {"raw_item_id": raw_item_id}) == 1


@pytest.mark.asyncio
async def test_resume_when_triage_never_ran(repo):
    """Raw item exists but no evidence (triage never completed): resume must
    run triage and produce evidence without duplicating the raw item."""
    from levy.agents.scouts import ScoutService

    scouts = ScoutService(repo)
    await scouts.ingest(DOC)
    assert await repo.count(RAW_ITEMS) == 1
    assert await repo.count(EVIDENCE) == 0

    pipeline = Pipeline(repo)
    result = await pipeline.process_document(DOC)
    assert result["resumed"] is True
    assert result["accepted"] is True
    assert await repo.count(RAW_ITEMS) == 1
    assert await repo.count(EVIDENCE) == 1


# --- Honest register summary counters --------------------------------------


@pytest.mark.asyncio
async def test_register_summary_counts_resumed_not_duplicate(repo):
    """Rerunning the same doc after a forecast failure should be reported as
    resumed, never as a plain duplicate/no-op or inserted=0."""
    from datetime import date

    from levy.agents.register import RegisterService

    class _FakeClient:
        request_count = 0
        pages_fetched = 0

        async def fetch_documents(self, *a, **k):
            self.request_count = 1
            self.pages_fetched = 1
            return [DOC]

    # Run 1 with a pipeline whose forecast fails, leaving partial state.
    svc = RegisterService(repo, settings=Settings(offline=True), client=_FakeClient())

    # Monkeypatch Pipeline.run_forecast to fail on first call.
    import levy.agents.register as reg_mod

    state = {"fail": True}
    orig = Pipeline.run_forecast

    async def flaky(self, question, *, trigger=None):
        if state["fail"]:
            raise _ForecastBoom("boom")
        return await orig(self, question, trigger=trigger)

    Pipeline.run_forecast = flaky  # type: ignore[assignment]
    try:
        s1 = await svc.run(start=date(2026, 9, 1), end=date(2026, 9, 2))
        assert s1.inserted == 1
        assert s1.resumed == 0
        assert s1.accepted == 1
        assert len(s1.errors) == 1  # forecast error surfaced honestly
        assert s1.beliefs == 0

        # Run 2: forecast now succeeds -> resumed, belief written.
        state["fail"] = False
        s2 = await svc.run(start=date(2026, 9, 1), end=date(2026, 9, 2))
        assert s2.inserted == 0
        assert s2.resumed == 1
        assert s2.duplicates == 0
        assert s2.beliefs == 1

        # Run 3: fully complete -> reported as duplicate (already complete).
        s3 = await svc.run(start=date(2026, 9, 1), end=date(2026, 9, 2))
        assert s3.resumed == 0
        assert s3.duplicates == 1
        assert s3.beliefs == 0
    finally:
        Pipeline.run_forecast = orig  # type: ignore[assignment]
        _ = reg_mod  # keep import used
