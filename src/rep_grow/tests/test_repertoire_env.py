import pytest

import asyncio
import time

from rep_grow.repertoire import ExplorerRateLimiter, _env_float, _env_int


def test_env_int_parses_and_falls_back(monkeypatch):
    monkeypatch.delenv("REP_GROW_TEST_INT", raising=False)
    assert _env_int("REP_GROW_TEST_INT", 3) == 3

    monkeypatch.setenv("REP_GROW_TEST_INT", "7")
    assert _env_int("REP_GROW_TEST_INT", 3) == 7

    monkeypatch.setenv("REP_GROW_TEST_INT", "not-a-number")
    assert _env_int("REP_GROW_TEST_INT", 9) == 9


def test_env_float_parses_and_falls_back(monkeypatch):
    monkeypatch.delenv("REP_GROW_TEST_FLOAT", raising=False)
    assert _env_float("REP_GROW_TEST_FLOAT", 1.5) == 1.5

    monkeypatch.setenv("REP_GROW_TEST_FLOAT", "2.75")
    assert _env_float("REP_GROW_TEST_FLOAT", 1.5) == 2.75

    monkeypatch.setenv("REP_GROW_TEST_FLOAT", "invalid")
    assert _env_float("REP_GROW_TEST_FLOAT", 0.25) == 0.25


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
