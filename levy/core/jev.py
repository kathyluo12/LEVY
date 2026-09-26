"""Jev decision-model adapter (TypeSafe ``typesafe/jev-1.13`` via OpenRouter).

Uses the Decisions API endpoint ``POST /api/alpha/decisions`` (not chat
completions). Provides typed normalization for the three question types:

* ``noul``  -> probability of "yes" in [0, 1]
* ``choice`` -> {"choice": <key>, "probs": {key: p, ...}}
* ``score``  -> integer level plus a probability distribution

On timeout (>2s, configurable) or error, an injectable fallback is invoked so
tests and offline mode never touch the network. Every call returns raw
probabilities so callers can persist them into ``classifications``.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

JEV_ENDPOINT_PATH = "/api/alpha/decisions"
JEV_MODEL = "typesafe/jev-1.13"

# input price: $0.042 per million input tokens; output tokens free.
JEV_INPUT_PRICE_PER_MTOK = 0.042


@dataclass
class JevResult:
    answers: dict[str, Any]  # normalized answers keyed by question name
    raw: dict[str, Any]  # raw probability payload (for classifications)
    classifier: str  # "jev" | "fallback" | "offline"
    latency_ms: float
    cost_usd: float = 0.0


def _norm_noul(payload: dict[str, Any]) -> float:
    """Normalize a noul answer to P(yes) in [0,1]."""
    if payload is None:
        return 0.5
    for key in ("p", "probability", "p_true", "true", "yes"):
        if key in payload:
            try:
                return _clamp01(float(payload[key]))
            except (TypeError, ValueError):
                continue
    if "probs" in payload and isinstance(payload["probs"], dict):
        return _clamp01(float(payload["probs"].get("true", 0.5)))
    return 0.5


def _norm_choice(payload: dict[str, Any]) -> dict[str, Any]:
    probs: dict[str, float] = {}
    if isinstance(payload.get("probs"), dict):
        probs = {k: _clamp01(float(v)) for k, v in payload["probs"].items()}
    choice = payload.get("choice") or payload.get("selected")
    if choice is None and probs:
        choice = max(probs, key=lambda key: probs[key])
    return {"choice": choice, "probs": probs}


def _norm_score(payload: dict[str, Any], n_levels: int) -> dict[str, Any]:
    dist: list[float] = []
    if isinstance(payload.get("distribution"), list):
        dist = [_clamp01(float(x)) for x in payload["distribution"]]
    level = payload.get("level")
    if level is None:
        if dist:
            level = max(range(len(dist)), key=lambda i: dist[i])
        elif "score" in payload:
            level = int(round(float(payload["score"])))
        else:
            level = 0
    return {"level": int(level), "distribution": dist, "n_levels": n_levels}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def normalize_answer(qtype: str, payload: dict[str, Any], *, criteria: Any = None) -> Any:
    if qtype == "noul":
        return _norm_noul(payload)
    if qtype == "choice":
        return _norm_choice(payload)
    if qtype == "score":
        n = len(criteria) if isinstance(criteria, list) else 5
        return _norm_score(payload, n)
    raise ValueError(f"unknown Jev question type: {qtype!r}")


# --- Offline deterministic classifier --------------------------------------

_TARIFF_TERMS = (
    "tariff",
    "trade",
    "section 301",
    "section 232",
    "section 338",
    "section 122",
    "ieepa",
    "duty",
    "duties",
    "customs",
    "ustr",
    "import",
    "quota",
)
_ESCALATION_TERMS = ("raise", "increase", "impose", "new tariff", "escalat")
_DEESCALATION_TERMS = ("remove", "lower", "reduce", "suspend", "truce", "deal", "agreement")
_DELAY_TERMS = ("delay", "extend", "postpone", "pause")

_COUNTRY_MAP = {
    "china": "CHN",
    "canada": "CAN",
    "mexico": "MEX",
    "eu": "EU",
    "european union": "EU",
    "korea": "KOR",
    "japan": "JPN",
}


def _stable_unit(text: str, salt: str = "") -> float:
    h = hashlib.sha256((salt + "::" + text).encode("utf-8")).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def offline_classify(state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    """Deterministic classifier used offline and as the ultimate fallback."""
    item = state.get("item", {}) if isinstance(state, dict) else {}
    text = f"{item.get('title', '')} {item.get('text', '')}".lower()
    raw: dict[str, Any] = {}

    for name, q in questions.items():
        qtype = q.get("type")
        if qtype == "noul":
            if name == "relevant" or "tariff" in q.get("instructions", "").lower():
                hits = sum(1 for t in _TARIFF_TERMS if t in text)
                p = _clamp01(0.15 + 0.2 * hits)
            elif name in ("new", "novel"):
                p = 0.7  # assume materially new by default offline
            else:
                p = _clamp01(0.4 + 0.4 * _stable_unit(text, name))
            raw[name] = {"p": p, "probs": {"true": p, "false": 1 - p}}
        elif qtype == "choice":
            criteria = q.get("criteria", {})
            keys = list(criteria.keys()) if isinstance(criteria, dict) else []
            probs = _authority_or_direction_probs(name, text, keys)
            raw[name] = {
                "probs": probs,
                "choice": max(probs, key=lambda key: probs[key]) if probs else None,
            }
        elif qtype == "score":
            criteria = q.get("criteria", [])
            n = len(criteria) if isinstance(criteria, list) else 5
            hits = sum(1 for t in _TARIFF_TERMS if t in text)
            level = min(n - 1, hits)
            dist = [0.05] * n
            if 0 <= level < n:
                dist[level] = 1.0 - 0.05 * (n - 1)
            raw[name] = {"level": level, "distribution": dist}
    return raw


def _authority_or_direction_probs(name: str, text: str, keys: list[str]) -> dict[str, float]:
    probs = {k: 0.05 for k in keys}
    if not keys:
        return probs
    if "authority" in name or set(keys) & {"s301", "s232", "s338", "s122"}:
        mapping = {
            "s301": "301",
            "s232": "232",
            "s338": "338",
            "s122": "122",
            "court": "court",
            "deal": "deal",
            "ieepa": "ieepa",
        }
        for k in keys:
            token = mapping.get(k, k)
            if token in text:
                probs[k] = 0.8
    elif "direction" in name or set(keys) & {"escalation", "de_escalation", "delay"}:
        if any(t in text for t in _DEESCALATION_TERMS):
            _bump(probs, "de_escalation")
        elif any(t in text for t in _DELAY_TERMS):
            _bump(probs, "delay")
        elif any(t in text for t in _ESCALATION_TERMS):
            _bump(probs, "escalation")
        else:
            _bump(probs, "neutral")
    else:
        # generic: pick first key deterministically
        probs[keys[0]] = 0.6
    total = sum(probs.values()) or 1.0
    return {k: v / total for k, v in probs.items()}


def _bump(probs: dict[str, float], key: str) -> None:
    if key in probs:
        probs[key] = 0.8


def extract_countries(text: str) -> list[str]:
    text_l = text.lower()
    found = []
    for name, iso in _COUNTRY_MAP.items():
        if name in text_l and iso not in found:
            found.append(iso)
    return found


# --- The adapter ------------------------------------------------------------

FallbackFn = Callable[[dict[str, Any], dict[str, Any]], Awaitable[dict[str, Any]]]


async def _default_fallback(state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
    return offline_classify(state, questions)


@dataclass
class JevAdapter:
    settings: Any = None
    timeout_seconds: float = 2.0
    fallback: Optional[FallbackFn] = None
    http_client: Any = None  # injectable httpx.AsyncClient for tests
    offline: bool = True
    model: str = JEV_MODEL
    api_key: str = ""
    base_url: str = "https://openrouter.ai"

    def __post_init__(self) -> None:
        if self.settings is not None:
            self.offline = self.settings.offline
            self.timeout_seconds = self.settings.jev_timeout_seconds
            self.model = self.settings.jev_model
            self.api_key = self.settings.openrouter_api_key
            self.base_url = self.settings.openrouter_base_url
        if self.fallback is None:
            self.fallback = _default_fallback

    @property
    def _has_key(self) -> bool:
        return bool(self.api_key)

    async def decide(self, state: dict[str, Any], questions: dict[str, Any]) -> JevResult:
        """Ask Jev all questions for one item. Returns normalized + raw answers."""
        start = time.perf_counter()

        if self.offline:
            raw = offline_classify(state, questions)
            return self._finalize(raw, state, questions, "offline", start)

        # Atlas/online mode but no provider key: use deterministic fallback
        # rather than attempting a network call that would fail. Label the
        # classifier honestly as "fallback".
        if not self._has_key:
            fallback = self.fallback or _default_fallback
            raw = await fallback(state, questions)
            return self._finalize(raw, state, questions, "fallback", start)

        try:
            raw = await asyncio.wait_for(
                self._call_api(state, questions), timeout=self.timeout_seconds
            )
            return self._finalize(raw, state, questions, "jev", start)
        except Exception:  # noqa: BLE001 — fallback for timeout or provider errors
            fallback = self.fallback or _default_fallback
            raw = await fallback(state, questions)
            return self._finalize(raw, state, questions, "fallback", start)

    def _finalize(
        self,
        raw: dict[str, Any],
        state: dict[str, Any],
        questions: dict[str, Any],
        classifier: str,
        start: float,
    ) -> JevResult:
        answers: dict[str, Any] = {}
        for name, q in questions.items():
            payload = raw.get(name, {})
            answers[name] = normalize_answer(q["type"], payload, criteria=q.get("criteria"))
        latency_ms = (time.perf_counter() - start) * 1000.0
        cost = self._estimate_cost(state, questions)
        return JevResult(
            answers=answers, raw=raw, classifier=classifier, latency_ms=latency_ms, cost_usd=cost
        )

    def _estimate_cost(self, state: dict[str, Any], questions: dict[str, Any]) -> float:
        approx_tokens = (len(str(state)) + len(str(questions))) / 4.0
        return approx_tokens / 1_000_000.0 * JEV_INPUT_PRICE_PER_MTOK

    async def _call_api(self, state: dict[str, Any], questions: dict[str, Any]) -> dict[str, Any]:
        client = self.http_client
        close_client = False
        if client is None:
            import httpx  # lazy import

            client = httpx.AsyncClient(base_url=self.base_url)
            close_client = True
        try:
            resp = await client.post(
                JEV_ENDPOINT_PATH,
                json={"model": self.model, "state": state, "questions": questions},
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get("answers", data)
        finally:
            if close_client:
                await client.aclose()
