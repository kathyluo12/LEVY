"""Resolution Clerk: proposes resolutions (Jev), human confirms, writes scores.

The clerk inspects an official doc and proposes YES/NO/not-yet via Jev choice.
On human confirmation the question is marked resolved, per-forecaster and
supervisor Brier/log scores are computed and stored.
"""

from __future__ import annotations

from typing import Any, Optional

from levy.core.calibrate import brier_score, log_score
from levy.core.collections import (
    BELIEFS,
    FORECAST_RUNS,
    QUESTIONS,
    RESOLUTIONS,
    SCORES,
)
from levy.core.db import DuplicateKeyError, Repository
from levy.core.events import EventBus
from levy.core.jev import JevAdapter
from levy.schemas import QuestionStatus, Resolution, Score


class ResolutionClerk:
    def __init__(
        self,
        repo: Repository,
        jev: Optional[JevAdapter] = None,
        bus: Optional[EventBus] = None,
        settings=None,
    ) -> None:
        self.repo = repo
        self.jev = jev or JevAdapter(settings=settings)
        self.bus = bus

    async def propose(
        self, question_key: str, official_doc: dict[str, Any]
    ) -> Optional[Resolution]:
        question = await self.repo.find_one(QUESTIONS, {"key": question_key})
        if not question:
            return None
        state = {
            "item": {"title": official_doc.get("title", ""), "text": official_doc.get("text", "")}
        }
        q = {
            "resolution": {
                "type": "choice",
                "instructions": "Did official_doc resolve the question?",
                "criteria": {"YES": "Resolved yes", "NO": "Resolved no", "PENDING": "Not yet"},
            }
        }
        result = await self.jev.decide(state, q)
        choice = result.answers.get("resolution", {}).get("choice", "PENDING")
        if choice == "PENDING":
            return None
        resolution = Resolution(
            question_id=question.get("_id", ""),
            outcome=choice,
            source_url=official_doc.get("url", ""),
            proposed=True,
        )
        try:
            await self.repo.insert(RESOLUTIONS, resolution.to_doc())
        except DuplicateKeyError:
            existing = await self.repo.find_one(RESOLUTIONS, {"question_id": question.get("_id")})
            return Resolution(**existing) if existing else None
        return resolution

    async def confirm(
        self, question_key: str, *, confirmed_by: str = "human", outcome: Optional[str] = None
    ):
        question = await self.repo.find_one(QUESTIONS, {"key": question_key})
        if not question:
            return None
        qid = question.get("_id", "")
        existing = await self.repo.find_one(RESOLUTIONS, {"question_id": qid})
        if existing is None and outcome is None:
            return None
        final_outcome = outcome or (existing.get("outcome") if existing else None)
        if final_outcome not in {"YES", "NO"}:
            return None

        if existing is None:
            res = Resolution(
                question_id=qid, outcome=final_outcome, confirmed_by=confirmed_by, proposed=False
            )
            try:
                await self.repo.insert(RESOLUTIONS, res.to_doc())
            except DuplicateKeyError:
                pass
        await self.repo.update_one(
            RESOLUTIONS,
            {"question_id": qid},
            {"$set": {"outcome": final_outcome, "confirmed_by": confirmed_by, "proposed": False}},
        )
        await self.repo.update_one(
            QUESTIONS, {"_id": qid}, {"$set": {"status": QuestionStatus.RESOLVED.value}}
        )

        scores = await self._score(qid, final_outcome)

        if self.bus is not None:
            await self.bus.publish(
                "resolution", {"question_id": qid, "outcome": final_outcome, "scores": len(scores)}
            )
        return {"question_id": qid, "outcome": final_outcome, "scores": scores}

    async def _score(self, question_id: str, outcome: str) -> list[dict[str, Any]]:
        y = 1 if outcome == "YES" else 0
        results: list[dict[str, Any]] = []

        # Score the latest supervisor belief.
        beliefs = await self.repo.find(
            BELIEFS, {"question_id": question_id}, sort=[("version", -1)], limit=1
        )
        if beliefs:
            b = beliefs[0]
            score = Score(
                question_id=question_id,
                agent="supervisor",
                model="ensemble",
                brier=round(brier_score(b.get("p_calibrated", 0.5), y), 4),
                log_score=round(log_score(b.get("p_calibrated", 0.5), y), 4),
            )
            await self.repo.insert(SCORES, score.to_doc())
            results.append(score.to_doc())

        # Score individual forecaster runs (latest per agent).
        runs = await self.repo.find(
            FORECAST_RUNS, {"question_id": question_id}, sort=[("created_at", -1)]
        )
        seen: set[str] = set()
        for r in runs:
            agent = r.get("agent")
            if not isinstance(agent, str) or not agent or agent in seen:
                continue
            seen.add(agent)
            score = Score(
                question_id=question_id,
                agent=agent,
                model=r.get("model", ""),
                brier=round(brier_score(r.get("p_raw", 0.5), y), 4),
                log_score=round(log_score(r.get("p_raw", 0.5), y), 4),
            )
            await self.repo.insert(SCORES, score.to_doc())
            results.append(score.to_doc())
        return results
