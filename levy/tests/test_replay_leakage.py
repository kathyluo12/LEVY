"""test_replay_leakage: no document with published_at > sim_now reaches any agent."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from levy.agents.replay import SCENARIOS, ReplayEngine
from levy.core.clock import document_visible, leak_probe


def test_document_visibility_cutoff():
    sim_now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert document_visible("2026-07-20T00:00:00Z", sim_now) is True
    assert document_visible("2026-08-01T00:00:00Z", sim_now) is True
    assert document_visible("2026-08-05T00:00:00Z", sim_now) is False


def test_leak_probe_detects_future_dates():
    sim_now = datetime(2026, 8, 1, tzinfo=timezone.utc)
    probe = leak_probe("The deal was signed on 2026-08-22 after talks.", sim_now)
    assert probe["leak"] is True
    assert "2026-08-22" in probe["hits"]

    clean = leak_probe("As of July 2026 no deal exists.", sim_now)
    assert clean["leak"] is False


@pytest.mark.asyncio
async def test_replay_never_reveals_future_documents():
    engine = ReplayEngine("canada_338")
    await engine.prepare()

    # For every simulated day, visible docs must all be dated <= sim_now.
    sim = engine.start
    while sim <= engine.end:
        visible = engine.visible_now(sim)
        for d in visible:
            assert document_visible(d["published_at"], sim)
        sim = engine.clock.advance()


@pytest.mark.asyncio
async def test_replay_runs_and_scores():
    engine = ReplayEngine("canada_338")
    await engine.prepare()
    result = await engine.run()
    assert result["resolved"] is True
    assert result["outcome"] == "YES"
    assert result["documents_processed"] >= 1
    assert "leak_probe_hits" in result
    # isolated replay database naming
    assert result["db"].startswith("levy_replay_")


@pytest.mark.asyncio
async def test_all_scenarios_prepare():
    for scenario in SCENARIOS:
        engine = ReplayEngine(scenario)
        info = await engine.prepare()
        assert info["documents"] >= 1
