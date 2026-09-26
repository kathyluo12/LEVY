"""Persistence abstraction.

Two implementations:

* :class:`InMemoryRepository` — a fully functional, concurrency-safe repository
  backed by dicts and an ``asyncio.Lock``. Used by tests and offline mode. No
  external dependencies.
* :class:`MongoRepository` — production async implementation using Motor. All
  Mongo/Motor imports are performed lazily inside methods so that importing this
  module (and running the in-memory tests) never requires ``motor``/``pymongo``.

Both share the same async API surface described by :class:`Repository`.
"""

from __future__ import annotations

import asyncio
import copy
import re
from datetime import datetime, timezone
from typing import Any, Optional, Protocol, runtime_checkable

from levy.core import collections as C
from levy.core.collections import COLLECTION_SPECS


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _matches(doc: dict[str, Any], flt: dict[str, Any]) -> bool:
    """Minimal Mongo-style filter matcher for the in-memory repo."""
    for key, cond in flt.items():
        val = _dig(doc, key)
        if isinstance(cond, dict):
            for op, operand in cond.items():
                if op == "$gt" and not (val is not None and val > operand):
                    return False
                elif op == "$gte" and not (val is not None and val >= operand):
                    return False
                elif op == "$lt" and not (val is not None and val < operand):
                    return False
                elif op == "$lte" and not (val is not None and val <= operand):
                    return False
                elif op == "$ne" and not (val != operand):
                    return False
                elif op == "$in" and val not in operand:
                    return False
                elif op == "$exists":
                    exists = val is not None
                    if exists != operand:
                        return False
                elif op == "$regex":
                    if val is None or not re.search(operand, str(val)):
                        return False
        elif isinstance(cond, list):
            if val != cond:
                return False
        else:
            # equality; support membership when doc field is a list
            if isinstance(val, list):
                if cond not in val:
                    return False
            elif val != cond:
                return False
    return True


def _dig(doc: dict[str, Any], dotted: str) -> Any:
    cur: Any = doc
    for part in dotted.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            return None
    return cur


@runtime_checkable
class Repository(Protocol):
    async def bootstrap(self, *, seed: bool = False) -> None: ...
    async def insert(self, coll: str, doc: dict[str, Any]) -> dict[str, Any]: ...
    async def find_one(self, coll: str, flt: dict[str, Any]) -> Optional[dict[str, Any]]: ...
    async def find(
        self,
        coll: str,
        flt: Optional[dict[str, Any]] = None,
        *,
        sort: Optional[list[tuple[str, int]]] = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]: ...
    async def update_one(
        self, coll: str, flt: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> Optional[dict[str, Any]]: ...
    async def find_one_and_update(
        self,
        coll: str,
        flt: dict[str, Any],
        update: dict[str, Any],
        *,
        return_after: bool = True,
    ) -> Optional[dict[str, Any]]: ...
    async def count(self, coll: str, flt: Optional[dict[str, Any]] = None) -> int: ...
    async def delete_many(self, coll: str, flt: dict[str, Any]) -> int: ...
    async def close(self) -> None: ...


# --- In-memory implementation ---------------------------------------------


class InMemoryRepository:
    """Concurrency-safe in-memory repository.

    All mutating operations are guarded by a single ``asyncio.Lock`` which
    guarantees atomicity for ``find_one_and_update`` — the primitive used for
    leased job claims. Stored documents are deep-copied on the way in and out so
    callers cannot mutate internal state.
    """

    def __init__(self) -> None:
        self._data: dict[str, list[dict[str, Any]]] = {c: [] for c in C.ALL_COLLECTIONS}
        self._lock = asyncio.Lock()

    async def bootstrap(self, *, seed: bool = False) -> None:
        async with self._lock:
            for c in C.ALL_COLLECTIONS:
                self._data.setdefault(c, [])
        if seed:
            from levy.seed import seed_repository

            await seed_repository(self)

    def _apply_update(self, doc: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
        if any(k.startswith("$") for k in update):
            if "$set" in update:
                for k, v in update["$set"].items():
                    doc[k] = v
            if "$inc" in update:
                for k, v in update["$inc"].items():
                    doc[k] = (doc.get(k) or 0) + v
            if "$setOnInsert" in update:
                for k, v in update["$setOnInsert"].items():
                    doc.setdefault(k, v)
            if "$push" in update:
                for k, v in update["$push"].items():
                    doc.setdefault(k, []).append(v)
            if "$addToSet" in update:
                # Mongo semantics: append only if not already present. Supports
                # the ``$each`` modifier for adding multiple distinct values.
                for k, v in update["$addToSet"].items():
                    arr = doc.setdefault(k, [])
                    if isinstance(v, dict) and "$each" in v:
                        values = v["$each"]
                    else:
                        values = [v]
                    for item in values:
                        if item not in arr:
                            arr.append(item)
        else:
            doc = copy.deepcopy(update)
        return doc

    def _unique_key(self, coll: str) -> Optional[str]:
        spec = COLLECTION_SPECS.get(coll)
        return spec.unique_key if spec else None

    async def insert(self, coll: str, doc: dict[str, Any]) -> dict[str, Any]:
        async with self._lock:
            store = self._data.setdefault(coll, [])
            uk = self._unique_key(coll)
            if uk is not None and doc.get(uk) is not None:
                for existing in store:
                    if existing.get(uk) == doc.get(uk):
                        raise DuplicateKeyError(f"duplicate {uk}={doc.get(uk)} in {coll}")
            store.append(copy.deepcopy(doc))
            return copy.deepcopy(doc)

    async def find_one(self, coll: str, flt: dict[str, Any]) -> Optional[dict[str, Any]]:
        async with self._lock:
            for d in self._data.get(coll, []):
                if _matches(d, flt):
                    return copy.deepcopy(d)
        return None

    async def find(
        self,
        coll: str,
        flt: Optional[dict[str, Any]] = None,
        *,
        sort: Optional[list[tuple[str, int]]] = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        flt = flt or {}
        async with self._lock:
            rows = [copy.deepcopy(d) for d in self._data.get(coll, []) if _matches(d, flt)]
        if sort:
            for field_name, direction in reversed(sort):
                rows.sort(key=lambda r: _sort_key(_dig(r, field_name)), reverse=(direction < 0))
        if limit:
            rows = rows[:limit]
        return rows

    async def update_one(
        self, coll: str, flt: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> Optional[dict[str, Any]]:
        async with self._lock:
            store = self._data.setdefault(coll, [])
            for i, d in enumerate(store):
                if _matches(d, flt):
                    store[i] = self._apply_update(d, update)
                    return copy.deepcopy(store[i])
            if upsert:
                base: dict[str, Any] = {}
                for k, v in flt.items():
                    if not isinstance(v, dict):
                        base[k] = v
                base = self._apply_update(base, update)
                store.append(base)
                return copy.deepcopy(base)
        return None

    async def find_one_and_update(
        self,
        coll: str,
        flt: dict[str, Any],
        update: dict[str, Any],
        *,
        return_after: bool = True,
    ) -> Optional[dict[str, Any]]:
        async with self._lock:
            store = self._data.setdefault(coll, [])
            for i, d in enumerate(store):
                if _matches(d, flt):
                    before = copy.deepcopy(d)
                    store[i] = self._apply_update(d, update)
                    return copy.deepcopy(store[i]) if return_after else before
        return None

    async def count(self, coll: str, flt: Optional[dict[str, Any]] = None) -> int:
        flt = flt or {}
        async with self._lock:
            return sum(1 for d in self._data.get(coll, []) if _matches(d, flt))

    async def delete_many(self, coll: str, flt: dict[str, Any]) -> int:
        async with self._lock:
            store = self._data.get(coll, [])
            keep = [d for d in store if not _matches(d, flt)]
            removed = len(store) - len(keep)
            self._data[coll] = keep
            return removed

    async def close(self) -> None:
        return None


def _sort_key(v: Any) -> Any:
    if v is None:
        return ""
    if isinstance(v, datetime):
        return v.isoformat()
    return v


class DuplicateKeyError(Exception):
    """Raised by the in-memory repo on a unique-key violation."""


# --- Mongo implementation (lazy imports) -----------------------------------


class MongoRepository:
    """Production async repository using Motor.

    ``motor``/``pymongo`` are imported lazily so this module can be imported in
    environments where those packages are not installed (e.g. offline tests).
    """

    def __init__(self, uri: str, db_name: str) -> None:
        self._uri = uri
        self._db_name = db_name
        self._client = None
        self._db = None

    def _ensure_client(self):
        if self._client is None:
            from motor.motor_asyncio import AsyncIOMotorClient  # lazy import

            self._client = AsyncIOMotorClient(self._uri)
            self._db = self._client[self._db_name]
        return self._db

    async def bootstrap(self, *, seed: bool = False) -> None:
        db = self._ensure_client()
        from pymongo import ASCENDING, DESCENDING  # noqa: F401  lazy import
        from pymongo.errors import OperationFailure

        existing = set(await db.list_collection_names())

        for name, spec in COLLECTION_SPECS.items():
            # Create collection with special options where needed.
            if name not in existing:
                try:
                    if spec.time_series:
                        await db.create_collection(name, timeseries=spec.time_series)
                    elif spec.capped_bytes:
                        await db.create_collection(name, capped=True, size=spec.capped_bytes)
                    else:
                        await db.create_collection(name)
                except OperationFailure:
                    pass  # already exists / race

            coll = db[name]
            for idx in spec.indexes:
                kwargs: dict[str, Any] = {}
                if idx.unique:
                    kwargs["unique"] = True
                if idx.ttl_seconds is not None:
                    kwargs["expireAfterSeconds"] = idx.ttl_seconds
                if idx.name:
                    kwargs["name"] = idx.name
                try:
                    await coll.create_index(idx.keys, **kwargs)
                except OperationFailure:
                    pass

            # Best-effort Atlas vector search index creation.
            for vidx in spec.vector_indexes:
                await self._try_create_vector_index(coll, vidx)

        if seed:
            from levy.seed import seed_repository

            await seed_repository(self)

    async def _try_create_vector_index(self, coll, vidx) -> None:
        """Best-effort creation of an Atlas Vector Search index.

        Silently no-ops on clusters that do not support ``search_indexes`` (e.g.
        local mongod), so bootstrap never fails on non-Atlas deployments.
        """
        try:
            from pymongo.operations import SearchIndexModel

            definition = {
                "fields": [
                    {
                        "type": "vector",
                        "path": vidx.field,
                        "numDimensions": vidx.dimensions,
                        "similarity": "cosine",
                    },
                    *[{"type": "filter", "path": f} for f in vidx.filters],
                ]
            }
            model = SearchIndexModel(
                definition=definition, name=vidx.name or f"{vidx.field}_idx", type="vectorSearch"
            )
            await coll.create_search_index(model=model)
        except Exception:
            # Vector search unsupported on this tier/deployment; ignore.
            return None

    async def insert(self, coll: str, doc: dict[str, Any]) -> dict[str, Any]:
        db = self._ensure_client()
        from pymongo.errors import DuplicateKeyError as PyMongoDuplicateKeyError

        try:
            await db[coll].insert_one(copy.deepcopy(doc))
        except PyMongoDuplicateKeyError as exc:
            raise DuplicateKeyError(f"duplicate key in {coll}") from exc
        return doc

    async def find_one(self, coll: str, flt: dict[str, Any]) -> Optional[dict[str, Any]]:
        db = self._ensure_client()
        return await db[coll].find_one(flt, {"_id": 0} if False else None)

    async def find(
        self,
        coll: str,
        flt: Optional[dict[str, Any]] = None,
        *,
        sort: Optional[list[tuple[str, int]]] = None,
        limit: int = 0,
    ) -> list[dict[str, Any]]:
        db = self._ensure_client()
        cursor = db[coll].find(flt or {})
        if sort:
            cursor = cursor.sort(sort)
        if limit:
            cursor = cursor.limit(limit)
        return [d async for d in cursor]

    async def update_one(
        self, coll: str, flt: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> Optional[dict[str, Any]]:
        db = self._ensure_client()
        if not any(k.startswith("$") for k in update):
            update = {"$set": update}
        await db[coll].update_one(flt, update, upsert=upsert)
        return await db[coll].find_one(flt)

    async def find_one_and_update(
        self,
        coll: str,
        flt: dict[str, Any],
        update: dict[str, Any],
        *,
        return_after: bool = True,
    ) -> Optional[dict[str, Any]]:
        db = self._ensure_client()
        from pymongo import ReturnDocument

        rd = ReturnDocument.AFTER if return_after else ReturnDocument.BEFORE
        return await db[coll].find_one_and_update(flt, update, return_document=rd)

    async def count(self, coll: str, flt: Optional[dict[str, Any]] = None) -> int:
        db = self._ensure_client()
        return await db[coll].count_documents(flt or {})

    async def delete_many(self, coll: str, flt: dict[str, Any]) -> int:
        db = self._ensure_client()
        res = await db[coll].delete_many(flt)
        return res.deleted_count

    async def close(self) -> None:
        if self._client is not None:
            self._client.close()


# --- Factory ---------------------------------------------------------------

_repo_singleton: Optional[Repository] = None


def make_repository(settings=None) -> Repository:
    from levy.settings import get_settings

    settings = settings or get_settings()
    if settings.offline:
        return InMemoryRepository()
    return MongoRepository(settings.mongo_uri, settings.mongo_db)


async def get_repository(settings=None) -> Repository:
    """Return a process-wide singleton repository (bootstrapped)."""
    global _repo_singleton
    if _repo_singleton is None:
        _repo_singleton = make_repository(settings)
        await _repo_singleton.bootstrap()
    return _repo_singleton


def set_repository(repo: Optional[Repository]) -> None:
    global _repo_singleton
    _repo_singleton = repo
