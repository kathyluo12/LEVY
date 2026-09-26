"""Question Factory: draft new questions from material evidence with no match.

Uses Jev-style dedup (deterministic key check) plus the LLM to draft text.
"""

from __future__ import annotations

import re
from typing import Any, Optional

from levy.core.collections import QUESTIONS
from levy.core.db import DuplicateKeyError, Repository
from levy.core.llm import LLMClient
from levy.schemas import Authority, Question, QuestionStatus


def make_key(country: str, authority: str, action: str) -> str:
    action_slug = re.sub(r"[^A-Z0-9]+", "-", action.upper()).strip("-")[:20] or "GEN"
    return f"{(country or 'GLB').upper()}-{authority.upper()}-{action_slug}"


class QuestionFactory:
    def __init__(self, repo: Repository, llm: Optional[LLMClient] = None, settings=None) -> None:
        self.repo = repo
        self.llm = llm or LLMClient(settings=settings)

    async def maybe_create(self, evidence: dict[str, Any]) -> Optional[Question]:
        if evidence.get("question_ids"):
            return None  # already linked
        countries = evidence.get("countries") or []
        country = countries[0] if countries else ""
        authority = evidence.get("authority", Authority.OTHER.value)
        action = evidence.get("direction", "action")
        key = make_key(country, authority, action)

        if await self.repo.find_one(QUESTIONS, {"key": key}):
            return None

        prompt = (
            f"Draft a binary forecast question for country={country}, "
            f"authority={authority}, direction={action}. Evidence summary: "
            f"{evidence.get('summary','')}"
        )
        resp = await self.llm.chat_json("question_factory", prompt)
        text = (
            resp.content.get("text") or f"Will the {authority} measure affecting {country} change?"
        )

        question = Question(
            key=key,
            text=text,
            country=country,
            authority=authority,
            outcomes=resp.content.get("outcomes", ["YES", "NO"]),
            status=QuestionStatus.DRAFT.value,
        )
        try:
            await self.repo.insert(QUESTIONS, question.to_doc())
        except DuplicateKeyError:
            return None
        # Link the evidence to the new question.
        await self.repo.update_one(
            "evidence", {"_id": evidence.get("_id")}, {"$push": {"question_ids": question.id}}
        )
        return question
