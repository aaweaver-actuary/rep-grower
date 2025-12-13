from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Generic, TypeVar

from .cache import CacheStore, CacheContext

Ctx = TypeVar("Ctx", bound=CacheContext)
Resp = TypeVar("Resp")


class CachedFetcher(ABC, Generic[Ctx, Resp]):
    """Base class that handles cache hydration and bookkeeping."""

    def __init__(self, *, ctx: Ctx, cache_store: CacheStore[Ctx] | None = None):
        self._ctx = ctx
        self._cache_store = cache_store
        self._response: Resp | None = None
        self._last_response_source: str = "uninitialized"

        if self._cache_store is not None:
            cached = self._cache_store.get(self._ctx)
            if cached is not None:
                self._response = self._hydrate(cached)
                self._last_response_source = "cache"

    @property
    def last_response_source(self) -> str:
        return self._last_response_source

    def _record(self, response: Resp) -> Resp:
        """Persist response to cache store if present."""
        if self._cache_store is not None:
            self._cache_store.put(self._serialize(response), self._ctx)
        return response

    @abstractmethod
    def _hydrate(self, payload: dict[str, Any]) -> Resp:
        """Convert cached payload into a response object."""
        raise NotImplementedError

    @abstractmethod
    def _serialize(self, response: Resp) -> dict[str, Any]:
        """Convert response into cacheable payload."""
        raise NotImplementedError
