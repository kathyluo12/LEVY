"""Forecast cell: 5 forecasters + red team + supervisor + calibrator.

Sequence (per the TDD):
1. Load prior belief, briefs, top lessons, market price.
2. Run 5 forecasters in parallel (different models); each returns probability,
   rate-bucket distribution and cited evidence IDs.
3. Red Team argues against the median and lists missed evidence.
4. Supervisor reconciles, writes the rationale, cites evidence IDs only.
5. Jev verifies each cited claim against its evidence; unsupported cited IDs are
   removed and the supervisor retries once.
6. Calibrator applies the current calibration map; confidence grade computed.
7. New append-only belief version is written; stream event emitted.
"""

from __future__ import annotations

import asyncio
import statistics
from typing import Any, Optional

from levy.core.calibrate import model_from_doc, neutral_map
from levy.core.collections import (
    BELIEFS,
    BRIEFS,
    CALIBRATION_MAPS,
    EVIDENCE,
    FORECAST_RUNS,
    MARKET_TICKS,
    SCORES,
)
from levy.core.db import Repository
from levy.core.events import EventBus
from levy.core.jev import JevAdapter
from levy.core.llm import BudgetTracker, LLMClient
from levy.core.memory import recall_lessons
from levy.schemas import Belief, Confidence, ForecastRun

FORECASTER_ROLES = ["forecaster_a", "forecaster_b", "forecaster_c", "forecaster_d", "forecaster_e"]

CONFIDENCE_COMPONENTS = (
    "ensemble_agreement",
    "evidence_quality",
    "structural_clarity",
    "market_agreement",
    "track_record",
    "horizon",
)


def grade_from_score(score: float) -> str:
    if score >= 80:
        return "A"
    if score >= 65:
        return "B"
    if score >= 50:
        return "C"
    return "D"


class ForecastCell:
    def __init__(
        self,
        repo: Repository,
        llm: Optional[LLMClient] = None,
        jev: Optional[JevAdapter] = None,
        bus: Optional[EventBus] = None,
        settings=None,
    ) -> None:
        self.repo = repo
        self.llm = llm or LLMClient(settings=settings)
        self.jev = jev or JevAdapter(settings=settings)
        self.bus = bus

    async def _prior_belief(self, question_id: str) -> Optional[dict[str, Any]]:
        beliefs = await self.repo.find(
            BELIEFS, {"question_id": question_id}, sort=[("version", -1)], limit=1
        )
        return beliefs[0] if beliefs else None

    async def _next_version(self, question_id: str) -> int:
        prior = await self._prior_belief(question_id)
        return (prior["version"] + 1) if prior else 1

    async def _calibration_model(self):
        maps = await self.repo.find(CALIBRATION_MAPS, {}, sort=[("version", -1)], limit=1)
        if maps:
            return model_from_doc(maps[0])
        return neutral_map()

    async def _forecaster_weights(self) -> dict[str, float]:
        return await load_forecaster_weights(self.repo)

    async def run(self, question: dict[str, Any], *, trigger: Optional[dict] = None) -> Belief:
        question_id = question.get("_id", "")
        budget = BudgetTracker(limit_usd=question.get("budget", 5.0))

        evidence = await self.repo.find(EVIDENCE, {"question_ids": question_id})
        evidence_ids = [e.get("_id", "") for e in evidence]
        briefs = await self.repo.find(BRIEFS, {"question_id": question_id})
        lessons = await recall_lessons(
            self.repo, question.get("text", ""), authority=question.get("authority"), k=5
        )
        prior = await self._prior_belief(question_id)
        market = await self._market_price(question)

        brief_blob = " | ".join(b.get("text", "") for b in briefs)
        lesson_blob = " | ".join(ls.get("text", "") for ls in lessons)

        # 2. Five forecasters in parallel.
        weights = await self._forecaster_weights()
        runs = await asyncio.gather(
            *[
                self._forecaster(role, question, brief_blob, lesson_blob, evidence_ids, budget)
                for role in FORECASTER_ROLES
            ]
        )
        probs = [r.p_raw for r in runs]
        median = statistics.median(probs) if probs else 0.5

        # 3. Red team.
        red = await self._red_team(question, median, evidence_ids, budget)

        # 4. Supervisor reconciles (weighted mean of forecasters).
        w_sum = sum(weights.get(r.agent, 1.0) for r in runs) or 1.0
        p_super = sum(r.p_raw * weights.get(r.agent, 1.0) for r in runs) / w_sum
        supervisor_run, cited = await self._supervisor(
            question, p_super, evidence_ids, budget, prior=prior, red_team=red
        )

        # 5. Jev cited-evidence verification (drop unsupported, retry once).
        verified = await self._verify_citations(cited, evidence)
        if len(verified) < len(cited):
            supervisor_run, cited2 = await self._supervisor(
                question, p_super, verified, budget, prior=prior, red_team=red
            )
            verified = await self._verify_citations(cited2, evidence)

        # 6. Calibrate + confidence.
        model = await self._calibration_model()
        p_calibrated = model.apply(p_super)
        components = self._confidence_components(probs, evidence, market, p_super, question)
        conf_score = round(100 * sum(components.values()) / len(components), 1)
        confidence = Confidence(
            grade=grade_from_score(conf_score), score=conf_score, components=components
        )

        buckets = self._buckets(p_super)

        # 7. Append-only belief.
        belief = Belief(
            question_id=question_id,
            version=await self._next_version(question_id),
            p_raw=round(p_super, 4),
            p_calibrated=round(p_calibrated, 4),
            buckets=buckets,
            confidence=confidence,
            rationale=supervisor_run.get("rationale", ""),
            evidence_ids=verified,
            trigger=trigger or {"type": "manual"},
        )
        await self.repo.insert(BELIEFS, belief.to_doc())

        if self.bus is not None:
            await self.bus.publish("belief", belief.to_doc())
        return belief

    async def _forecaster(
        self, role, question, briefs, lessons, evidence_ids, budget
    ) -> ForecastRun:
        prompt = (
            f"Q: {question.get('text','')}\nBriefs: {briefs}\nLessons: {lessons}\n"
            f"Evidence: {evidence_ids}\nReturn probability, buckets, evidence_ids."
        )
        resp = await self.llm.chat_json(role, prompt, budget=budget)
        p = float(resp.content.get("probability", 0.5))
        run = ForecastRun(
            question_id=question.get("_id", ""),
            agent=role,
            model=resp.model,
            p_raw=p,
            buckets=resp.content.get("buckets", self._buckets(p)),
            evidence_ids=resp.content.get("evidence_ids") or evidence_ids[:3],
        )
        await self.repo.insert(FORECAST_RUNS, run.to_doc())
        return run

    async def _red_team(self, question, median, evidence_ids, budget) -> ForecastRun:
        prompt = (
            f"Argue against median={median} for Q: {question.get('text','')}. List missed evidence."
        )
        resp = await self.llm.chat_json("red_team", prompt, budget=budget)
        p = float(resp.content.get("probability", 1 - median))
        run = ForecastRun(
            question_id=question.get("_id", ""),
            agent="red_team",
            model=resp.model,
            p_raw=p,
            buckets=resp.content.get("buckets", self._buckets(p)),
            evidence_ids=resp.content.get("evidence_ids") or evidence_ids[:2],
            is_red_team=True,
        )
        await self.repo.insert(FORECAST_RUNS, run.to_doc())
        return run

    async def _supervisor(
        self, question, p_super, evidence_ids, budget, *, prior=None, red_team=None
    ) -> tuple[dict, list[str]]:
        prior_p = prior.get("p_calibrated") if prior else None
        red_p = red_team.p_raw if red_team else None
        prompt = (
            f"Reconcile ensemble to a final probability near {p_super:.3f} for "
            f"Q: {question.get('text','')}. Prior={prior_p}; red-team={red_p}. "
            f"Cite only evidence IDs from {evidence_ids}."
        )
        resp = await self.llm.chat_json("supervisor", prompt, budget=budget)
        cited = resp.content.get("evidence_ids") or evidence_ids
        rationale = resp.content.get("rationale", "")
        return {"rationale": rationale, "p": p_super}, list(cited)

    async def _verify_citations(self, cited: list[str], evidence: list[dict]) -> list[str]:
        valid_ids = {e.get("_id") for e in evidence}
        verified = []
        for cid in cited:
            if cid not in valid_ids:
                continue
            ev = next((e for e in evidence if e.get("_id") == cid), None)
            if ev is None:
                continue
            state = {"item": {"title": "claim", "text": ev.get("summary", "")}}
            q = {
                "supports": {
                    "type": "noul",
                    "instructions": "Does evidence support the cited claim?",
                    "criteria": {"true": "supports", "false": "does not"},
                }
            }
            result = await self.jev.decide(state, q)
            if result.answers.get("supports", 0.0) >= 0.4:
                verified.append(cid)
        return verified

    async def _market_price(self, question) -> Optional[float]:
        ticks = await self.repo.find(
            MARKET_TICKS, {"meta.question_key": question.get("key")}, sort=[("ts", -1)], limit=1
        )
        if ticks:
            return float(ticks[0].get("value", 0.0))
        return None

    def _confidence_components(
        self, probs, evidence, market, p_super, question
    ) -> dict[str, float]:
        spread = (max(probs) - min(probs)) if probs else 1.0
        ensemble_agreement = max(0.0, 1.0 - spread)
        evidence_quality = min(1.0, len(evidence) / 5.0) if evidence else 0.3
        structural_clarity = 0.5 + 0.1 * (
            question.get("authority") in ("s301", "s232", "s338", "s122")
        )
        market_agreement = 1.0 - abs((market or p_super) - p_super)
        track_record = 0.55
        horizon = 0.7
        return {
            "ensemble_agreement": round(ensemble_agreement, 3),
            "evidence_quality": round(evidence_quality, 3),
            "structural_clarity": round(min(1.0, structural_clarity), 3),
            "market_agreement": round(max(0.0, market_agreement), 3),
            "track_record": track_record,
            "horizon": horizon,
        }

    def _buckets(self, p: float) -> dict[str, float]:
        return {
            "0pct": round(max(0.0, (1 - p) * 0.9), 4),
            "25pct": round(0.1, 4),
            "50pct": round(min(1.0, p * 0.9), 4),
        }


async def load_forecaster_weights(
    repo: Repository, *, floor: float = 0.05, n: int = 20
) -> dict[str, float]:
    """Softmax of negative Brier over recent resolved scores, with a floor."""
    import math

    weights: dict[str, float] = {r: 1.0 for r in FORECASTER_ROLES}
    briers: dict[str, list[float]] = {}
    scores = await repo.find(SCORES, {}, sort=[("created_at", -1)], limit=n * len(FORECASTER_ROLES))
    for s in scores:
        agent = s.get("agent")
        if agent in weights:
            briers.setdefault(agent, []).append(s.get("brier", 0.25))
    if not briers:
        total = sum(weights.values())
        return {k: v / total for k, v in weights.items()}
    neg = {a: -(sum(b) / len(b)) for a, b in briers.items()}
    for a in weights:
        neg.setdefault(a, -0.25)
    mx = max(neg.values())
    exp = {a: math.exp(v - mx) for a, v in neg.items()}
    z = sum(exp.values())
    raw = {a: exp[a] / z for a in exp}
    floored = {a: max(floor, w) for a, w in raw.items()}
    z2 = sum(floored.values())
    return {a: w / z2 for a, w in floored.items()}
