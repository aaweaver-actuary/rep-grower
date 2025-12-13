from __future__ import annotations

from typing import TypedDict

import httpx
import pytest

from rep_grow.lichess_explorer_api import LichessExplorerApi
from rep_grow.stockfish_analysis_api import StockfishAnalysisApi
from rep_grow.db import DuckDb, DbQueryContext, ExplorerQueryContext


class ExplorerPayload(TypedDict):
    opening: None
    white: int
    draws: int
    black: int
    moves: list[dict[str, int | str]]
    recentGames: list[dict]
    topGames: list[dict]


class FakeEval(TypedDict):
    depth: int
    fen: str
    knodes: int
    pvs: list[dict[str, int | str]]


@pytest.mark.asyncio
async def test_explorer_cache_persists(monkeypatch, tmp_path):
    payload: ExplorerPayload = {
        "opening": None,
        "white": 12,
        "draws": 3,
        "black": 5,
        "moves": [
            {"san": "e4", "white": 7, "draws": 2, "black": 1},
            {"san": "d4", "white": 3, "draws": 1, "black": 2},
        ],
        "recentGames": [],
        "topGames": [],
    }
    response = httpx.Response(
        200,
        json=payload,
        request=httpx.Request("GET", LichessExplorerApi.BASE_URL),
    )
    call_count = {"count": 0}

    def client_factory(*args, **kwargs):
        class DummyAsyncClient:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            async def get(self_inner, *args, **kwargs):
                call_count["count"] += 1
                return response

        return DummyAsyncClient()

    monkeypatch.setattr(
        "rep_grow.lichess_explorer_api.httpx.AsyncClient",
        client_factory,
    )

    db_path = tmp_path / "cache.duckdb"
    api = LichessExplorerApi(
        fen="8/8/8/8/8/8/8/8 w - - 0 1",
        db_path=db_path,
    )

    first = await api.raw_explorer()
    assert api.last_response_source == "network"
    assert call_count["count"] == 1
    assert first.totalGames == payload["white"] + payload["black"] + payload["draws"]

    api_again = LichessExplorerApi(
        fen="8/8/8/8/8/8/8/8 w - - 0 1",
        db_path=db_path,
    )
    cached = await api_again.raw_explorer()
    assert api_again.last_response_source == "cache"
    assert call_count["count"] == 1
    assert cached.white == first.white


@pytest.mark.asyncio
async def test_stockfish_cache_persists(monkeypatch, tmp_path):
    fake_eval: FakeEval = {
        "depth": 12,
        "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
        "knodes": 42,
        "pvs": [
            {"cp": 20, "moves": "e2e4 e7e5"},
            {"cp": 10, "moves": "d2d4 d7d5"},
        ],
    }
    call_count = {"count": 0}

    def fake_eval_fn(self):
        call_count["count"] += 1
        return fake_eval

    monkeypatch.setattr(StockfishAnalysisApi, "_evaluate_position", fake_eval_fn)

    db_path = tmp_path / "cache.duckdb"
    api = StockfishAnalysisApi(
        fen=fake_eval["fen"],
        multi_pv=2,
        depth=fake_eval["depth"],
        db_path=db_path,
    )

    first = await api.raw_evaluation()
    assert call_count["count"] == 1
    assert api.last_response_source == "engine"
    assert first.moves[0][1] == "e2e4"

    api_again = StockfishAnalysisApi(
        fen=fake_eval["fen"],
        multi_pv=2,
        depth=fake_eval["depth"],
        db_path=db_path,
    )
    cached = await api_again.raw_evaluation()
    assert api_again.last_response_source == "cache"
    assert call_count["count"] == 1
    assert cached.pvs == first.pvs


@pytest.mark.asyncio
async def test_explorer_uses_db_before_network(monkeypatch, tmp_path):
    payload: ExplorerPayload = {
        "opening": None,
        "white": 2,
        "draws": 1,
        "black": 3,
        "moves": [
            {"san": "e4", "white": 1, "draws": 0, "black": 1},
        ],
        "recentGames": [],
        "topGames": [],
    }

    db_path = tmp_path / "cache.duckdb"
    db = DuckDb(db_path=db_path)
    ctx = ExplorerQueryContext(
        fen="8/8/8/8/8/8/8/8 w - - 0 1",
        variant="standard",
        play="",
        speeds="ultraBullet,bullet,blitz,rapid",
        ratings="0,1000,1200,1400,1600,1800,2000,2200,2500",
        since="1952-01",
        until="3000-12",
        moves="15",
        top_games=0,
        recent_games=0,
        history="false",
    )
    db.put_explorer(payload, ctx)

    call_count = {"count": 0}

    def client_factory(*args, **kwargs):
        class DummyAsyncClient:
            async def __aenter__(self_inner):
                return self_inner

            async def __aexit__(self_inner, exc_type, exc, tb):
                return False

            async def get(self_inner, *args, **kwargs):
                call_count["count"] += 1
                raise AssertionError("Network should not be called when cached")

        return DummyAsyncClient()

    monkeypatch.setattr(
        "rep_grow.lichess_explorer_api.httpx.AsyncClient",
        client_factory,
    )

    api = LichessExplorerApi(
        fen=ctx.fen,
        db_path=db_path,
    )
    cached = await api.raw_explorer()

    assert api.last_response_source == "cache"
    assert call_count["count"] == 0
    assert cached.totalGames == payload["white"] + payload["black"] + payload["draws"]


@pytest.mark.asyncio
async def test_stockfish_uses_db_before_engine(monkeypatch, tmp_path):
    fake_eval: FakeEval = {
        "depth": 10,
        "fen": "8/8/8/8/8/8/8/8 w - - 0 1",
        "knodes": 7,
        "pvs": [
            {"cp": 15, "moves": "e2e4 e7e5"},
        ],
    }

    db_path = tmp_path / "cache.duckdb"
    db = DuckDb(db_path=db_path)
    ctx = DbQueryContext(fen=fake_eval["fen"], multipv=1, depth=fake_eval["depth"])
    db.put(fake_eval, ctx)

    def never_called(self):  # pragma: no cover - would indicate failure
        raise AssertionError("Engine should not run when cached")

    monkeypatch.setattr(StockfishAnalysisApi, "_evaluate_position", never_called)

    api = StockfishAnalysisApi(
        fen=fake_eval["fen"],
        multi_pv=1,
        depth=fake_eval["depth"],
        db_path=db_path,
    )

    cached = await api.raw_evaluation()

    assert api.last_response_source == "cache"
    assert cached.moves[0][1] == "e2e4"
