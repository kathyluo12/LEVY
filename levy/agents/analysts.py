"""Analysts: Legal, Political, Counterparty, Base-Rate brief writers.

Each analyst reads material evidence for a question, optionally recalls lessons
and reference-class events (Base-Rate), and writes a versioned ``briefs`` doc.
Runs offline via the deterministic LLM adapter.
"""

from __future__ import annotations

from typing import Any, Optional

from levy.core.collections import BRIEFS, EVIDENCE
from levy.core.db import Repository
from levy.core.llm import BudgetTracker, LLMClient
from levy.core.memory import base_rate, reference_class
from levy.schemas import Brief

ANALYSTS = ("legal", "political", "counterparty", "base_rate")


class AnalystService:
    def __init__(self, repo: Repository, llm: Optional[LLMClient] = None, settings=None) -> None:
        self.repo = repo
        self.llm = llm or LLMClient(settings=settings)

    async def _next_version(self, question_id: str, analyst: str) -> int:
        existing = await self.repo.find(BRIEFS, {"question_id": question_id, "analyst": analyst})
        return len(existing) + 1

    async def _evidence_for(self, question_id: str) -> list[dict[str, Any]]:
        ev = await self.repo.find(EVIDENCE, {"question_ids": question_id})
        return ev

    async def run_analyst(
        self, analyst: str, question: dict[str, Any], *, budget: Optional[BudgetTracker] = None
    ) -> Brief:
        question_id = question.get("_id", "")
        evidence = await self._evidence_for(question_id)
        evidence_ids = [e.get("_id", "") for e in evidence]

        extra = ""
        if analyst == "base_rate":
            events = await reference_class(
                self.repo, country=question.get("country"), authority=question.get("authority")
            )
            rate = base_rate(events)
            extra = f" base_rate={rate:.2f} over {len(events)} events"

        prompt = (
            f"Question: {question.get('text','')}\n"
            f"Country: {question.get('country','')} Authority: {question.get('authority','')}\n"
            f"Evidence IDs: {evidence_ids}\n"
            f"Write a {analyst} analyst brief citing only evidence IDs.{extra}"
        )
        resp = await self.llm.chat_json(analyst, prompt, budget=budget)
        text = resp.content.get("brief") or resp.text

        brief = Brief(
            question_id=question_id,
            analyst=analyst,
            version=await self._next_version(question_id, analyst),
            text=text + extra,
            evidence_ids=evidence_ids,
        )
        await self.repo.insert(BRIEFS, brief.to_doc())
        return brief

    async def run_all(
        self, question: dict[str, Any], *, budget: Optional[BudgetTracker] = None
    ) -> list[Brief]:
        return [await self.run_analyst(a, question, budget=budget) for a in ANALYSTS]
