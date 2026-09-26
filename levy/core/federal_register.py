"""Federal Register live ingestion client.

Production-grade async client for the public Federal Register API
(https://www.federalregister.gov/api/v1). No API key is required. The client:

* Uses ``httpx`` with an injectable client/base URL/clock for tests.
* Identifies itself with a descriptive ``User-Agent`` including a configurable
  contact string, per the Federal Register API etiquette.
* Retries timeouts, network errors, HTTP 429 and 5xx with bounded exponential
  backoff, honouring ``Retry-After`` up to a cap. Other 4xx fail clearly.
* Clamps ``per_page`` (<= 1000), ``max_pages`` (<= 10) and ``max_documents``
  (<= 500) to safe bounds and never follows an arbitrary ``next_page_url``
  host — it drives pagination with explicit page numbers against the configured
  base URL only.
* Excludes future-dated documents by default and bounds the ingestion window.
* Normalizes HTML snippets to plain text and combines title + abstract +
  excerpts into a bounded text blob. It can optionally fetch document detail /
  raw text under an explicit ``include_full_text`` flag with byte and document
  caps. Fetched text is treated strictly as data; no scraped instruction is
  ever executed.
* Normalizes each result into the existing Pipeline document shape with
  ``source="federal_register"``, the canonical ``html_url`` and a stable content
  hash derived from ``document_number`` so a re-published/updated abstract does
  not create a duplicate raw item.
* Applies a documented tariff/trade prefilter (terms + agencies) that is broad
  enough to retain genuine tariff/trade actions and antidumping/countervailing
  notices, while remaining independently testable.
"""

from __future__ import annotations

import asyncio
import hashlib
import html
import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any, Callable, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

log = logging.getLogger("levy.federal_register")

# --- Bounds (hard caps enforced regardless of caller input) ----------------

MAX_PER_PAGE = 1000
MAX_PAGES = 10
MAX_DOCUMENTS = 500
DEFAULT_PER_PAGE = 100
DEFAULT_MAX_PAGES = 3
DEFAULT_MAX_DOCUMENTS = 100

# Cap on how long we will wait for a single Retry-After hint (seconds).
RETRY_AFTER_CAP_SECONDS = 30.0
# Base backoff and its cap for exponential retry (seconds).
BACKOFF_BASE_SECONDS = 0.5
BACKOFF_CAP_SECONDS = 30.0

# Bound on the combined title+abstract+excerpts text blob (characters).
MAX_TEXT_CHARS = 8000
# Character cap when optionally fetching full/raw text per document.
MAX_FULL_TEXT_CHARS = 200_000

API_DOCUMENTS_PATH = "/api/v1/documents.json"
API_DOCUMENT_DETAIL_PATH = "/api/v1/documents/{document_number}.json"

SOURCE = "federal_register"


# --- Typed models for API responses ----------------------------------------


class Agency(BaseModel):
    """A single agency entry as returned by the API (partial)."""

    name: Optional[str] = None
    id: Optional[int] = None
    raw_name: Optional[str] = None


class FederalRegisterResult(BaseModel):
    """A single document result from the search endpoint."""

    document_number: str
    title: str = ""
    abstract: Optional[str] = None
    excerpts: Optional[str] = None
    agencies: list[Agency] = Field(default_factory=list)
    type: Optional[str] = None
    publication_date: Optional[str] = None
    html_url: Optional[str] = None
    pdf_url: Optional[str] = None

    def agency_names(self) -> list[str]:
        names: list[str] = []
        for a in self.agencies:
            name = a.name or a.raw_name
            if name:
                names.append(name)
        return names


class FederalRegisterPage(BaseModel):
    """A single page of search results."""

    count: int = 0
    total_pages: int = 0
    next_page_url: Optional[str] = None
    results: list[FederalRegisterResult] = Field(default_factory=list)


class FederalRegisterDetail(BaseModel):
    """Selected fields from the document detail endpoint."""

    document_number: str
    raw_text_url: Optional[str] = None
    body_html_url: Optional[str] = None
    full_text_xml_url: Optional[str] = None
    topics: list[str] = Field(default_factory=list)
    effective_on: Optional[str] = None


# --- HTML / text normalization ---------------------------------------------

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: Optional[str]) -> str:
    """Safely reduce an HTML snippet to plain text.

    Removes tags and unescapes entities. The result is treated purely as text
    data; nothing in it is ever interpreted as an instruction.
    """
    if not value:
        return ""
    # Drop script/style blocks entirely before removing remaining tags.
    without_blocks = re.sub(
        r"<(script|style)\b[^>]*>.*?</\1>", " ", value, flags=re.IGNORECASE | re.DOTALL
    )
    no_tags = _TAG_RE.sub(" ", without_blocks)
    unescaped = html.unescape(no_tags)
    return _WS_RE.sub(" ", unescaped).strip()


def combine_text(result: FederalRegisterResult, *, max_chars: int = MAX_TEXT_CHARS) -> str:
    """Combine title, abstract and excerpts into a bounded plain-text blob."""
    parts = [
        strip_html(result.title),
        strip_html(result.abstract),
        strip_html(result.excerpts),
    ]
    blob = "\n\n".join(p for p in parts if p)
    if len(blob) > max_chars:
        blob = blob[:max_chars].rstrip()
    return blob


# --- Tariff/trade prefilter -------------------------------------------------

# Documented term list. Kept deliberately broad to retain genuine tariff/trade
# actions and trade-remedy notices (antidumping / countervailing duties), while
# still filtering out the large volume of unrelated Federal Register content.
TARIFF_TERMS: tuple[str, ...] = (
    "tariff",
    "tariffs",
    "duty",
    "duties",
    "customs",
    "import",
    "imports",
    "quota",
    "trade",
    "antidumping",
    "anti-dumping",
    "countervailing",
    "safeguard",
    "section 301",
    "section 232",
    "section 338",
    "section 122",
    "ieepa",
    "harmonized tariff",
    "hts",
    "trade remedy",
    "trade agreement",
    "de minimis",
    "column 2",
)

# Documented agency substrings. These are the agencies that issue tariff/trade
# actions and trade-remedy determinations.
TARIFF_AGENCIES: tuple[str, ...] = (
    "trade representative",  # USTR
    "u.s. trade representative",
    "international trade administration",
    "international trade commission",
    "commerce",  # Department of Commerce (AD/CVD)
    "customs and border protection",
    "homeland security",  # parent of CBP
    "treasury",
)


def is_tariff_relevant(
    result: FederalRegisterResult,
    *,
    terms: tuple[str, ...] = TARIFF_TERMS,
    agencies: tuple[str, ...] = TARIFF_AGENCIES,
) -> bool:
    """Return True if a result looks like a tariff/trade action.

    A document matches if any documented term appears in its title/abstract/
    excerpts/type, OR if it was issued by a documented tariff/trade agency.
    Pure, side-effect free and independently testable.
    """
    haystack = " ".join(
        [
            result.title or "",
            result.abstract or "",
            result.excerpts or "",
            result.type or "",
        ]
    ).lower()
    for term in terms:
        if term in haystack:
            return True
    agency_blob = " ".join(result.agency_names()).lower()
    for agency in agencies:
        if agency in agency_blob:
            return True
    return False


# --- Errors -----------------------------------------------------------------


class FederalRegisterError(RuntimeError):
    """Raised for non-retryable client errors (e.g. unexpected 4xx)."""


# --- Date helpers -----------------------------------------------------------


def _as_date(value: Any) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None
    return None


def _publication_datetime(value: Optional[str]) -> datetime:
    d = _as_date(value)
    if d is None:
        return datetime.now(timezone.utc)
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)


def _stable_hash(document_number: str) -> str:
    """Stable content hash keyed only on the document number.

    Using the document number (not the abstract text) means a later republish
    with an edited abstract dedupes against the first ingest rather than
    creating a second raw item.
    """
    return hashlib.sha256(f"{SOURCE}|{document_number}".encode()).hexdigest()


# --- The client -------------------------------------------------------------


@dataclass
class FederalRegisterClient:
    """Async client for the Federal Register documents API."""

    base_url: str = "https://www.federalregister.gov"
    user_agent: str = "LEVY-TariffDesk/1.0"
    contact: str = "levy-ops@example.com"
    default_query: str = "tariff"
    timeout_seconds: float = 20.0
    max_retries: int = 3
    http_client: Any = None  # injectable httpx.AsyncClient for tests
    clock: Callable[[], datetime] = field(
        default_factory=lambda: (lambda: datetime.now(timezone.utc))
    )
    # Injectable async sleep so tests can avoid real backoff delays.
    sleep: Callable[[float], Any] = field(default_factory=lambda: asyncio.sleep)
    settings: Any = None

    # Populated per-run for observability.
    request_count: int = 0
    pages_fetched: int = 0

    def __post_init__(self) -> None:
        if self.settings is not None:
            self.base_url = self.settings.federal_register_base_url
            self.user_agent = self.settings.federal_register_user_agent
            self.contact = self.settings.federal_register_contact
            self.default_query = self.settings.federal_register_query
            self.timeout_seconds = self.settings.federal_register_timeout_seconds
            self.max_retries = self.settings.federal_register_max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": f"{self.user_agent} (contact: {self.contact})",
            "Accept": "application/json",
        }

    async def _request_json(self, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        """GET a JSON resource with bounded retry/backoff.

        Retries on timeouts, network errors, 429 and 5xx. Honours ``Retry-After``
        up to a cap. Other 4xx raise :class:`FederalRegisterError`.
        """
        import httpx

        client = self.http_client
        close_client = False
        if client is None:
            client = httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout_seconds)
            close_client = True

        try:
            attempt = 0
            while True:
                self.request_count += 1
                try:
                    resp = await client.get(path, params=params, headers=self._headers())
                except (httpx.TimeoutException, httpx.TransportError) as exc:
                    if attempt >= self.max_retries:
                        raise FederalRegisterError(
                            f"network error after {attempt} retries: {exc}"
                        ) from exc
                    await self._backoff(attempt, None)
                    attempt += 1
                    continue

                status = resp.status_code
                if status == 200:
                    return resp.json()
                if status == 429 or 500 <= status < 600:
                    if attempt >= self.max_retries:
                        raise FederalRegisterError(
                            f"server returned {status} after {attempt} retries"
                        )
                    await self._backoff(attempt, resp.headers.get("Retry-After"))
                    attempt += 1
                    continue
                # Other 4xx: fail clearly and do not retry.
                raise FederalRegisterError(f"unexpected HTTP {status} for {path}")
        finally:
            if close_client:
                await client.aclose()

    async def _backoff(self, attempt: int, retry_after: Optional[str]) -> None:
        delay = min(BACKOFF_BASE_SECONDS * (2**attempt), BACKOFF_CAP_SECONDS)
        if retry_after:
            try:
                delay = min(float(retry_after), RETRY_AFTER_CAP_SECONDS)
            except (TypeError, ValueError):
                pass
        await self.sleep(delay)

    async def fetch_page(
        self,
        *,
        query: str,
        start: date,
        end: date,
        per_page: int,
        page: int,
    ) -> FederalRegisterPage:
        """Fetch a single page of search results (page numbers only)."""
        params = {
            "conditions[term]": query,
            "conditions[publication_date][gte]": start.isoformat(),
            "conditions[publication_date][lte]": end.isoformat(),
            "per_page": min(max(1, per_page), MAX_PER_PAGE),
            "page": max(1, page),
            "order": "newest",
        }
        data = await self._request_json(API_DOCUMENTS_PATH, params)
        result = FederalRegisterPage.model_validate(data)
        self.pages_fetched += 1
        return result

    async def fetch_detail(self, document_number: str) -> FederalRegisterDetail:
        path = API_DOCUMENT_DETAIL_PATH.format(document_number=document_number)
        data = await self._request_json(path)
        # Ensure document_number is present for validation.
        if isinstance(data, dict):
            data.setdefault("document_number", document_number)
        return FederalRegisterDetail.model_validate(data)

    async def _fetch_full_text(self, detail: FederalRegisterDetail) -> str:
        """Best-effort fetch of official raw text under a character cap.

        ``raw_text_url`` comes from a remote response, so it is treated as
        untrusted. Only credential-free HTTPS URLs on the configured Federal
        Register host are allowed; redirects are not followed by our client.
        """
        url = detail.raw_text_url
        if not url:
            return ""
        candidate = urlparse(url)
        official = urlparse(self.base_url)
        if (
            candidate.scheme != "https"
            or candidate.hostname != official.hostname
            or candidate.username is not None
            or candidate.password is not None
        ):
            log.warning("skipping untrusted Federal Register raw-text URL")
            return ""
        import httpx

        client = self.http_client
        close_client = False
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds, follow_redirects=False)
            close_client = True
        try:
            self.request_count += 1
            resp = await client.get(url, headers=self._headers(), follow_redirects=False)
            if resp.status_code != 200:
                return ""
            text = resp.text[:MAX_FULL_TEXT_CHARS]
            return strip_html(text) if "<" in text else text.strip()
        except (httpx.TimeoutException, httpx.TransportError):
            return ""
        finally:
            if close_client:
                await client.aclose()

    def normalize(
        self,
        result: FederalRegisterResult,
        *,
        detail: Optional[FederalRegisterDetail] = None,
        full_text: str = "",
    ) -> dict[str, Any]:
        """Normalize an API result into the Pipeline document shape."""
        text = combine_text(result)
        if full_text:
            text = (text + "\n\n" + full_text)[: MAX_TEXT_CHARS + MAX_FULL_TEXT_CHARS]
        metadata: dict[str, Any] = {
            "document_number": result.document_number,
            "agencies": result.agency_names(),
            "type": result.type,
            "pdf_url": result.pdf_url,
        }
        if detail is not None:
            metadata["topics"] = detail.topics
            metadata["effective_on"] = detail.effective_on
        return {
            "source": SOURCE,
            "url": result.html_url or "",
            "title": result.title,
            "text": text,
            "hash": _stable_hash(result.document_number),
            "published_at": _publication_datetime(result.publication_date),
            "metadata": metadata,
        }

    async def fetch_documents(
        self,
        start: date,
        end: date,
        query: Optional[str] = None,
        *,
        per_page: int = DEFAULT_PER_PAGE,
        max_pages: int = DEFAULT_MAX_PAGES,
        max_documents: int = DEFAULT_MAX_DOCUMENTS,
        include_full_text: bool = False,
        exclude_future: bool = True,
        prefilter: bool = True,
    ) -> list[dict[str, Any]]:
        """Fetch, filter and normalize documents in ``[start, end]``.

        Returns Pipeline-shaped documents deduped by ``document_number``.
        Request volume is strictly bounded by the clamped page/document caps.
        """
        if end < start:
            raise ValueError("end date must be on or after start date")

        query = query or self.default_query
        per_page = min(max(1, per_page), MAX_PER_PAGE)
        max_pages = min(max(1, max_pages), MAX_PAGES)
        max_documents = min(max(1, max_documents), MAX_DOCUMENTS)

        today = self.clock().date()
        # Never request beyond today when future exclusion is on.
        effective_end = min(end, today) if exclude_future else end
        if effective_end < start:
            return []

        seen: set[str] = set()
        docs: list[dict[str, Any]] = []

        for page in range(1, max_pages + 1):
            if len(docs) >= max_documents:
                break
            page_data = await self.fetch_page(
                query=query,
                start=start,
                end=effective_end,
                per_page=per_page,
                page=page,
            )
            if not page_data.results:
                break

            for result in page_data.results:
                if len(docs) >= max_documents:
                    break
                if not result.document_number or result.document_number in seen:
                    continue
                if exclude_future:
                    pub = _as_date(result.publication_date)
                    if pub is not None and pub > today:
                        continue
                if prefilter and not is_tariff_relevant(result):
                    continue
                seen.add(result.document_number)

                detail: Optional[FederalRegisterDetail] = None
                full_text = ""
                if include_full_text:
                    try:
                        detail = await self.fetch_detail(result.document_number)
                        full_text = await self._fetch_full_text(detail)
                    except FederalRegisterError as exc:
                        log.warning("detail fetch failed for %s: %s", result.document_number, exc)

                docs.append(self.normalize(result, detail=detail, full_text=full_text))

            # Stop early if we've consumed all available pages.
            if page >= page_data.total_pages:
                break

        return docs
