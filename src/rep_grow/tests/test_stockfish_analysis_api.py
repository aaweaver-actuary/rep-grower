import chess
import pytest

import rep_grow.stockfish_analysis_api as stockfish_module
from rep_grow.stockfish_analysis_api import StockfishAnalysisApi


class FakePovScore:
    def __init__(self, cp: int | None = None, mate: int | None = None):
        self._cp = cp
        self._mate = mate

    def pov(self, color):  # pragma: no cover - simple passthrough
        return self

    def score(self):
        return self._cp

    def mate(self):
        return self._mate


class FakeEngine:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def analyse(self, board, limit, multipv):
        self.calls.append((board.fen(), limit, multipv))
        return self.response

    def quit(self):  # pragma: no cover - cleanup hook
        pass

    def __enter__(self):  # pragma: no cover
        return self

    def __exit__(self, exc_type, exc, tb):  # pragma: no cover
        self.quit()


@pytest.fixture
def patch_engine(monkeypatch):
    created = {}

    def _patch(response):
        engine = FakeEngine(response)

        def popen(path):
            created["path"] = path
            return engine

        simple_engine = stockfish_module.chess.engine.SimpleEngine
        monkeypatch.setattr(simple_engine, "popen_uci", staticmethod(popen))
        return engine, created

    return _patch


def test_params_include_defaults():
    fen = chess.STARTING_FEN
    api = StockfishAnalysisApi(fen)

    params = api.params()
    assert params["enginePath"] == "/opt/homebrew/bin/stockfish"
    assert params["thinkTime"] is None
    assert params["multiPv"] == str(api.multi_pv)


def test_params_include_think_time_override():
    api = StockfishAnalysisApi(chess.STARTING_FEN, think_time=0.75, depth=0)
    params = api.params()
    assert params["thinkTime"] == "0.75"
    assert params["depth"] == "0"


def test_init_validates_multi_pv():
    with pytest.raises(ValueError):
        StockfishAnalysisApi(chess.STARTING_FEN, multi_pv=0)


def test_init_requires_search_constraints():
    with pytest.raises(ValueError):
        StockfishAnalysisApi(chess.STARTING_FEN, depth=0, think_time=None)

    # depth=0 but think time positive should pass
    StockfishAnalysisApi(chess.STARTING_FEN, depth=0, think_time=0.3)


@pytest.mark.asyncio
async def test_raw_evaluation_converts_engine_response(patch_engine):
    response = [
        {
            "pv": [chess.Move.from_uci("e2e4"), chess.Move.from_uci("e7e5")],
            "score": FakePovScore(cp=80),
            "depth": 22,
            "nodes": 410000,
        },
        {
            "pv": [chess.Move.from_uci("d2d4")],
            "score": FakePovScore(cp=35),
            "depth": 21,
            "nodes": 320000,
        },
    ]
    engine, meta = patch_engine(response)

    api = StockfishAnalysisApi(chess.STARTING_FEN, multi_pv=2)
    await api.raw_evaluation()

    assert meta["path"] == "/opt/homebrew/bin/stockfish"
    assert len(engine.calls) == 1
    call_fen, _, call_multipv = engine.calls[0]
    assert call_fen == chess.STARTING_FEN
    assert call_multipv == 2

    resp = api.response
    assert resp.depth == 22
    assert resp.knodes == 410

    moves = api.moves
    assert moves[0] == (80, "e2e4")
    assert moves[1] == (35, "d2d4")
    assert api.best_move == "e2e4"
    assert api.best_score == 80


@pytest.mark.asyncio
async def test_raw_evaluation_accepts_single_dict_response(patch_engine):
    single_response = {
        "pv": [chess.Move.from_uci("g1f3")],
        "score": FakePovScore(cp=12),
        "depth": 18,
        "nodes": 20000,
    }
    patch_engine(single_response)

    api = StockfishAnalysisApi(chess.STARTING_FEN, multi_pv=1)
    await api.raw_evaluation()

    assert len(api.moves) == 1
    assert api.moves[0] == (12, "g1f3")


@pytest.mark.asyncio
async def test_scores_within_threshold_uses_absolute_difference(patch_engine):
    response = [
        {
            "pv": [chess.Move.from_uci("c2c4")],
            "score": FakePovScore(cp=50),
            "depth": 20,
            "nodes": 100000,
        },
        {
            "pv": [chess.Move.from_uci("d2d3")],
            "score": FakePovScore(cp=40),
            "depth": 20,
            "nodes": 100000,
        },
        {
            "pv": [chess.Move.from_uci("a2a3")],
            "score": FakePovScore(cp=10),
            "depth": 20,
            "nodes": 100000,
        },
    ]
    patch_engine(response)

    api = StockfishAnalysisApi(chess.STARTING_FEN, multi_pv=3)
    await api.raw_evaluation()

    within_15 = api.scores_within(15)
    assert within_15 == [(50, "c2c4"), (40, "d2d3")]
    assert api.moves_within(15) == ["c2c4", "d2d3"]


@pytest.mark.asyncio
async def test_handles_mate_scores_when_cp_missing(patch_engine):
    response = [
        {
            "pv": [chess.Move.from_uci("h5f7")],
            "score": FakePovScore(cp=None, mate=2),
            "depth": 24,
            "nodes": 50000,
        },
        {
            "pv": [chess.Move.from_uci("d1h5")],
            "score": FakePovScore(cp=None, mate=4),
            "depth": 24,
            "nodes": 50000,
        },
    ]
    patch_engine(response)

    api = StockfishAnalysisApi(chess.STARTING_FEN, multi_pv=2)
    await api.raw_evaluation()

    moves = api.moves
    scores = sorted(score for score, _ in moves)
    assert scores == [2, 4]
    assert api.best_score in {2, 4}
    assert api.best_move in {"h5f7", "d1h5"}


@pytest.mark.asyncio
async def test_missing_engine_raises_runtime(monkeypatch):
    def raise_error(_path):
        raise FileNotFoundError("no binary")

    simple_engine = stockfish_module.chess.engine.SimpleEngine
    monkeypatch.setattr(simple_engine, "popen_uci", staticmethod(raise_error))

    api = StockfishAnalysisApi(chess.STARTING_FEN)
    with pytest.raises(RuntimeError) as excinfo:
        await api.raw_evaluation()

    assert "Stockfish binary not found" in str(excinfo.value)


@pytest.mark.asyncio
async def test_depth_zero_uses_time_limit(patch_engine):
    response = {
        "pv": [chess.Move.from_uci("e2e3")],
        "score": FakePovScore(cp=5),
        "depth": 1,
        "nodes": 1000,
    }
    engine, _ = patch_engine(response)

    api = StockfishAnalysisApi(chess.STARTING_FEN, depth=0, think_time=0.1)
    await api.raw_evaluation()

    _, limit_obj, _ = engine.calls[0]
    assert hasattr(limit_obj, "time")
    assert limit_obj.time == pytest.approx(0.1, rel=0, abs=1e-9)
