from __future__ import annotations

from dataclasses import dataclass
from typing import Any


from rep_grow.fetcher import CachedFetcher


@dataclass(frozen=True)
class DummyCtx:
    key_value: str

    def key(self) -> str:
        return self.key_value

    def __hash__(self) -> int:
        return hash(self.key_value)


class DummyStore:
    def __init__(self, payload: dict[str, Any] | None = None):
        self.payload = payload
        self.get_calls: list[DummyCtx] = []
        self.put_calls: list[tuple[dict[str, Any], DummyCtx]] = []

    def get(self, ctx: DummyCtx):
        self.get_calls.append(ctx)
        return self.payload

    def put(self, payload: dict[str, Any], ctx: DummyCtx):
        self.put_calls.append((payload, ctx))
        self.payload = payload


class DummyFetcher(CachedFetcher[DummyCtx, dict]):
    def __init__(self, ctx: DummyCtx, store: DummyStore):
        super().__init__(ctx=ctx, cache_store=store)

    def fetch(self, value: dict, use_cache: bool = True) -> dict:
        if use_cache and self._response is not None:
            self._last_response_source = "cache"
            return self._response
        self._response = value
        self._record(self._response)
        self._last_response_source = "network"
        return self._response

    def _hydrate(self, payload: dict) -> dict:
        return dict(payload)

    def _serialize(self, response: dict) -> dict:
        return dict(response)


def test_cached_fetcher_uses_cache_first():
    cached = {"value": 1}
    store = DummyStore(payload=cached)
    ctx = DummyCtx("ctx1")

    fetcher = DummyFetcher(ctx, store)
    result = fetcher.fetch({"value": 2}, use_cache=True)

    assert result == cached
    assert fetcher.last_response_source == "cache"
    assert store.put_calls == []
    assert len(store.get_calls) == 1


def test_cached_fetcher_records_when_missing():
    store = DummyStore(payload=None)
    ctx = DummyCtx("ctx2")

    fetcher = DummyFetcher(ctx, store)
    result = fetcher.fetch({"value": 5}, use_cache=True)

    assert result == {"value": 5}
    assert fetcher.last_response_source == "network"
    assert store.put_calls[-1][0] == {"value": 5}
    assert store.put_calls[-1][1] == ctx
    assert len(store.get_calls) == 1
