"""test_routes: API contract tests using the in-memory repo (offline)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from levy.api.main import create_app
from levy.core.db import InMemoryRepository, set_repository
from levy.core.events import set_event_bus


@pytest.fixture
def client():
    # Fresh in-memory repo + bus per test client.
    import asyncio

    repo = InMemoryRepository()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(repo.bootstrap())
    from levy.seed import seed_repository

    loop.run_until_complete(seed_repository(repo))
    set_repository(repo)
    set_event_bus(None)  # reset -> fresh bus created lazily
    app = create_app()
    with TestClient(app) as c:
        yield c
    set_repository(None)
    loop.close()


def test_health_endpoints(client):
    assert client.get("/health").json() == {"status": "ok"}
    assert client.get("/api/health").json() == {"status": "ok"}


def test_board(client):
    r = client.get("/api/board")
    assert r.status_code == 200
    assert "countries" in r.json()


def test_stats(client):
    r = client.get("/api/stats")
    body = r.json()
    assert "items_scanned" in body
    assert "pct_filtered" in body


def test_country_404(client):
    r = client.get("/api/countries/ZZ")
    assert r.status_code == 404


def test_country_ok(client):
    r = client.get("/api/countries/CHN")
    assert r.status_code == 200
    assert r.json()["country"] == "CHN"


def test_question_timeline_404(client):
    r = client.get("/api/questions/NOPE/timeline")
    assert r.status_code == 404


def test_agents_status(client):
    r = client.get("/api/agents/status")
    body = r.json()
    assert "agents" in body and "queue" in body


def test_learning(client):
    r = client.get("/api/learning")
    body = r.json()
    assert "calibration" in body and "leaderboard" in body


def test_evidence_feed(client):
    r = client.get("/api/evidence")
    assert r.status_code == 200
    assert "evidence" in r.json()


def test_inject_and_forecast_flow(client):
    r = client.post("/api/demo/inject", json={})
    assert r.status_code == 200
    body = r.json()
    assert body["ingested"] is True


def test_replay_endpoint(client):
    r = client.post("/api/demo/replay", json={"scenario": "canada_338", "speed": 5})
    assert r.status_code == 200
    assert r.json()["resolved"] is True


def test_replay_unknown_scenario(client):
    r = client.post("/api/demo/replay", json={"scenario": "nope"})
    assert r.status_code == 400
