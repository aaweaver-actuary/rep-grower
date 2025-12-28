from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Awaitable, Callable, Mapping, Sequence

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AnalysisTask:
    fen: str
    multi_pv: int
    depth: int | None
    best_score_threshold: int
    variant: str = "standard"


ProviderFn = Callable[[AnalysisTask], Awaitable[list[str]]]


class AnalysisQueue:
    """Small async queue that tries providers in order with bounded concurrency."""

    def __init__(
        self,
        providers: Mapping[str, ProviderFn],
        order: Sequence[str],
        *,
        max_concurrency: int = 4,
        on_success: Callable[[str, AnalysisTask], None] | None = None,
    ) -> None:
        self._providers = {name.lower(): fn for name, fn in providers.items()}
        self._order = [name.lower() for name in order if name]
        self._semaphore = asyncio.Semaphore(max(1, max_concurrency))
        self._on_success = on_success

    @staticmethod
    def _suppressible(exc: Exception) -> bool:
        text = str(exc)
        return (
            "No cloud evaluation available for that position" in text or " 404" in text
        )

    async def evaluate(self, task: AnalysisTask) -> list[str]:
        if not self._order:
            raise RuntimeError("No analysis providers configured")

        last_error: Exception | None = None
        async with self._semaphore:
            for name in self._order:
                provider = self._providers.get(name)
                if provider is None:
                    logger.warning("Unknown analysis provider '%s'", name)
                    continue
                try:
                    result = await provider(task)
                    if self._on_success is not None:
                        self._on_success(name, task)
                    return result
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    if self._suppressible(exc):
                        continue
                    logger.warning(
                        "Analysis provider '%s' failed for %s: %s", name, task.fen, exc
                    )
                    continue
        if last_error is not None:
            raise last_error
        raise RuntimeError("No analysis provider succeeded")
