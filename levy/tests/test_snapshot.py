"""test_snapshot: /v1/snapshot UI-integration contract (offline, in-memory).

Covers the no-belief seeded state, the with-beliefs/evidence state, A–D grade
mapping, percentage bounds, calibration curve, dynamic stats, and ISO ``since``
filtering on /api/evidence (now that Atlas stores BSON datetimes).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from levy.api.main import create_app
from levy.api.snapshot import parse_since
from levy.core.db import InMemoryRepository, set_repository
from levy.core.events import set_event_bus


def _make_client(
    seed: bool = True,
) -> tuple[TestClient, InMemoryRepository, "asyncio.AbstractEventLoop"]:
    repo = InMemoryRepository()
    loop = asyncio.new_event_loop()
    loop.run_until_complete(repo.bootstrap())
    if seed:
        from levy.seed import seed_repository

        loop.run_until_complete(seed_repository(repo))
    set_repository(repo)
    set_event_bus(None)
    app = create_app()
    return TestClient(app), repo, loop


@pytest.fixture
def client():
    c, _repo, loop = _make_client(seed=True)
    with c:
        yield c
    set_repository(None)
    loop.close()


@pytest.fixture
def empty_client():
    c, _repo, loop = _make_client(seed=False)
    with c:
        yield c
    set_repository(None)
    loop.close()


# --- Contract shape --------------------------------------------------------


def test_snapshot_contract_keys(client):
    r = client.get("/v1/snapshot")
    assert r.status_code == 200
    body = r.json()
    for key in ("source", "questions", "replay", "calibration", "resolved", "brierScore", "stats"):
        assert key in body, f"missing {key}"
    assert body["source"] == "atlas"
    assert isinstance(body["questions"], list)


def test_snapshot_lives_outside_api_prefix(client):
    # It must NOT be under /api.
    assert client.get("/api/v1/snapshot").status_code == 404
    assert client.get("/v1/snapshot").status_code == 200


def test_question_camelcase_fields(client):
    body = client.get("/v1/snapshot").json()
    q = body["questions"][0]
    expected = {
        "id",
        "key",
        "market",
        "flag",
        "question",
        "shortLabel",
        "probability",
        "change",
        "confidence",
        "horizon",
        "status",
        "reviewed",
        "retained",
        "thesis",
        "forecast",
        "evidence",
    }
    assert expected.issubset(q.keys())


# --- No-belief seeded state: honest neutral / awaiting-data ----------------


def test_no_belief_state_is_neutral(client):
    body = client.get("/v1/snapshot").json()
    # Seeded questions exist but no beliefs/evidence yet.
    q = body["questions"][0]
    assert q["probability"] == 50.0  # neutral
    assert q["change"] == 0
    assert q["confidence"] == "D"  # ungraded -> lowest grade
    assert q["status"] == "Stable"
    assert q["forecast"] == []
    assert q["evidence"] == []  # never fabricated
    assert "Awaiting data" in q["thesis"]


def test_empty_repo_snapshot_is_valid(empty_client):
    body = empty_client.get("/v1/snapshot").json()
    assert body["questions"] == []
    assert body["resolved"] == []
    assert body["brierScore"] == 0.0
    # Neutral calibration curve is always present.
    assert len(body["calibration"]) == 9
    # Replay fixture is independent of repo state.
    assert len(body["replay"]) >= 1


# --- With beliefs + evidence -----------------------------------------------


def test_snapshot_with_beliefs_and_evidence(client):
    inj = client.post("/api/demo/inject", json={})
    assert inj.status_code == 200
    body = client.get("/v1/snapshot").json()
    with_data = [q for q in body["questions"] if q["forecast"] or q["evidence"]]
    assert with_data, "expected at least one question with a belief/evidence after inject"

    q = with_data[0]
    # Evidence items carry the linked raw_item fields, never fabricated.
    for ev in q["evidence"]:
        assert set(ev.keys()) == {
            "id",
            "time",
            "source",
            "title",
            "summary",
            "reliability",
            "stance",
            "impact",
        }
        assert ev["reliability"] in {"High", "Medium"}
        assert ev["stance"] in {"Raises", "Lowers", "Neutral"}
    # Forecast points reflect belief history.
    for pt in q["forecast"]:
        assert 0 <= pt["probability"] <= 100
        assert 0 <= pt["lower"] <= pt["upper"] <= 100
    assert q["reviewed"] >= 1
    assert q["retained"] >= 0


def test_confidence_grades_are_a_to_d(client):
    client.post("/api/demo/inject", json={})
    body = client.get("/v1/snapshot").json()
    grades = {q["confidence"] for q in body["questions"]}
    assert grades, "expected grades"
    assert grades.issubset({"A", "B", "C", "D"})


def test_probabilities_are_bounded_percentages(client):
    client.post("/api/demo/inject", json={})
    body = client.get("/v1/snapshot").json()
    for q in body["questions"]:
        assert 0 <= q["probability"] <= 100
        assert -100 <= q["change"] <= 100
        assert q["status"] in {"Escalating", "Monitoring", "Stable"}


# --- Calibration -----------------------------------------------------------


def test_calibration_curve_bounds(client):
    body = client.get("/v1/snapshot").json()
    cal = body["calibration"]
    assert len(cal) == 9
    for pt in cal:
        assert 0 <= pt["forecast"] <= 100
        assert 0 <= pt["actual"] <= 100


# --- Resolved + Brier ------------------------------------------------------


def test_resolved_and_brier_after_replay(client):
    r = client.post("/api/demo/replay", json={"scenario": "canada_338", "speed": 5})
    assert r.status_code == 200
    assert r.json()["resolved"] is True
    # Replay runs in an isolated repo, so live resolved may stay empty; the
    # confirm endpoint writes to the live repo. Exercise that path too.
    body = client.get("/v1/snapshot").json()
    assert isinstance(body["resolved"], list)
    assert 0.0 <= body["brierScore"] <= 1.0


def test_resolved_via_confirm(client):
    # Give the question a belief, then confirm a resolution.
    client.post("/api/demo/inject", json={})
    conf = client.post(
        "/api/resolutions/CHN-S301-SEMI/confirm",
        json={"outcome": "YES", "confirmed_by": "test"},
    )
    assert conf.status_code == 200
    body = client.get("/v1/snapshot").json()
    events = [row["event"] for row in body["resolved"]]
    # The confirmed question should surface in resolved with a bounded score.
    assert body["resolved"], "expected a resolved entry after confirm"
    for row in body["resolved"]:
        assert 0.0 <= row["score"] <= 1.0
        assert row["outcome"] in {"Occurred", "Did not occur"}
        assert 0 <= row["probability"] <= 100
    assert any(events)


# --- Stats -----------------------------------------------------------------


def test_stats_shape_and_dynamics(client):
    body = client.get("/v1/snapshot").json()
    stats = body["stats"]
    for key in (
        "itemsScanned",
        "itemsTriaged",
        "filteredByJev",
        "pctFiltered",
        "beliefsUpdated",
        "totalCostUsd",
        "questions",
        "resolved",
    ):
        assert key in stats
    assert stats["questions"] == len(body["questions"])

    before = stats["itemsScanned"]
    client.post("/api/demo/inject", json={})
    after = client.get("/v1/snapshot").json()["stats"]
    assert after["itemsScanned"] >= before  # dynamic, grows with ingestion


# --- Replay ----------------------------------------------------------------


def test_replay_from_canada_fixture(client):
    body = client.get("/v1/snapshot").json()
    replay = body["replay"]
    assert replay
    assert replay[0]["kicker"] == "Baseline"
    assert replay[-1]["kicker"] == "Current view"
    for ev in replay:
        assert 0 <= ev["probability"] <= 100
        assert ev["confidence"] in {"A", "B", "C", "D"}


# --- ISO since filtering ---------------------------------------------------


def test_parse_since_variants():
    assert parse_since(None) is None
    assert parse_since("") is None
    assert parse_since("not-a-date") is None
    dt = parse_since("2026-01-02T03:04:05Z")
    assert dt is not None and dt.tzinfo is not None
    naive = parse_since("2026-01-02T03:04:05")
    assert naive is not None and naive.tzinfo is not None  # coerced to UTC


def test_evidence_since_filter_iso(client):
    client.post("/api/demo/inject", json={})
    all_items = client.get("/api/evidence").json()["evidence"]
    assert all_items, "expected evidence after inject"

    past = client.get("/api/evidence", params={"since": "2000-01-01T00:00:00Z"}).json()
    assert len(past["evidence"]) == len(all_items)  # everything is after 2000

    future = client.get("/api/evidence", params={"since": "2999-01-01T00:00:00Z"}).json()
    assert future["evidence"] == []  # nothing is after 2999
    assert future["since"] == "2999-01-01T00:00:00Z"
