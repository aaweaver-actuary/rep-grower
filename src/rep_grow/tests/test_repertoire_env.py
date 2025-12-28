import pytest

import asyncio
import time

from rep_grow.repertoire import ExplorerRateLimiter, RuntimeConfig


def test_runtime_config_parses_and_falls_back(monkeypatch):
    monkeypatch.delenv("REP_GROW_EXPLORER_MAX_CONCURRENCY", raising=False)
    monkeypatch.delenv("REP_GROW_EXPLORER_MIN_DELAY", raising=False)

    defaults = RuntimeConfig.from_env()
    assert defaults.explorer_max_concurrency == 1
    assert defaults.explorer_min_delay == 1.0

    monkeypatch.setenv("REP_GROW_EXPLORER_MAX_CONCURRENCY", "3")
    monkeypatch.setenv("REP_GROW_EXPLORER_MIN_DELAY", "0.25")
    parsed = RuntimeConfig.from_env()
    assert parsed.explorer_max_concurrency == 3
    assert parsed.explorer_min_delay == 0.25

    monkeypatch.setenv("REP_GROW_EXPLORER_MAX_CONCURRENCY", "invalid")
    monkeypatch.setenv("REP_GROW_EXPLORER_MIN_DELAY", "bad")
    fallback = RuntimeConfig.from_env()
    assert fallback.explorer_max_concurrency == 1
    assert fallback.explorer_min_delay == 1.0


@pytest.mark.asyncio
async def test_explorer_rate_limiter_serializes_and_delays():
    limiter = ExplorerRateLimiter(max_concurrent=1, min_delay=0.05)
    starts: list[float] = []

    async def run():
        async with limiter:
            starts.append(time.perf_counter())
            await asyncio.sleep(0.0)

    await asyncio.gather(run(), run())

    assert len(starts) == 2
    assert starts[1] - starts[0] >= 0.04
