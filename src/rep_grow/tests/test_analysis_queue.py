import logging

import pytest

from rep_grow.analysis_queue import AnalysisQueue, AnalysisTask


@pytest.mark.asyncio
async def test_evaluate_returns_first_success_and_calls_callback():
    calls: list[tuple[str, str]] = []

    async def primary(task: AnalysisTask) -> list[str]:
        return ["primary", task.fen]

    secondary_called = False

    async def secondary(task: AnalysisTask) -> list[str]:
        nonlocal secondary_called
        secondary_called = True
        return ["secondary", task.fen]

    queue = AnalysisQueue(
        {"primary": primary, "secondary": secondary},
        ["primary", "secondary"],
        on_success=lambda name, task: calls.append((name, task.fen)),
    )

    task = AnalysisTask(fen="start", multi_pv=2, depth=None, best_score_threshold=10)
    result = await queue.evaluate(task)

    assert result == ["primary", "start"]
    assert calls == [("primary", "start")]
    assert secondary_called is False


@pytest.mark.asyncio
async def test_evaluate_warns_on_missing_provider_and_continues(
    caplog: pytest.LogCaptureFixture,
):
    async def good(task: AnalysisTask) -> list[str]:
        return ["ok", task.fen]

    queue = AnalysisQueue({"good": good}, ["missing", "GOOD"])

    caplog.set_level(logging.WARNING)
    task = AnalysisTask(fen="start", multi_pv=1, depth=12, best_score_threshold=5)
    result = await queue.evaluate(task)

    assert result == ["ok", "start"]
    assert "Unknown analysis provider 'missing'" in caplog.text


@pytest.mark.asyncio
async def test_evaluate_raises_last_suppressible_error():
    async def first(_: AnalysisTask) -> list[str]:
        raise ValueError("No cloud evaluation available for that position")

    async def second(_: AnalysisTask) -> list[str]:
        raise ValueError(" 404")

    queue = AnalysisQueue({"first": first, "second": second}, ["first", "second"])
    task = AnalysisTask(fen="fen", multi_pv=1, depth=None, best_score_threshold=5)

    with pytest.raises(ValueError) as excinfo:
        await queue.evaluate(task)

    assert "404" in str(excinfo.value)


@pytest.mark.asyncio
async def test_evaluate_logs_non_suppressible_and_falls_back(
    caplog: pytest.LogCaptureFixture,
):
    async def failing(_: AnalysisTask) -> list[str]:
        raise RuntimeError("boom")

    async def succeeding(task: AnalysisTask) -> list[str]:
        return ["ok", task.fen]

    queue = AnalysisQueue(
        {"failing": failing, "succeeding": succeeding}, ["failing", "succeeding"]
    )
    task = AnalysisTask(fen="fen", multi_pv=3, depth=8, best_score_threshold=15)

    caplog.set_level(logging.WARNING)
    result = await queue.evaluate(task)

    assert result == ["ok", "fen"]
    assert "Analysis provider 'failing' failed" in caplog.text


@pytest.mark.asyncio
async def test_evaluate_errors_when_no_providers_configured():
    queue = AnalysisQueue({}, [])
    task = AnalysisTask(fen="fen", multi_pv=1, depth=None, best_score_threshold=5)

    with pytest.raises(RuntimeError):
        await queue.evaluate(task)


@pytest.mark.asyncio
async def test_evaluate_errors_when_all_providers_missing():
    queue = AnalysisQueue({}, ["first", "second"])
    task = AnalysisTask(fen="fen", multi_pv=1, depth=None, best_score_threshold=5)

    with pytest.raises(RuntimeError, match="No analysis provider succeeded"):
        await queue.evaluate(task)
