"""OpenRouter chat adapter.

Features:
* Role-based model routing (analyst, forecaster, supervisor, red_team, reflector).
* Strict JSON output parsing with schema-shaped fallbacks.
* Retries with exponential backoff.
* Per-question token/cost budget accounting via :class:`BudgetTracker`.
* Deterministic offline responses so tests/offline mode never hit the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
from dataclasses import dataclass
from typing import Any, Optional

# Role -> model routing table (2-3 different providers as per the TDD).
ROLE_MODELS: dict[str, str] = {
    "analyst": "anthropic/claude-3.5-sonnet",
    "forecaster_a": "anthropic/claude-3.5-sonnet",
    "forecaster_b": "openai/gpt-4o",
    "forecaster_c": "google/gemini-1.5-pro",
    "forecaster_d": "openai/gpt-4o-mini",
    "forecaster_e": "mistralai/mistral-large",
    "red_team": "openai/gpt-4o",
    "supervisor": "anthropic/claude-3.5-sonnet",
    "reflector": "anthropic/claude-3.5-sonnet",
    "translation": "openai/gpt-4o-mini",
    "question_factory": "openai/gpt-4o-mini",
}

# Rough per-model price ($/M tokens) input, output — used for cost accounting.
MODEL_PRICES: dict[str, tuple[float, float]] = {
    "anthropic/claude-3.5-sonnet": (3.0, 15.0),
    "openai/gpt-4o": (2.5, 10.0),
    "openai/gpt-4o-mini": (0.15, 0.6),
    "google/gemini-1.5-pro": (1.25, 5.0),
    "mistralai/mistral-large": (2.0, 6.0),
}


@dataclass
class BudgetTracker:
    limit_usd: float = 5.0
    spent_usd: float = 0.0
    tokens_in: int = 0
    tokens_out: int = 0

    def would_exceed(self, cost: float) -> bool:
        return (self.spent_usd + cost) > self.limit_usd

    def charge(self, cost: float, tin: int, tout: int) -> None:
        self.spent_usd += cost
        self.tokens_in += tin
        self.tokens_out += tout


class BudgetExceeded(Exception):
    pass


@dataclass
class LLMResponse:
    content: dict[str, Any]
    text: str
    model: str
    tokens_in: int
    tokens_out: int
    cost_usd: float
    offline: bool = False


def _price(model: str) -> tuple[float, float]:
    return MODEL_PRICES.get(model, (1.0, 3.0))


def _est_tokens(s: str) -> int:
    return max(1, len(s) // 4)


def _stable_hash_unit(text: str, salt: str = "") -> float:
    h = hashlib.sha256((salt + "|" + text).encode()).hexdigest()
    return int(h[:8], 16) / 0xFFFFFFFF


def offline_json_response(role: str, prompt: str, schema: Optional[dict]) -> dict[str, Any]:
    """Deterministic JSON response shaped by role and (optional) schema."""
    u = _stable_hash_unit(prompt, role)

    if role.startswith("forecaster") or role == "red_team":
        p = round(0.2 + 0.6 * u, 4)
        return {
            "probability": p,
            "buckets": {"0pct": round(1 - p, 4), "25pct": 0.0, "50pct": round(p, 4)},
            "evidence_ids": [],
            "rationale": f"[offline {role}] deterministic estimate {p}",
        }
    if role == "supervisor":
        p = round(0.25 + 0.5 * u, 4)
        return {
            "probability": p,
            "rationale": "[offline supervisor] Reconciled ensemble around the median.",
            "evidence_ids": [],
        }
    if role == "reflector":
        return {
            "lesson": "[offline] Deadline extensions of 3 days or less usually precede implementation.",
            "applies_to": {"authority": "s338", "type": "removal"},
            "supported": True,
        }
    if role == "question_factory":
        return {
            "text": "[offline] Will the measure be implemented by the stated deadline?",
            "outcomes": ["YES", "NO"],
        }
    if role.startswith("analyst") or role in ("legal", "political", "counterparty", "base_rate"):
        return {
            "brief": f"[offline {role}] Structured brief citing available evidence.",
            "evidence_ids": [],
        }
    # generic
    if schema and "properties" in schema:
        out: dict[str, Any] = {}
        for k, spec in schema["properties"].items():
            t = spec.get("type")
            out[k] = 0.5 if t == "number" else ([] if t == "array" else "")
        return out
    return {"text": f"[offline {role}] {prompt[:80]}"}


@dataclass
class LLMClient:
    settings: Any = None
    http_client: Any = None
    offline: bool = True
    max_retries: int = 3

    def __post_init__(self) -> None:
        if self.settings is not None:
            self.offline = self.settings.offline

    def model_for(self, role: str) -> str:
        return ROLE_MODELS.get(role, "openai/gpt-4o-mini")

    async def chat_json(
        self,
        role: str,
        prompt: str,
        *,
        schema: Optional[dict] = None,
        budget: Optional[BudgetTracker] = None,
        system: str = "",
    ) -> LLMResponse:
        model = self.model_for(role)
        tin = _est_tokens(system + prompt)

        if self.offline:
            content = offline_json_response(role, prompt, schema)
            text = json.dumps(content)
            tout = _est_tokens(text)
            cost = self._cost(model, tin, tout)
            self._charge_budget(budget, cost, tin, tout)
            return LLMResponse(content, text, model, tin, tout, cost, offline=True)

        # Budget pre-check with a rough output estimate.
        est_cost = self._cost(model, tin, tin)
        if budget is not None and budget.would_exceed(est_cost):
            raise BudgetExceeded(f"budget {budget.limit_usd} would be exceeded by role {role}")

        last_exc: Optional[Exception] = None
        for attempt in range(self.max_retries):
            try:
                data = await self._call(model, system, prompt)
                text = data["choices"][0]["message"]["content"]
                content = _parse_json(text)
                usage = data.get("usage", {})
                tin = usage.get("prompt_tokens", tin)
                tout = usage.get("completion_tokens", _est_tokens(text))
                cost = self._cost(model, tin, tout)
                self._charge_budget(budget, cost, tin, tout)
                return LLMResponse(content, text, model, tin, tout, cost)
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                await asyncio.sleep(min(2**attempt * 0.1, 2.0))
        raise RuntimeError(f"LLM call failed after {self.max_retries} retries: {last_exc}")

    def _charge_budget(self, budget, cost, tin, tout) -> None:
        if budget is not None:
            budget.charge(cost, tin, tout)

    def _cost(self, model: str, tin: int, tout: int) -> float:
        pin, pout = _price(model)
        return tin / 1_000_000 * pin + tout / 1_000_000 * pout

    async def _call(self, model: str, system: str, prompt: str) -> dict[str, Any]:
        client = self.http_client
        close = False
        if client is None:
            import httpx

            client = httpx.AsyncClient(base_url=self.settings.openrouter_base_url, timeout=30.0)
            close = True
        try:
            messages = []
            if system:
                messages.append({"role": "system", "content": system})
            messages.append({"role": "user", "content": prompt})
            resp = await client.post(
                "/api/v1/chat/completions",
                json={
                    "model": model,
                    "messages": messages,
                    "response_format": {"type": "json_object"},
                },
                headers={"Authorization": f"Bearer {self.settings.openrouter_api_key}"},
            )
            resp.raise_for_status()
            return resp.json()
        finally:
            if close:
                await client.aclose()


def _parse_json(text: str) -> dict[str, Any]:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                return json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                pass
    return {"_unparsed": text}
