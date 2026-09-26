"""Shared fixtures. Forces offline mode so tests never touch network or Mongo."""

from __future__ import annotations

import os

import pytest
import pytest_asyncio

os.environ.setdefault("LEVY_OFFLINE", "true")

from levy.core.db import InMemoryRepository  # noqa: E402
from levy.settings import Settings  # noqa: E402


@pytest.fixture
def settings() -> Settings:
    return Settings(offline=True)


@pytest_asyncio.fixture
async def repo() -> InMemoryRepository:
    r = InMemoryRepository()
    await r.bootstrap()
    return r


@pytest_asyncio.fixture
async def seeded_repo() -> InMemoryRepository:
    from levy.seed import seed_repository

    r = InMemoryRepository()
    await r.bootstrap()
    await seed_repository(r)
    return r
