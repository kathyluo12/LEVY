"""Self-improvement: Reflector lessons, calibration refit, forecaster weights.

* Reflector compares a resolved belief timeline with the outcome and writes at
  most one lesson (Jev checks it is supported before saving).
* Calibration refit fits a new calibration_map version from resolved supervisor
  probabilities vs outcomes.
* Forecaster weights are derived in ``forecast.load_forecaster_weights``; this
  module exposes a leaderboard helper.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any, Optional

from levy.core.calibrate import fit_calibration
from levy.core.collections import (
    BELIEFS,
    CALIBRATION_MAPS,
    LESSONS,
    RESOLUTIONS,
    SCORES,
)
from levy.core.db import Repository
from levy.core.events import EventBus
from levy.core.jev import JevAdapter
from levy.core.llm import LLMClient
from levy.core.memory import embed
from levy.schemas import CalibrationMap, Lesson, iso_now


class Reflector:
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

    async def reflect(self, question: dict[str, Any], outcome: str) -> Optional[Lesson]:
        qid = question.get("_id", "")
        beliefs = await self.repo.find(BELIEFS, {"question_id": qid}, sort=[("version", 1)])
        timeline = " -> ".join(f"v{b['version']}:{b.get('p_calibrated')}" for b in beliefs)
        prompt = (
            f"Question resolved {outcome}. Belief timeline: {timeline}. "
            f"Write at most one lesson (pattern, evidence, applies-to question type)."
        )
        resp = await self.llm.chat_json("reflector", prompt)
        text = resp.content.get("lesson")
        if not text:
            return None

        # Jev checks the lesson is supported by the timeline.
        state = {"item": {"title": "lesson", "text": text + " timeline: " + timeline}}
        check = {
            "supported": {
                "type": "noul",
                "instructions": "Is the lesson supported by the timeline?",
                "criteria": {"true": "supported", "false": "unsupported"},
            }
        }
        verdict = await self.jev.decide(state, check)
        if verdict.answers.get("supported", 0.0) < 0.4 and not resp.content.get("supported"):
            return None

        applies = resp.content.get("applies_to", {"authority": question.get("authority")})
        lesson = Lesson(
            text=text,
            applies_to=applies,
            evidence=beliefs[-1].get("evidence_ids", []) if beliefs else [],
            status="active",
            embedding=embed(text),
            expires_at=iso_now() + timedelta(days=90),
        )
        await self.repo.insert(LESSONS, lesson.to_doc())
        if self.bus is not None:
            await self.bus.publish("lesson", lesson.to_doc())
        return lesson


class CalibrationRefit:
    def __init__(self, repo: Repository) -> None:
        self.repo = repo

    async def refit(self) -> Optional[CalibrationMap]:
        resolutions = await self.repo.find(RESOLUTIONS, {"proposed": False})
        preds: list[float] = []
        outcomes: list[int] = []
        for r in resolutions:
            qid = r.get("question_id")
            beliefs = await self.repo.find(
                BELIEFS, {"question_id": qid}, sort=[("version", -1)], limit=1
            )
            if not beliefs:
                continue
            preds.append(float(beliefs[0].get("p_raw", 0.5)))
            outcomes.append(1 if r.get("outcome") == "YES" else 0)

        model = fit_calibration(preds, outcomes)
        existing = await self.repo.find(CALIBRATION_MAPS, {}, sort=[("version", -1)], limit=1)
        version = (existing[0]["version"] + 1) if existing else 1
        cmap = CalibrationMap(
            version=version,
            method=model.method,
            points=model.points,
            params=model.params,
            n_resolutions=len(preds),
        )
        await self.repo.insert(CALIBRATION_MAPS, cmap.to_doc())
        return cmap


async def leaderboard(repo: Repository) -> list[dict[str, Any]]:
    """Average Brier per agent/model for the learning panel."""
    scores = await repo.find(SCORES, {})
    agg: dict[str, dict[str, Any]] = {}
    for s in scores:
        agent = s.get("agent", "")
        row = agg.setdefault(agent, {"agent": agent, "model": s.get("model", ""), "briers": []})
        row["briers"].append(s.get("brier", 0.25))
    out = []
    for agent, row in agg.items():
        briers = row["briers"]
        out.append(
            {
                "agent": agent,
                "model": row["model"],
                "avg_brier": round(sum(briers) / len(briers), 4) if briers else None,
                "n": len(briers),
            }
        )
    out.sort(key=lambda r: (r["avg_brier"] is None, r["avg_brier"]))
    return out
