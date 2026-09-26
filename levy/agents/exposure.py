"""Exposure mapper: sectors, FX and tickers weighted by tariff probability.

Fires on a new belief. Computes probability-weighted sector/FX/ticker exposure
and a market-divergence figure (LEVY p_calibrated vs latest market tick).
"""

from __future__ import annotations

from typing import Any, Optional

from levy.core.collections import EXPOSURES, MARKET_TICKS
from levy.core.db import Repository
from levy.schemas import Exposure

# Static exposure fixtures by country (sector weights, FX pairs, tickers).
COUNTRY_EXPOSURE: dict[str, dict[str, Any]] = {
    "CAN": {
        "sectors": [
            {"name": "autos", "weight": 0.35},
            {"name": "energy", "weight": 0.25},
            {"name": "aluminum", "weight": 0.20},
            {"name": "agriculture", "weight": 0.20},
        ],
        "fx": [{"pair": "USDCAD", "beta": 0.8}],
        "tickers": [
            {"symbol": "GM", "beta": 0.6},
            {"symbol": "F", "beta": 0.55},
            {"symbol": "MGA", "beta": 0.7},
        ],
    },
    "CHN": {
        "sectors": [
            {"name": "electronics", "weight": 0.40},
            {"name": "machinery", "weight": 0.25},
            {"name": "textiles", "weight": 0.20},
            {"name": "solar", "weight": 0.15},
        ],
        "fx": [{"pair": "USDCNH", "beta": 0.7}],
        "tickers": [
            {"symbol": "AAPL", "beta": 0.4},
            {"symbol": "FXI", "beta": 0.8},
            {"symbol": "TSLA", "beta": 0.5},
        ],
    },
    "MEX": {
        "sectors": [{"name": "autos", "weight": 0.5}, {"name": "agriculture", "weight": 0.5}],
        "fx": [{"pair": "USDMXN", "beta": 0.75}],
        "tickers": [{"symbol": "GM", "beta": 0.5}],
    },
    "KOR": {
        "sectors": [{"name": "semiconductors", "weight": 0.6}, {"name": "autos", "weight": 0.4}],
        "fx": [{"pair": "USDKRW", "beta": 0.65}],
        "tickers": [{"symbol": "SSNLF", "beta": 0.5}],
    },
}


class ExposureMapper:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def map_belief(
        self, belief: dict[str, Any], question: dict[str, Any]
    ) -> Optional[Exposure]:
        country = question.get("country", "")
        base = COUNTRY_EXPOSURE.get(country)
        if not base:
            return None
        p = belief.get("p_calibrated", belief.get("p_raw", 0.5))

        sectors = [{**s, "shock": round(s["weight"] * p, 4)} for s in base["sectors"]]
        fx = [{**f, "expected_move": round(f["beta"] * p, 4)} for f in base["fx"]]
        tickers = [{**t, "shock": round(t["beta"] * p, 4)} for t in base["tickers"]]

        divergence = await self._market_divergence(question, p)

        exposure = Exposure(
            country=country,
            question_id=question.get("_id", ""),
            sectors=sectors,
            fx=fx,
            tickers=tickers,
            market_divergence=divergence,
        )
        await self.repo.insert(EXPOSURES, exposure.to_doc())
        return exposure

    async def _market_divergence(self, question: dict[str, Any], p: float) -> Optional[float]:
        ticks = await self.repo.find(
            MARKET_TICKS, {"meta.question_key": question.get("key")}, sort=[("ts", -1)], limit=1
        )
        if not ticks:
            return None
        market_p = float(ticks[0].get("value", 0.0))
        return round(p - market_p, 4)
