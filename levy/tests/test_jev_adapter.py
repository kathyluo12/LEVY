"""test_jev_adapter: typed normalization + timeout fallback."""

from __future__ import annotations

import asyncio

import pytest

from levy.core.jev import (
    JEV_ENDPOINT_PATH,
    JEV_MODEL,
    JevAdapter,
    normalize_answer,
)

QUESTIONS = {
    "relevant": {
        "type": "noul",
        "instructions": "Does item concern U.S. tariffs?",
        "criteria": {"true": "tariff", "false": "not"},
    },
    "authority": {
        "type": "choice",
        "instructions": "Which authority?",
        "criteria": {"s301": "301", "s338": "338", "other": "other"},
    },
    "materiality": {
        "type": "score",
        "instructions": "How material?",
        "criteria": ["none", "minor", "relevant", "important", "decisive"],
    },
}


def test_normalize_noul_bounds():
    assert normalize_answer("noul", {"p": 1.5}) == 1.0
    assert normalize_answer("noul", {"p": -0.2}) == 0.0
    assert normalize_answer("noul", {"probs": {"true": 0.7}}) == 0.7


def test_normalize_choice_picks_max():
    out = normalize_answer("choice", {"probs": {"a": 0.2, "b": 0.8}})
    assert out["choice"] == "b"
    assert out["probs"]["b"] == 0.8


def test_normalize_score_levels():
    out = normalize_answer("score", {"distribution": [0.1, 0.2, 0.7]}, criteria=["x", "y", "z"])
    assert out["level"] == 2
    assert out["n_levels"] == 3


@pytest.mark.asyncio
async def test_offline_returns_typed_fields():
    adapter = JevAdapter(offline=True)
    state = {"item": {"title": "USTR Section 301 tariff on China", "text": "new tariff duty"}}
    result = await adapter.decide(state, QUESTIONS)
    assert result.classifier == "offline"
    assert 0.0 <= result.answers["relevant"] <= 1.0
    assert result.answers["authority"]["choice"] in ("s301", "s338", "other")
    assert isinstance(result.answers["materiality"]["level"], int)
    # raw probabilities logged for classifications
    assert "relevant" in result.raw


@pytest.mark.asyncio
async def test_timeout_triggers_fallback():
    async def slow_fallback(state, questions):
        return {"relevant": {"p": 0.99}}

    class SlowClient:
        async def post(self, *args, **kwargs):
            await asyncio.sleep(5)  # exceeds timeout

        async def aclose(self):
            pass

    class _S:
        offline = False
        jev_timeout_seconds = 0.05
        jev_model = JEV_MODEL
        openrouter_base_url = "https://openrouter.ai"
        openrouter_api_key = "x"

    adapter = JevAdapter(settings=_S(), fallback=slow_fallback, http_client=SlowClient())
    result = await adapter.decide(
        {"item": {"title": "t", "text": "x"}}, {"relevant": QUESTIONS["relevant"]}
    )
    assert result.classifier == "fallback"
    assert result.answers["relevant"] == 0.99


def test_endpoint_and_model_pinned():
    assert JEV_ENDPOINT_PATH == "/api/alpha/decisions"
    assert JEV_MODEL == "typesafe/jev-1.13"
