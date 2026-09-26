"""Replay scenario engine with SimClock, cutoffs and leak probing.

Loads dated documents for a scenario, advances a SimClock, and feeds only
documents dated <= sim_now into the pipeline (document cutoff / leakage
defense). After each forecast, a leak probe checks the rationale for references
to events after the simulated date. Runs against an isolated in-memory repo so
the live board is never polluted.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

from levy.agents.pipeline import Pipeline
from levy.core.clock import (
    SimClock,
    document_visible,
    leak_probe,
    prompt_cutoff_note,
    replay_db_name,
)
from levy.core.collections import QUESTIONS
from levy.core.db import InMemoryRepository, Repository
from levy.core.events import EventBus
from levy.schemas import Question, QuestionStatus

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "replay"

SCENARIOS = ("canada_338", "section_122_expiry", "china_truce_extension")


def _coerce_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    if isinstance(v, str):
        try:
            dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)


def load_scenario(scenario: str) -> dict[str, Any]:
    path = DATA_DIR / scenario / "scenario.json"
    with path.open() as f:
        return json.load(f)


class ReplayEngine:
    def __init__(self, scenario: str, *, bus: Optional[EventBus] = None, settings=None) -> None:
        self.scenario = scenario
        self.bus = bus
        self.settings = settings
        # Isolated repo/database per replay scenario.
        self.db_name = replay_db_name(scenario)
        self.repo: Repository = InMemoryRepository()
        self.leaks: list[dict[str, Any]] = []
        self.visible_docs_seen: list[str] = []

    async def prepare(self) -> dict[str, Any]:
        await self.repo.bootstrap()
        data = load_scenario(self.scenario)
        # Seed the scenario question.
        q = data["question"]
        question = Question(
            key=q["key"],
            text=q["text"],
            country=q.get("country", ""),
            authority=q.get("authority", "other"),
            resolution_source=q.get("resolution_source", ""),
            status=QuestionStatus.ACTIVE.value,
        )
        await self.repo.insert(QUESTIONS, question.to_doc())
        self._question = question
        self._docs = sorted(data["documents"], key=lambda d: _coerce_dt(d["published_at"]))
        self._resolution = data.get("resolution")
        self.start = _coerce_dt(self._docs[0]["published_at"])
        self.end = _coerce_dt(self._docs[-1]["published_at"])
        self.clock = SimClock(self.start, step=timedelta(days=1))
        return {"scenario": self.scenario, "db": self.db_name, "documents": len(self._docs)}

    def visible_now(self, sim_now: datetime) -> list[dict[str, Any]]:
        """Leak-defense accessor: only documents dated on/before sim_now."""
        return [d for d in self._docs if document_visible(d["published_at"], sim_now)]

    async def run(self) -> dict[str, Any]:
        pipeline = Pipeline(self.repo, bus=self.bus, settings=self.settings)
        processed: set[str] = set()
        sim_now = self.start
        timeline: list[dict[str, Any]] = []

        while sim_now <= self.end + timedelta(days=1):
            for doc in self.visible_now(sim_now):
                doc_id = doc.get("hash") or doc.get("title", "")
                if doc_id in processed:
                    continue
                processed.add(doc_id)
                self.visible_docs_seen.append(doc_id)

                enriched = dict(doc)
                enriched.setdefault("source", self.scenario)
                result = await pipeline.process_document(enriched)

                # Leak probe on the latest belief rationale.
                for b in result.get("beliefs", []):
                    probe = leak_probe(b.get("rationale", ""), sim_now)
                    if probe["leak"]:
                        self.leaks.append({"version": b.get("version"), **probe})
                    timeline.append(
                        {
                            "sim_now": sim_now.isoformat(),
                            "version": b.get("version"),
                            "p_calibrated": b.get("p_calibrated"),
                        }
                    )
            sim_now = self.clock.advance()

        scored = await self._resolve_and_score(pipeline)
        return {
            "scenario": self.scenario,
            "db": self.db_name,
            "documents_processed": len(processed),
            "timeline": timeline,
            "leak_probe_hits": len(self.leaks),
            "leaks": self.leaks,
            "prompt_cutoff": prompt_cutoff_note(self.end),
            **scored,
        }

    async def _resolve_and_score(self, pipeline: Pipeline) -> dict[str, Any]:
        if not self._resolution:
            return {"resolved": False}
        from levy.agents.resolution import ResolutionClerk

        clerk = ResolutionClerk(self.repo, bus=self.bus, settings=self.settings)
        res = await clerk.confirm(
            self._question.key,
            confirmed_by="replay",
            outcome=self._resolution["outcome"],
        )
        return {
            "resolved": True,
            "outcome": self._resolution["outcome"],
            "scores": res.get("scores", []) if res else [],
            "note": "in-sample demonstration",
        }
