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
from levy.agents.scouts import ScoutService, content_hash
from levy.agents.triage import TriageService
from levy.core.collections import BELIEFS, EVIDENCE, QUESTIONS, RAW_ITEMS
from levy.core.db import Repository
from levy.core.events import EventBus
from levy.core.jobs import JobQueue
from levy.schemas import Evidence


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
        """Ingest -> triage -> (maybe question) -> forecast affected questions.

        Idempotent and resumable. A previously-seen document (duplicate raw
        hash) is *not* treated as a no-op: instead we load the existing raw
        item and any evidence produced for it and continue from wherever the
        prior (possibly interrupted) run stopped:

        * no evidence yet            -> resume triage on the existing raw item
        * evidence but no belief yet -> continue linking/factory/forecast
        * evidence + complete beliefs -> return duplicate/complete (no new work)
        """
        raw = await self.scouts.ingest(doc)
        if raw is None:
            return await self._resume_document(doc)

        outcome = await self.triage.triage_item(raw.to_doc())
        return await self._process_after_triage(outcome, resumed=False)

    async def _resume_document(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Resume processing for a document whose raw item already exists."""
        h = doc.get("hash") or content_hash(
            doc.get("source", "unknown"),
            doc.get("url", ""),
            doc.get("title", ""),
            doc.get("text", ""),
        )
        raw_doc = await self.repo.find_one(RAW_ITEMS, {"hash": h})
        if raw_doc is None:
            # Hash collision report but no stored raw item; nothing to resume.
            return {"ingested": False, "reason": "duplicate"}

        raw_item_id = raw_doc.get("_id", "")
        existing = await self.repo.find(EVIDENCE, {"raw_item_id": raw_item_id})
        if not existing:
            # Evidence was never produced (triage never completed). Resume it.
            outcome = await self.triage.triage_item(raw_doc)
            return await self._process_after_triage(outcome, resumed=True)

        # Evidence already exists — do not reclassify or duplicate it. Continue
        # the downstream stages using the persisted evidence document.
        evidence = Evidence(**existing[0])
        return await self._process_evidence(evidence, resumed=True)

    async def _process_after_triage(self, outcome, *, resumed: bool) -> dict[str, Any]:
        if not outcome.accepted:
            reason = outcome.rejected.reason if outcome.rejected is not None else "rejected"
            if self.bus is not None:
                await self.bus.publish("evidence", {"rejected": True, "reason": reason})
            return {"ingested": True, "accepted": False, "reason": reason, "resumed": resumed}

        evidence = outcome.evidence
        if evidence is None:
            raise RuntimeError("triage accepted an item without producing evidence")
        if self.bus is not None:
            await self.bus.publish("evidence", evidence.to_doc())
        return await self._process_evidence(
            evidence, resumed=resumed, materiality=outcome.materiality
        )

    async def _process_evidence(
        self, evidence: Evidence, *, resumed: bool, materiality: Optional[int] = None
    ) -> dict[str, Any]:
        """Link questions, run the factory, and forecast material questions.

        Safe to call repeatedly for the same evidence: question links use
        ``$addToSet`` (no duplicate ``question_ids``) and forecasts are skipped
        for questions that already carry an evidence-triggered belief.
        """
        if materiality is None:
            materiality = evidence.materiality

        # Create/link a question if none matched.
        if not evidence.question_ids:
            created = await self.factory.maybe_create(evidence.to_doc())
            if created is not None:
                # Re-read the persisted evidence so question_ids reflect the
                # link the factory just wrote (avoids duplicates on resume).
                refreshed = await self.repo.find_one(EVIDENCE, {"_id": evidence.id})
                if refreshed is not None:
                    evidence = Evidence(**refreshed)
                elif created.id not in evidence.question_ids:
                    evidence.question_ids = [*evidence.question_ids, created.id]

        beliefs: list[dict[str, Any]] = []
        newly_forecast = 0
        if materiality >= 3:
            for qid in evidence.question_ids:
                question = await self.repo.find_one(QUESTIONS, {"_id": qid})
                if not question:
                    continue
                # Idempotency: if this evidence already triggered a belief for
                # this question, do not create another one.
                if await self._belief_exists_for(qid, evidence.id):
                    continue
                job = await self.jobs.enqueue("forecast", qid, evidence_ids=[evidence.id])
                try:
                    belief = await self.run_forecast(
                        question, trigger={"type": "evidence", "id": evidence.id}
                    )
                except Exception as exc:  # noqa: BLE001
                    # Forecast failed *after* raw/evidence/classification were
                    # already persisted. Fail/requeue the job so a later run can
                    # retry, and return an honest result: the item WAS ingested
                    # and accepted (do not report it as a non-insertion), but
                    # carries an error and is not yet complete.
                    await self.jobs.fail(job.key)
                    return {
                        "ingested": True,
                        "accepted": True,
                        "resumed": resumed,
                        "complete": False,
                        "error": str(exc),
                        "evidence_id": evidence.id,
                        "questions": evidence.question_ids,
                        "beliefs": beliefs,
                    }
                beliefs.append(belief.to_doc())
                newly_forecast += 1
                await self.jobs.complete(job.key)

        # Distinguish "resumed with new work" from "already complete".
        complete = resumed and newly_forecast == 0 and not beliefs
        return {
            "ingested": True,
            "accepted": True,
            "resumed": resumed,
            "complete": complete,
            "evidence_id": evidence.id,
            "questions": evidence.question_ids,
            "beliefs": beliefs,
        }

    async def _belief_exists_for(self, question_id: str, evidence_id: str) -> bool:
        """True if an evidence-triggered belief already exists for the pair."""
        prior = await self.repo.find(BELIEFS, {"question_id": question_id})
        for b in prior:
            trigger = b.get("trigger") or {}
            if trigger.get("type") == "evidence" and trigger.get("id") == evidence_id:
                return True
        return False

    async def run_forecast(self, question: dict[str, Any], *, trigger: Optional[dict] = None):
        await self.analysts.run_all(question)
        belief = await self.forecast.run(question, trigger=trigger)
        await self.exposure.map_belief(belief.to_doc(), question)
        return belief
