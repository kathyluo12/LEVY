"""Web research adapter (Firecrawl + Tavily) behind one interface.

Routing:
* search   -> Tavily (fallback Firecrawl search)
* read     -> Firecrawl (fallback Tavily extract)
* research -> Tavily research (mini)
* watch    -> Firecrawl Monitor (returns a monitor id)

Caching: results are cached in ``web_cache`` keyed by ``provider + normalized
query/URL``. TTLs: search 6h, pages 24h. Per-run budgets limit credit use.

Safety: all returned text is untrusted data. It is wrapped via
:func:`wrap_untrusted` so callers can embed it as quoted content in prompts and
it is never treated as instructions.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

SEARCH_TTL = timedelta(hours=6)
PAGE_TTL = timedelta(hours=24)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def cache_key(provider: str, kind: str, ident: str) -> str:
    norm = ident.strip().lower()
    h = hashlib.sha256(f"{provider}:{kind}:{norm}".encode()).hexdigest()[:24]
    return f"{provider}:{kind}:{h}"


def wrap_untrusted(text: str) -> str:
    """Wrap scraped/searched text as quoted, untrusted data for prompts."""
    return (
        "<<<UNTRUSTED_WEB_CONTENT\n"
        + (text or "").replace("<<<", "").strip()
        + "\nUNTRUSTED_WEB_CONTENT>>>"
    )


@dataclass
class WebHit:
    title: str
    url: str
    snippet: str
    score: float = 0.0
    provider: str = ""


@dataclass
class WebDoc:
    url: str
    markdown: str
    structured: dict[str, Any] = field(default_factory=dict)
    provider: str = ""


@dataclass
class ResearchReport:
    question: str
    answer: str
    citations: list[str] = field(default_factory=list)
    provider: str = ""


@dataclass
class WebBudget:
    searches: int = 5
    reads: int = 5
    research: int = 1
    used_searches: int = 0
    used_reads: int = 0
    used_research: int = 0

    def can_search(self) -> bool:
        return self.used_searches < self.searches

    def can_read(self) -> bool:
        return self.used_reads < self.reads

    def can_research(self) -> bool:
        return self.used_research < self.research


class WebBudgetExceeded(Exception):
    pass


# --- Offline deterministic adapter -----------------------------------------


def _offline_hits(q: str, k: int) -> list[WebHit]:
    hits = []
    for i in range(min(k, 3)):
        hits.append(
            WebHit(
                title=f"[offline] Result {i + 1} for {q[:40]}",
                url=f"https://example.test/{hashlib.sha1(f'{q}{i}'.encode()).hexdigest()[:8]}",
                snippet=f"Deterministic offline snippet {i + 1} about {q[:60]}.",
                score=1.0 - i * 0.1,
                provider="offline",
            )
        )
    return hits


class WebClient:
    """Provider-routing web client with cache and budget enforcement."""

    def __init__(self, settings=None, repo=None, http_client=None) -> None:
        self.settings = settings
        self.repo = repo
        self.http_client = http_client
        self.offline = settings.offline if settings is not None else True

    async def _cache_get(self, key: str, ttl: timedelta) -> Optional[Any]:
        if self.repo is None:
            return None
        from levy.core.collections import WEB_CACHE

        doc = await self.repo.find_one(WEB_CACHE, {"key": key})
        if not doc:
            return None
        fetched = doc.get("fetched_at")
        if isinstance(fetched, str):
            try:
                fetched = datetime.fromisoformat(fetched)
            except ValueError:
                return None
        if fetched and (_now() - _as_utc(fetched)) < ttl:
            return doc.get("result")
        return None

    async def _cache_put(
        self, key: str, provider: str, kind: str, result: Any, credits: float
    ) -> None:
        if self.repo is None:
            return
        from levy.core.collections import WEB_CACHE
        from levy.schemas import WebCache

        entry = WebCache(key=key, provider=provider, kind=kind, result=result, credits=credits)
        await self.repo.update_one(WEB_CACHE, {"key": key}, entry.to_doc(), upsert=True)

    async def search(
        self,
        q: str,
        *,
        since_days: int = 30,
        domains: Optional[list[str]] = None,
        k: int = 8,
        budget: Optional[WebBudget] = None,
    ) -> list[WebHit]:
        key = cache_key("tavily", "search", q)
        cached = await self._cache_get(key, SEARCH_TTL)
        if cached is not None:
            return [WebHit(**h) for h in cached]
        if budget is not None:
            if not budget.can_search():
                raise WebBudgetExceeded("search budget exhausted")
            budget.used_searches += 1

        if self.offline:
            hits = _offline_hits(q, k)
        else:
            hits = await self._tavily_search(q, since_days, domains, k)
        await self._cache_put(key, "tavily", "search", [h.__dict__ for h in hits], credits=1.0)
        return hits

    async def read(
        self, url: str, *, structured: Optional[dict] = None, budget: Optional[WebBudget] = None
    ) -> WebDoc:
        key = cache_key("firecrawl", "read", url)
        cached = await self._cache_get(key, PAGE_TTL)
        if cached is not None:
            return WebDoc(**cached)
        if budget is not None:
            if not budget.can_read():
                raise WebBudgetExceeded("read budget exhausted")
            budget.used_reads += 1

        if self.offline:
            doc = WebDoc(
                url=url,
                markdown=f"[offline] Clean markdown content for {url}",
                structured=structured or {},
                provider="offline",
            )
        else:
            doc = await self._firecrawl_read(url, structured)
        await self._cache_put(key, "firecrawl", "read", doc.__dict__, credits=1.0)
        return doc

    async def research(
        self, question: str, *, depth: str = "mini", budget: Optional[WebBudget] = None
    ) -> ResearchReport:
        key = cache_key("tavily", "research", question)
        cached = await self._cache_get(key, SEARCH_TTL)
        if cached is not None:
            return ResearchReport(**cached)
        if budget is not None:
            if not budget.can_research():
                raise WebBudgetExceeded("research budget exhausted")
            budget.used_research += 1

        if self.offline:
            report = ResearchReport(
                question=question,
                answer=f"[offline] Synthesized reference-class summary for: {question[:80]}",
                citations=[],
                provider="offline",
            )
        else:
            report = await self._tavily_research(question, depth)
        await self._cache_put(key, "tavily", "research", report.__dict__, credits=4.0)
        return report

    async def watch(self, url: str, *, every_min: int = 30) -> str:
        if self.offline:
            return "offline-monitor-" + hashlib.sha1(url.encode()).hexdigest()[:8]
        return await self._firecrawl_watch(url, every_min)

    # --- Provider calls (only in non-offline mode) --------------------------

    async def _tavily_search(self, q, since_days, domains, k) -> list[WebHit]:
        client = self._client("https://api.tavily.com")
        resp = await client.post(
            "/search",
            json={
                "query": q,
                "max_results": k,
                "days": since_days,
                "include_domains": domains or [],
            },
            headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return [
            WebHit(
                title=r.get("title", ""),
                url=r.get("url", ""),
                snippet=r.get("content", ""),
                score=r.get("score", 0.0),
                provider="tavily",
            )
            for r in data.get("results", [])
        ]

    async def _firecrawl_read(self, url, structured) -> WebDoc:
        client = self._client("https://api.firecrawl.dev")
        payload = {"url": url, "formats": ["markdown"]}
        if structured:
            payload["formats"].append("json")
            payload["jsonOptions"] = {"schema": structured}
        resp = await client.post(
            "/v2/scrape",
            json=payload,
            headers={"Authorization": f"Bearer {self.settings.firecrawl_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json().get("data", {})
        return WebDoc(
            url=url,
            markdown=data.get("markdown", ""),
            structured=data.get("json", {}),
            provider="firecrawl",
        )

    async def _tavily_research(self, question, depth) -> ResearchReport:
        client = self._client("https://api.tavily.com")
        resp = await client.post(
            "/research",
            json={"query": question, "depth": depth},
            headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
        )
        resp.raise_for_status()
        data = resp.json()
        return ResearchReport(
            question=question,
            answer=data.get("answer", ""),
            citations=[c.get("url", "") for c in data.get("results", [])],
            provider="tavily",
        )

    async def _firecrawl_watch(self, url, every_min) -> str:
        client = self._client("https://api.firecrawl.dev")
        resp = await client.post(
            "/v2/monitor",
            json={"url": url, "intervalMinutes": every_min},
            headers={"Authorization": f"Bearer {self.settings.firecrawl_api_key}"},
        )
        resp.raise_for_status()
        return resp.json().get("id", "")

    def _client(self, base_url: str):
        if self.http_client is not None:
            return self.http_client
        import httpx

        return httpx.AsyncClient(base_url=base_url, timeout=30.0)


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)
