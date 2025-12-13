from __future__ import annotations

from typing import Any, Protocol, TypeVar, runtime_checkable


@runtime_checkable
class CacheableResponse(Protocol):
    """Response objects that can be serialized for cache storage."""

    def to_dict(self) -> dict[str, Any]: ...


@runtime_checkable
class CacheContext(Protocol):
    """Represents a hashable cache lookup context."""

    def key(self) -> str: ...

    def __hash__(self) -> int: ...


TContext = TypeVar("TContext", bound=CacheContext)


class CacheStore(Protocol[TContext]):
    """Abstract cache store interface to support dependency inversion."""

    def get(self, ctx: TContext) -> dict[str, Any] | None: ...

    def put(self, payload: dict[str, Any], ctx: TContext) -> None: ...
