"""Pipeline orchestration: wire scouts -> triage -> analysts -> forecast -> exposure.

Offline-friendly end-to-end driver used by the API demo endpoints, the seed
routine and the replay engine. Emits stream events at each stage.
"""

from __future__ import annotations

from typing import Any, Optional

from levy.agents.analysts import AnalystService
from levy.agents.exposure import ExposureMapper
from levy.agents.forecast import ForecastCell
from levy.agents.question_factory import QuestionFactory
from levy.agents.scouts import ScoutService
from levy.agents.triage import TriageService
from levy.core.collections import QUESTIONS
from levy.core.db import Repository
from levy.core.events import EventBus
from levy.core.jobs import JobQueue


class Pipeline:
    def __init__(self, repo: Repository, bus: Optional[EventBus] = None, settings=None) -> None:
        self.repo = repo
        self.bus = bus
        self.settings = settings
        self.scouts = ScoutService(repo)
        self.triage = TriageService(repo, settings=settings)
        self.analysts = AnalystService(repo, settings=settings)
        self.factory = QuestionFactory(repo, settings=settings)
        self.forecast = ForecastCell(repo, bus=bus, settings=settings)
        self.exposure = ExposureMapper(repo)
        self.jobs = JobQueue(repo)

    async def process_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Ingest -> triage -> (maybe question) -> forecast affected questions."""
        raw = await self.scouts.ingest(doc)
        if raw is None:
            return {"ingested": False, "reason": "duplicate"}

        outcome = await self.triage.triage_item(raw.to_doc())
        if not outcome.accepted:
            reason = outcome.rejected.reason if outcome.rejected is not None else "rejected"
            if self.bus is not None:
                await self.bus.publish("evidence", {"rejected": True, "reason": reason})
            return {"ingested": True, "accepted": False, "reason": reason}

        evidence = outcome.evidence
        if evidence is None:
            raise RuntimeError("triage accepted an item without producing evidence")
        if self.bus is not None:
            await self.bus.publish("evidence", evidence.to_doc())

        # Create a question if none matched.
        if not evidence.question_ids:
            created = await self.factory.maybe_create(evidence.to_doc())
            if created is not None:
                evidence.question_ids = [created.id]

        beliefs = []
        if outcome.materiality >= 3:
            for qid in evidence.question_ids:
                question = await self.repo.find_one(QUESTIONS, {"_id": qid})
                if not question:
                    continue
                # Enqueue an idempotent job (debounce + atomic exclusivity), run
                # the forecast inline for the demo path, then mark it done.
                job = await self.jobs.enqueue("forecast", qid, evidence_ids=[evidence.id])
                belief = await self.run_forecast(
                    question, trigger={"type": "evidence", "id": evidence.id}
                )
                beliefs.append(belief.to_doc())
                await self.jobs.complete(job.key)
        return {
            "ingested": True,
            "accepted": True,
            "evidence_id": evidence.id,
            "questions": evidence.question_ids,
            "beliefs": beliefs,
        }

    async def run_forecast(self, question: dict[str, Any], *, trigger: Optional[dict] = None):
        await self.analysts.run_all(question)
        belief = await self.forecast.run(question, trigger=trigger)
        await self.exposure.map_belief(belief.to_doc(), question)
        return belief
