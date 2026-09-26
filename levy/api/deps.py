"""Shared API dependencies: repository + event bus accessors."""

from __future__ import annotations

from levy.core.db import Repository, get_repository
from levy.core.events import EventBus, get_event_bus


async def repo_dep() -> Repository:
    return await get_repository()


def bus_dep() -> EventBus:
    return get_event_bus()
