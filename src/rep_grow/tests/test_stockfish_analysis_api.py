import pytest

import rep_grow.stockfish_analysis_api as stockfish_module
from rep_grow.stockfish_analysis_api import StockfishAnalysisApi
from rep_grow.requests import StockfishRequest


def stockfish_payload(
    *,
    fen: str,
    pvs: list[dict],
    depth: int = 22,
    knodes: int = 410,
) -> dict:
    return {
        "depth": depth,
        "fen": fen,
        "knodes": knodes,
        "pvs": pvs,
    }


def install_fake_eval(monkeypatch, payload: dict | Exception):
    calls: list[dict] = []

    def fake_eval(fen, engine_path, depth, multi_pv, think_time, pool_size):
        calls.append(
            {
                "fen": fen,
                "engine_path": engine_path,
                "depth": depth,
                "multi_pv": multi_pv,
                "think_time": think_time,
                "pool_size": pool_size,
            }
        )
        if isinstance(payload, Exception):
            raise payload
        return payload

    monkeypatch.setattr(stockfish_module._core, "stockfish_evaluate", fake_eval)
    return calls


def test_params_include_defaults():
    fen = "start"
    api = StockfishAnalysisApi(fen)

    params = api.params()
    assert params["enginePath"] == "/opt/homebrew/bin/stockfish"
    assert params["thinkTime"] is None
    assert params["multiPv"] == str(api.multi_pv)
    assert params["poolSize"] is None


def test_params_include_think_time_override():
    api = StockfishAnalysisApi("start", think_time=0.75, depth=0)
    params = api.params()
    assert params["thinkTime"] == "0.75"
    assert params["depth"] == "0"


def test_init_validates_multi_pv():
    with pytest.raises(ValueError):
        StockfishAnalysisApi("start", multi_pv=0)


def test_init_requires_search_constraints():
    with pytest.raises(ValueError):
        StockfishAnalysisApi("start", depth=0, think_time=None)

    # depth=0 but think time positive should pass
    StockfishAnalysisApi("start", depth=0, think_time=0.3)


@pytest.mark.asyncio
async def test_raw_evaluation_converts_engine_response(monkeypatch):
    fen = "start"
    response = stockfish_payload(
        fen=fen,
        pvs=[
            {"cp": 80, "score": 80, "moves": "e2e4 e7e5"},
            {"cp": 35, "score": 35, "moves": "d2d4 d7d5"},
        ],
    )
    calls = install_fake_eval(monkeypatch, response)

    api = StockfishAnalysisApi(
        request=StockfishRequest(
            fen=fen,
            multi_pv=2,
            depth=22,
            variant="standard",
        ),
    )
    await api.raw_evaluation()

    assert calls[0]["fen"] == fen
    assert calls[0]["multi_pv"] == 2
    resp = api.response
    assert resp.depth == 22
    assert resp.knodes == 410
    assert api.moves[0] == (80, "e2e4")
    assert api.moves[1] == (35, "d2d4")
    assert api.best_move == "e2e4"
    assert api.best_score == 80


@pytest.mark.asyncio
async def test_raw_evaluation_accepts_single_entry(monkeypatch):
    response = stockfish_payload(
        fen="start",
        depth=18,
        knodes=20,
        pvs=[{"cp": 12, "score": 12, "moves": "g1f3 g8f6"}],
    )
    install_fake_eval(monkeypatch, response)

    api = StockfishAnalysisApi("start", multi_pv=1)
    await api.raw_evaluation()

    assert api.moves == [(12, "g1f3")]


@pytest.mark.asyncio
async def test_scores_within_threshold_uses_absolute_difference(monkeypatch):
    response = stockfish_payload(
        fen="start",
        pvs=[
            {"cp": 50, "score": 50, "moves": "c2c4 e7e5"},
            {"cp": 40, "score": 40, "moves": "d2d3 d7d5"},
            {"cp": 10, "score": 10, "moves": "a2a3 a7a6"},
        ],
    )
    install_fake_eval(monkeypatch, response)

    api = StockfishAnalysisApi("start", multi_pv=3)
    await api.raw_evaluation()

    within_15 = api.scores_within(15)
    assert within_15 == [(50, "c2c4"), (40, "d2d3")]
    assert api.moves_within(15) == ["c2c4", "d2d3"]


@pytest.mark.asyncio
async def test_handles_mate_scores_when_cp_missing(monkeypatch):
    response = stockfish_payload(
        fen="start",
        depth=24,
        pvs=[
            {"mate": 2, "score": 2, "moves": "h5f7 e8f7"},
            {"mate": 4, "score": 4, "moves": "d1h5 g7g6"},
        ],
    )
    install_fake_eval(monkeypatch, response)

    api = StockfishAnalysisApi("start", multi_pv=2)
    await api.raw_evaluation()

    moves = api.moves
    scores = sorted(score for score, _ in moves)
    assert scores == [2, 4]
    assert api.best_score in {2, 4}
    assert api.best_move in {"h5f7", "d1h5"}


@pytest.mark.asyncio
async def test_missing_engine_raises_runtime(monkeypatch):
    install_fake_eval(
        monkeypatch,
        RuntimeError("Stockfish binary not found at /bad/path"),
    )

    api = StockfishAnalysisApi("start")
    with pytest.raises(RuntimeError) as excinfo:
        await api.raw_evaluation()

    assert "Stockfish binary not found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_depth_zero_uses_time_limit(monkeypatch):
    response = stockfish_payload(
        fen="start",
        pvs=[{"cp": 5, "score": 5, "moves": "e2e3 e7e6"}],
        depth=1,
    )
    calls = install_fake_eval(monkeypatch, response)

    api = StockfishAnalysisApi("start", depth=0, think_time=0.1)
    await api.raw_evaluation()

    assert calls[0]["think_time"] == pytest.approx(0.1)


@pytest.mark.asyncio
async def test_raw_evaluation_uses_cache_when_present(monkeypatch):
    response = stockfish_payload(
        fen="start",
        pvs=[{"cp": 5, "score": 5, "moves": "e2e4 e7e5"}],
    )
    calls = install_fake_eval(monkeypatch, response)

    api = StockfishAnalysisApi("start")
    first = await api.raw_evaluation(use_cache=True)
    second = await api.raw_evaluation(use_cache=True)

    assert calls == [
        {
            "fen": "start",
            "engine_path": str(api.engine_path),
            "depth": api.depth,
            "multi_pv": api.multi_pv,
            "think_time": api.think_time,
            "pool_size": api.pool_size or api._default_pool_size(),
        }
    ]
    assert first is second
    assert api.last_response_source == "cache"


def test_default_pool_size_clamps_cpu_count(monkeypatch):
    monkeypatch.setattr(stockfish_module.os, "cpu_count", lambda: 0)
    assert StockfishAnalysisApi._default_pool_size() == 1

    monkeypatch.setattr(stockfish_module.os, "cpu_count", lambda: 2)
    assert StockfishAnalysisApi._default_pool_size() == 2

    monkeypatch.setattr(stockfish_module.os, "cpu_count", lambda: 8)
    assert StockfishAnalysisApi._default_pool_size() == 4


def test_init_rejects_small_pool_size():
    with pytest.raises(ValueError):
        StockfishAnalysisApi("start", pool_size=0)
