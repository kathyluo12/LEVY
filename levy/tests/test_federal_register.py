"""test_federal_register: client bounds, retries, normalization, filter, service.

All tests use httpx.MockTransport or a fake client — no live network.
"""

from __future__ import annotations

from datetime import date, datetime, timezone

import httpx
import pytest

from levy.agents.register import RegisterService, resolve_window
from levy.core.collections import EVIDENCE, RAW_ITEMS
from levy.core.federal_register import (
    MAX_DOCUMENTS,
    MAX_PAGES,
    MAX_PER_PAGE,
    FederalRegisterClient,
    FederalRegisterResult,
    combine_text,
    is_tariff_relevant,
    strip_html,
)

FIXED_NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _clock():
    return FIXED_NOW


def _result(
    number: str,
    *,
    title: str = "Tariff notice",
    abstract: str = "A tariff action",
    excerpts: str = "",
    agencies=None,
    doctype: str = "Notice",
    pub: str = "2026-09-20",
    pdf: str = "https://example.gov/x.pdf",
) -> dict:
    return {
        "document_number": number,
        "title": title,
        "abstract": abstract,
        "excerpts": excerpts,
        "agencies": agencies if agencies is not None else [{"name": "Commerce Department"}],
        "type": doctype,
        "publication_date": pub,
        "html_url": f"https://www.federalregister.gov/documents/{number}",
        "pdf_url": pdf,
    }


def _page(results, *, count=None, total_pages=1, next_url=None) -> dict:
    return {
        "count": count if count is not None else len(results),
        "total_pages": total_pages,
        "next_page_url": next_url,
        "results": results,
    }


def _client_with(handler, **kwargs) -> FederalRegisterClient:
    transport = httpx.MockTransport(handler)
    http_client = httpx.AsyncClient(transport=transport, base_url="https://www.federalregister.gov")
    return FederalRegisterClient(
        http_client=http_client,
        clock=_clock,
        sleep=_noop_sleep,
        **kwargs,
    )


async def _noop_sleep(_seconds: float) -> None:
    return None


# --- HTML / text normalization ---------------------------------------------


def test_strip_html_removes_tags_and_scripts():
    raw = "<p>Hello <b>world</b></p><script>alert('x')</script> &amp; more"
    out = strip_html(raw)
    assert "alert" not in out
    assert "<" not in out
    assert "Hello world" in out
    assert "& more" in out


def test_combine_text_is_bounded():
    result = FederalRegisterResult(
        document_number="2026-1",
        title="T" * 100,
        abstract="A" * 100,
        excerpts="E" * 100,
    )
    out = combine_text(result, max_chars=50)
    assert len(out) <= 50


# --- Tariff prefilter -------------------------------------------------------


def test_prefilter_keeps_tariff_terms():
    r = FederalRegisterResult(document_number="1", title="New tariff on steel", abstract="")
    assert is_tariff_relevant(r) is True


def test_prefilter_keeps_antidumping():
    r = FederalRegisterResult(
        document_number="2", title="Antidumping duty order", abstract="countervailing"
    )
    assert is_tariff_relevant(r) is True


def test_prefilter_keeps_by_agency():
    r = FederalRegisterResult(
        document_number="3",
        title="Some determination",
        abstract="nothing obvious",
        agencies=[{"name": "Office of the U.S. Trade Representative"}],
    )
    assert is_tariff_relevant(r) is True


def test_prefilter_drops_unrelated():
    r = FederalRegisterResult(
        document_number="4",
        title="National park fee schedule",
        abstract="camping",
        agencies=[{"name": "Department of the Interior"}],
    )
    assert is_tariff_relevant(r) is False


# --- Pagination + bounds ----------------------------------------------------


@pytest.mark.asyncio
async def test_pagination_collects_across_pages():
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(dict(request.url.params).get("page", "1"))
        if page == 1:
            return httpx.Response(200, json=_page([_result("p1")], total_pages=2))
        return httpx.Response(200, json=_page([_result("p2")], total_pages=2))

    client = _client_with(handler)
    docs = await client.fetch_documents(
        date(2026, 9, 1), date(2026, 9, 25), per_page=1, max_pages=2
    )
    numbers = {d["metadata"]["document_number"] for d in docs}
    assert numbers == {"p1", "p2"}


@pytest.mark.asyncio
async def test_max_documents_bound_respected():
    def handler(request: httpx.Request) -> httpx.Response:
        results = [_result(f"d{i}") for i in range(50)]
        return httpx.Response(200, json=_page(results, total_pages=1))

    client = _client_with(handler)
    docs = await client.fetch_documents(
        date(2026, 9, 1), date(2026, 9, 25), max_documents=5, max_pages=1
    )
    assert len(docs) == 5


@pytest.mark.asyncio
async def test_per_page_clamped_in_request():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["per_page"] = dict(request.url.params).get("per_page")
        return httpx.Response(200, json=_page([_result("x")], total_pages=1))

    client = _client_with(handler)
    await client.fetch_documents(date(2026, 9, 1), date(2026, 9, 25), per_page=99999, max_pages=1)
    assert int(captured["per_page"]) == MAX_PER_PAGE


def test_module_bounds():
    assert MAX_PER_PAGE == 1000
    assert MAX_PAGES == 10
    assert MAX_DOCUMENTS == 500


# --- Date params + future exclusion ----------------------------------------


@pytest.mark.asyncio
async def test_date_params_are_sent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        captured.update(params)
        return httpx.Response(200, json=_page([_result("x")], total_pages=1))

    client = _client_with(handler)
    await client.fetch_documents(date(2026, 9, 1), date(2026, 9, 25), max_pages=1)
    assert captured["conditions[publication_date][gte]"] == "2026-09-01"
    assert captured["conditions[publication_date][lte]"] == "2026-09-25"
    assert captured["order"] == "newest"


@pytest.mark.asyncio
async def test_future_dates_excluded_by_default():
    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        # The end date should be clamped to "today" (2026-09-26).
        assert params["conditions[publication_date][lte]"] == "2026-09-26"
        results = [
            _result("past", pub="2026-09-20"),
            _result("future", pub="2026-12-31"),
        ]
        return httpx.Response(200, json=_page(results, total_pages=1))

    client = _client_with(handler)
    docs = await client.fetch_documents(date(2026, 9, 1), date(2026, 12, 31), max_pages=1)
    numbers = {d["metadata"]["document_number"] for d in docs}
    assert "future" not in numbers
    assert "past" in numbers


# --- Retry / 429 / 5xx ------------------------------------------------------


@pytest.mark.asyncio
async def test_retry_on_429_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "1"}, json={})
        return httpx.Response(200, json=_page([_result("ok")], total_pages=1))

    client = _client_with(handler)
    docs = await client.fetch_documents(date(2026, 9, 1), date(2026, 9, 25), max_pages=1)
    assert calls["n"] == 2
    assert len(docs) == 1


@pytest.mark.asyncio
async def test_retry_on_5xx_then_success():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 2:
            return httpx.Response(503, json={})
        return httpx.Response(200, json=_page([_result("ok")], total_pages=1))

    client = _client_with(handler)
    docs = await client.fetch_documents(date(2026, 9, 1), date(2026, 9, 25), max_pages=1)
    assert calls["n"] == 3
    assert len(docs) == 1


@pytest.mark.asyncio
async def test_4xx_fails_clearly():
    from levy.core.federal_register import FederalRegisterError

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={})

    client = _client_with(handler)
    with pytest.raises(FederalRegisterError):
        await client.fetch_documents(date(2026, 9, 1), date(2026, 9, 25), max_pages=1)


# --- Stable hash / dedup ----------------------------------------------------


@pytest.mark.asyncio
async def test_stable_hash_dedupes_updated_abstract():
    from levy.core.federal_register import _stable_hash

    r1 = FederalRegisterResult(document_number="D1", title="t", abstract="first abstract")
    r2 = FederalRegisterResult(document_number="D1", title="t", abstract="EDITED abstract")
    c = FederalRegisterClient(clock=_clock)
    n1 = c.normalize(r1)
    n2 = c.normalize(r2)
    assert n1["hash"] == n2["hash"] == _stable_hash("D1")


@pytest.mark.asyncio
async def test_duplicate_document_numbers_collapsed():
    def handler(request: httpx.Request) -> httpx.Response:
        results = [_result("dup"), _result("dup")]
        return httpx.Response(200, json=_page(results, total_pages=1))

    client = _client_with(handler)
    docs = await client.fetch_documents(date(2026, 9, 1), date(2026, 9, 25), max_pages=1)
    assert len(docs) == 1


# --- Full text (opt-in) -----------------------------------------------------


@pytest.mark.asyncio
async def test_include_full_text_fetches_detail_and_raw():
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("documents.json"):
            return httpx.Response(200, json=_page([_result("F1")], total_pages=1))
        if path.endswith("F1.json"):
            return httpx.Response(
                200,
                json={
                    "document_number": "F1",
                    "raw_text_url": "https://www.federalregister.gov/raw/F1.txt",
                    "topics": ["Trade"],
                    "effective_on": "2026-10-01",
                },
            )
        # raw text
        return httpx.Response(200, text="FULL TARIFF TEXT BODY")

    client = _client_with(handler)
    docs = await client.fetch_documents(
        date(2026, 9, 1), date(2026, 9, 25), max_pages=1, include_full_text=True
    )
    assert len(docs) == 1
    assert docs[0]["metadata"]["topics"] == ["Trade"]
    assert docs[0]["metadata"]["effective_on"] == "2026-10-01"
    assert "FULL TARIFF TEXT BODY" in docs[0]["text"]


@pytest.mark.asyncio
async def test_include_full_text_rejects_off_host_url_without_request():
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        path = request.url.path
        if path.endswith("documents.json"):
            return httpx.Response(200, json=_page([_result("F2")], total_pages=1))
        if path.endswith("F2.json"):
            return httpx.Response(
                200,
                json={
                    "document_number": "F2",
                    "raw_text_url": "https://169.254.169.254/latest/meta-data/",
                },
            )
        raise AssertionError(f"untrusted URL was requested: {request.url}")

    client = _client_with(handler)
    docs = await client.fetch_documents(
        date(2026, 9, 1), date(2026, 9, 25), max_pages=1, include_full_text=True
    )
    assert len(docs) == 1
    assert requested_paths == ["/api/v1/documents.json", "/api/v1/documents/F2.json"]


# --- resolve_window ---------------------------------------------------------


def test_resolve_window_days():
    start, end = resolve_window(start=None, end=None, days=2, now=FIXED_NOW)
    assert end == date(2026, 9, 26)
    assert start == date(2026, 9, 24)


def test_resolve_window_conflicting_args():
    with pytest.raises(ValueError):
        resolve_window(start=date(2026, 9, 1), end=None, days=3, now=FIXED_NOW)


def test_resolve_window_end_before_start():
    with pytest.raises(ValueError):
        resolve_window(start=date(2026, 9, 10), end=date(2026, 9, 1), days=None, now=FIXED_NOW)


# --- Service modes ----------------------------------------------------------


def _fake_docs_client():
    def handler(request: httpx.Request) -> httpx.Response:
        results = [
            _result(
                "S1",
                title="USTR Section 301 tariff on China",
                abstract="new Section 301 tariff duty on Chinese imports, an escalation",
                agencies=[{"name": "Office of the U.S. Trade Representative"}],
            ),
            _result(
                "S2",
                title="Local park notice",
                abstract="camping fees",
                agencies=[{"name": "Department of the Interior"}],
            ),
        ]
        return httpx.Response(200, json=_page(results, total_pages=1))

    return _client_with(handler)


@pytest.mark.asyncio
async def test_service_fetch_only_no_writes(repo):
    service = RegisterService(repo, client=_fake_docs_client())
    summary = await service.run(start=date(2026, 9, 1), end=date(2026, 9, 25), fetch_only=True)
    assert summary.fetched == 1  # only the tariff doc passes prefilter
    assert summary.pages == 1
    assert summary.request_count == 1
    assert summary.inserted == 0
    assert await repo.count(RAW_ITEMS) == 0


@pytest.mark.asyncio
async def test_service_ingest_only_writes_raw_only(repo):
    service = RegisterService(repo, client=_fake_docs_client())
    summary = await service.run(start=date(2026, 9, 1), end=date(2026, 9, 25), ingest_only=True)
    assert summary.inserted == 1
    assert await repo.count(RAW_ITEMS) == 1
    # ingest-only must not create evidence
    assert await repo.count(EVIDENCE) == 0


@pytest.mark.asyncio
async def test_service_ingest_only_dedupes(repo):
    service = RegisterService(repo, client=_fake_docs_client())
    await service.run(start=date(2026, 9, 1), end=date(2026, 9, 25), ingest_only=True)
    summary2 = await service.run(start=date(2026, 9, 1), end=date(2026, 9, 25), ingest_only=True)
    assert summary2.duplicates == 1
    assert summary2.inserted == 0
    assert await repo.count(RAW_ITEMS) == 1


@pytest.mark.asyncio
async def test_service_full_pipeline(seeded_repo):
    service = RegisterService(seeded_repo, client=_fake_docs_client())
    summary = await service.run(start=date(2026, 9, 1), end=date(2026, 9, 25))
    assert summary.inserted == 1
    assert summary.accepted == 1
    assert await seeded_repo.count(EVIDENCE) >= 1
    # metadata should be preserved on the raw item
    raw = await seeded_repo.find(RAW_ITEMS, {})
    fr = [r for r in raw if r.get("source") == "federal_register"]
    assert fr and fr[0]["metadata"]["document_number"] == "S1"


@pytest.mark.asyncio
async def test_service_records_fetch_error(repo):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={})

    service = RegisterService(repo, client=_client_with(handler))
    summary = await service.run(start=date(2026, 9, 1), end=date(2026, 9, 25), fetch_only=True)
    assert summary.errors
    assert summary.fetched == 0
