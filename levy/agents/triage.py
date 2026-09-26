"""Triage: run Jev on raw items, write evidence or evidence_rejected.

Thresholds (from the TDD):
* new (materially different): drop if P(yes) < 0.3
* relevant (US tariffs/trade): drop if P(yes) < 0.4
* materiality (0-4 scale): wake analysts at >= 3

Every Jev answer (raw probabilities) is stored in ``classifications`` so
thresholds can be re-tuned later without new calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from levy.core.collections import (
    CLASSIFICATIONS,
    EVIDENCE,
    EVIDENCE_REJECTED,
    QUESTIONS,
)
from levy.core.db import Repository
from levy.core.jev import JevAdapter, extract_countries
from levy.core.memory import embed
from levy.schemas import (
    Authority,
    Classification,
    Direction,
    Evidence,
    EvidenceRejected,
)

THRESH_NEW = 0.3
THRESH_RELEVANT = 0.4
MATERIALITY_WAKE = 3

TRIAGE_QUESTIONS: dict[str, Any] = {
    "new": {
        "type": "noul",
        "instructions": "Is `item` materially different from recent_items?",
        "criteria": {"true": "Materially new", "false": "Duplicate or stale"},
    },
    "relevant": {
        "type": "noul",
        "instructions": "Does `item` concern U.S. tariffs or trade actions?",
        "criteria": {
            "true": "It announces, changes, delays, litigates or negotiates a U.S. tariff/trade measure.",
            "false": "Unrelated to U.S. tariffs.",
        },
    },
    "authority": {
        "type": "choice",
        "instructions": "Which legal authority does `item` concern?",
        "criteria": {
            "s301": "Section 301",
            "s232": "Section 232",
            "s338": "Section 338",
            "s122": "Section 122",
            "ieepa": "IEEPA",
            "court": "Court ruling",
            "deal": "Trade agreement",
            "other": "Other or unclear",
        },
    },
    "direction": {
        "type": "choice",
        "instructions": "What is the direction of `item`?",
        "criteria": {
            "escalation": "Escalation",
            "de_escalation": "De-escalation",
            "delay": "Delay",
            "procedural": "Procedural",
            "neutral": "Neutral",
        },
    },
    "materiality": {
        "type": "score",
        "instructions": "How much could `item` change a tariff outcome?",
        "criteria": ["No effect", "Minor context", "Relevant", "Important", "Decisive"],
    },
    "specificity": {
        "type": "score",
        "instructions": "Rhetoric specificity: named rate, date, authority, sector.",
        "criteria": ["None", "One", "Two", "Three", "Four"],
    },
}


@dataclass
class TriageOutcome:
    accepted: bool
    evidence: Optional[Evidence] = None
    rejected: Optional[EvidenceRejected] = None
    materiality: int = 0


class TriageService:
    def __init__(self, repo: Repository, jev: Optional[JevAdapter] = None, settings=None) -> None:
        self.repo = repo
        self.jev = jev or JevAdapter(settings=settings)

    async def triage_item(
        self, raw_item: dict[str, Any], recent: Optional[list[str]] = None
    ) -> TriageOutcome:
        state = {
            "item": {
                "source": raw_item.get("source", ""),
                "title": raw_item.get("title", ""),
                "text": raw_item.get("text", ""),
            },
            "recent_items": recent or [],
        }
        result = await self.jev.decide(state, TRIAGE_QUESTIONS)

        # Persist raw probabilities for re-tuning.
        classification = Classification(
            item_id=raw_item.get("_id", ""),
            raw_probs=result.raw,
            model=self.jev.model,
            classifier=result.classifier,
            latency_ms=result.latency_ms,
            cost_usd=result.cost_usd,
        )
        await self.repo.insert(CLASSIFICATIONS, classification.to_doc())

        p_new = result.answers.get("new", 0.5)
        p_relevant = result.answers.get("relevant", 0.5)

        if p_new < THRESH_NEW:
            return await self._reject(raw_item, "duplicate", result.raw)
        if p_relevant < THRESH_RELEVANT:
            return await self._reject(raw_item, "irrelevant", result.raw)

        authority = result.answers.get("authority", {}).get("choice") or Authority.OTHER.value
        direction = result.answers.get("direction", {}).get("choice") or Direction.NEUTRAL.value
        materiality = result.answers.get("materiality", {}).get("level", 0)
        specificity = result.answers.get("specificity", {}).get("level", 0)

        text_blob = f"{raw_item.get('title','')} {raw_item.get('text','')}"
        countries = extract_countries(text_blob)
        summary = raw_item.get("title") or text_blob[:180]

        # Link to existing questions by country + authority.
        question_ids = await self._link_questions(countries, authority)

        evidence_data: dict[str, Any] = {
            "raw_item_id": raw_item.get("_id", ""),
            "countries": countries,
            "authority": authority,
            "direction": direction,
            "materiality": int(materiality),
            "specificity": int(specificity),
            "question_ids": question_ids,
            "summary": summary,
            "embedding": embed(summary),
        }
        if raw_item.get("published_at") is not None:
            evidence_data["published_at"] = raw_item["published_at"]
        evidence = Evidence(**evidence_data)
        await self.repo.insert(EVIDENCE, evidence.to_doc())
        return TriageOutcome(accepted=True, evidence=evidence, materiality=int(materiality))

    async def _reject(self, raw_item, reason, raw_probs) -> TriageOutcome:
        rej = EvidenceRejected(
            raw_item_id=raw_item.get("_id", ""), reason=reason, jev_probs=raw_probs
        )
        await self.repo.insert(EVIDENCE_REJECTED, rej.to_doc())
        return TriageOutcome(accepted=False, rejected=rej)

    async def _link_questions(self, countries: list[str], authority: str) -> list[str]:
        linked: list[str] = []
        questions = await self.repo.find(QUESTIONS, {})
        for q in questions:
            q_country = q.get("country", "")
            q_auth = q.get("authority", "")
            if q_country in countries and (q_auth == authority or q_auth == Authority.OTHER.value):
                linked.append(q.get("_id", ""))
        return linked
