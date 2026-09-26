"""test_register_cli_worker: CLI arg validation, worker dispatch, no-key adapters."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from levy.core.jev import JevAdapter
from levy.core.llm import LLMClient
from levy.settings import Settings
from levy.workers.register import build_parser

FIXED_NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


# --- CLI arg validation -----------------------------------------------------


def test_cli_parses_dates():
    parser = build_parser()
    args = parser.parse_args(["--start", "2026-09-01", "--end", "2026-09-10"])
    assert args.start == date(2026, 9, 1)
    assert args.end == date(2026, 9, 10)


def test_cli_rejects_bad_date():
    parser = build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(["--start", "not-a-date"])


def test_cli_flags_default_false():
    parser = build_parser()
    args = parser.parse_args([])
    assert args.dry_run is False
    assert args.ingest_only is False
    assert args.include_full_text is False


def test_cli_conflicting_dates_via_resolve_window():
    from levy.agents.register import resolve_window

    with pytest.raises(ValueError):
        resolve_window(start=date(2026, 9, 1), end=None, days=5, now=FIXED_NOW)


# --- No-provider-key adapter behavior --------------------------------------


@pytest.mark.asyncio
async def test_jev_no_key_uses_fallback_no_network():
    # Atlas-like: online but no key. Must not attempt network.
    settings = Settings(offline=False, openrouter_api_key="")
    adapter = JevAdapter(settings=settings)
    assert adapter.offline is False
    result = await adapter.decide(
        {"item": {"title": "tariff", "text": "duty"}},
        {"relevant": {"type": "noul", "instructions": "tariff?", "criteria": {}}},
    )
    assert result.classifier == "fallback"


@pytest.mark.asyncio
async def test_jev_offline_labels_offline():
    settings = Settings(offline=True)
    adapter = JevAdapter(settings=settings)
    result = await adapter.decide(
        {"item": {"title": "tariff", "text": "duty"}},
        {"relevant": {"type": "noul", "instructions": "tariff?", "criteria": {}}},
    )
    assert result.classifier == "offline"


@pytest.mark.asyncio
async def test_llm_no_key_is_offline_response():
    settings = Settings(offline=False, openrouter_api_key="")
    client = LLMClient(settings=settings)
    resp = await client.chat_json("forecaster_a", "estimate this")
    assert resp.offline is True
    assert "probability" in resp.content


@pytest.mark.asyncio
async def test_llm_offline_response():
    settings = Settings(offline=True)
    client = LLMClient(settings=settings)
    resp = await client.chat_json("supervisor", "reconcile")
    assert resp.offline is True


# --- Worker register scout dispatch ----------------------------------------


@pytest.mark.asyncio
async def test_register_scout_offline_heartbeat_no_network(repo, caplog):
    from levy.core.events import EventBus
    from levy.workers.scouts import RegisterScoutJob

    settings = Settings(offline=True)
    job = RegisterScoutJob(repo, settings, EventBus())
    # Offline tick should be a pure heartbeat: no client attribute is even used.
    with caplog.at_level("INFO"):
        await job.tick()
    assert any("offline heartbeat" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_register_scout_online_dispatches_service(seeded_repo):
    import httpx

    from levy.core.events import EventBus
    from levy.core.federal_register import FederalRegisterClient
    from levy.workers.scouts import RegisterScoutJob

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "count": 1,
                "total_pages": 1,
                "next_page_url": None,
                "results": [
                    {
                        "document_number": "W1",
                        "title": "USTR Section 301 tariff on China",
                        "abstract": "new Section 301 tariff duty, an escalation",
                        "agencies": [{"name": "Office of the U.S. Trade Representative"}],
                        "type": "Notice",
                        "publication_date": "2026-09-25",
                        "html_url": "https://www.federalregister.gov/documents/W1",
                        "pdf_url": "https://example.gov/w1.pdf",
                    }
                ],
            },
        )

    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://www.federalregister.gov")
    client = FederalRegisterClient(
        http_client=http_client,
        clock=lambda: FIXED_NOW,
        sleep=_noop,
    )

    settings = Settings(offline=False, openrouter_api_key="")
    job = RegisterScoutJob(seeded_repo, settings, EventBus())
    # Inject the fake client into the service the job constructs.
    from levy.agents import register as register_mod

    orig = register_mod.FederalRegisterClient
    register_mod.FederalRegisterClient = lambda *a, **k: client  # type: ignore[assignment]
    try:
        await job.tick()
    finally:
        register_mod.FederalRegisterClient = orig  # type: ignore[assignment]

    from levy.core.collections import RAW_ITEMS

    raw = await seeded_repo.find(RAW_ITEMS, {})
    assert any(r.get("source") == "federal_register" for r in raw)


async def _noop(_seconds: float) -> None:
    return None


@pytest.mark.asyncio
async def test_cli_dry_run_never_constructs_database(monkeypatch):
    from levy.workers import register as register_worker

    args = build_parser().parse_args(["--days", "1", "--dry-run", "--json"])

    def fail_make_repository(*_args, **_kwargs):
        raise AssertionError("dry-run attempted to construct the configured repository")

    class Summary:
        def to_dict(self):
            return {
                "start": "2026-09-25",
                "end": "2026-09-26",
                "query": "tariff",
                "mode": "fetch_only",
                "fetched": 0,
            }

    class FakeService:
        def __init__(self, repo, **_kwargs):
            from levy.core.db import InMemoryRepository

            assert isinstance(repo, InMemoryRepository)

        async def run(self, **kwargs):
            assert kwargs["fetch_only"] is True
            return Summary()

    monkeypatch.setattr(register_worker, "make_repository", fail_make_repository)
    monkeypatch.setattr(register_worker, "RegisterService", FakeService)
    result = await register_worker._run(args)
    assert result["mode"] == "fetch_only"
