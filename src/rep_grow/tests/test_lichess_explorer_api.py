import asyncio
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from rep_grow.lichess_explorer_api import ExplorerResponse, LichessExplorerApi


def _fake_api_with_moves(move_totals: list[tuple[str, int]]) -> LichessExplorerApi:
    api = LichessExplorerApi(fen="test")
    moves: list[dict] = []
    recent_games: list[dict] = []
    top_games: list[dict] = []
    for idx, (san, total) in enumerate(move_totals):
        moves.append(
            {
                "uci": f"move{idx}",
                "san": san,
                "white": total,
                "draws": 0,
                "black": 0,
                "opening": None,
            }
        )
    aggregate = sum(total for _, total in move_totals)
    api._response = ExplorerResponse(  # type: ignore[attr-defined]
        opening=None,
        white=aggregate,
        draws=0,
        black=0,
        moves=moves,
        recentGames=recent_games,
        topGames=top_games,
    )
    return api


def test_params():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessExplorerApi(
        fen,
        variant="chess960",
        play="white",
        speeds="blitz,classical",
        ratings=[1200, 1400, 1600],
        since="2020-01",
        until="2020-12",
        moves="10",
        topGames=5,
        recentGames=3,
        history="true",
    )
    expected_params = {
        "variant": "chess960",
        "fen": fen,
        "play": "white",
        "speeds": "blitz,classical",
        "ratings": "1200,1400,1600",
        "since": "2020-01",
        "until": "2020-12",
        "moves": "10",
        "topGames": "5",
        "recentGames": "3",
        "history": "true",
    }
    assert api.params == expected_params


@pytest.mark.asyncio
async def test_explorer_write_json():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessExplorerApi(fen=fen, variant="standard")
    _ = await api.raw_explorer()
    filepath = "explorer_response.json"
    api.response.write_json(filepath=str(filepath))

    with open(filepath, "r") as f:
        content = f.read()

    assert content == api.response.to_json(), (
        f"Expected file content to match response JSON. "
        f"Expected: {api.response.to_json()}, Got: {content}"
    )


@pytest.mark.asyncio
async def test_raw_explorer():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessExplorerApi(fen=fen, variant="standard")
    _ = await api.raw_explorer()
    expected_response = api.response

    async with httpx.AsyncClient() as client:
        r = await client.get(api.BASE_URL, params=api.params)
        r.raise_for_status()

    assert r.json() is not None
    assert r.json() == expected_response.model_dump(), (
        f"Expected {expected_response.model_dump()}, got {r.json()}"
    )

    assert isinstance(api.response.totalGames, int)
    assert isinstance(api.response.white, int)
    assert isinstance(api.response.black, int)
    assert isinstance(api.response.draws, int)

    assert isinstance(api.response.moves, list)
    for move_entry in api.response.moves:
        assert "uci" in move_entry
        assert "white" in move_entry
        assert "black" in move_entry
        assert "opening" in move_entry


@pytest.mark.asyncio
async def test_move_list_and_totals_properties():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessExplorerApi(fen=fen, variant="standard")

    _ = await api.raw_explorer()

    move_list = api.move_list
    totals = api.totals

    assert isinstance(move_list, list), f"Expected list, got {type(move_list)}"
    for entry in move_list:
        assert len(entry) == 4, (
            f"Expected 4 elements (san, white, draws, black), got {len(entry)}"
        )

    assert isinstance(totals, list), f"Expected list, got {type(totals)}"
    for entry in totals:
        assert len(entry) == 2, f"Expected 2 elements (san, total), got {len(entry)}"


@pytest.mark.asyncio
async def test_top_p_pct_moves():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessExplorerApi(fen=fen, variant="standard")

    _ = await api.raw_explorer()

    top_moves = api.top_p_pct_moves(pct=90.0, max_moves=None, min_game_share=0.0)

    assert isinstance(top_moves, list), f"Expected list, got {type(top_moves)}"
    assert len(top_moves) > 0, "Expected at least one move in top moves"

    total_games = sum(total for move, total in api.totals)
    cumulative = 0
    for entry in top_moves:
        cumulative += entry["total"]

    pct_covered = (cumulative / total_games) * 100
    assert pct_covered >= 90.0, (
        f"Expected at least 90% coverage, got {pct_covered:.2f}%"
    )


def test_top_p_pct_moves_limits_tail_and_sorts():
    api = _fake_api_with_moves(
        [
            ("Nc3", 5),
            ("e4", 50),
            ("d4", 25),
            ("c4", 10),
            ("g3", 8),
            ("b3", 2),
        ]
    )

    top_moves = api.top_p_pct_moves(pct=90.0)

    totals = [entry["total"] for entry in top_moves]
    assert totals == sorted(totals, reverse=True)
    assert len(top_moves) <= 8

    total_games = sum(total for _, total in api.totals)
    covered = sum(entry["total"] for entry in top_moves)
    assert covered / total_games >= 0.9


def test_top_p_pct_moves_obeys_max_moves_override():
    api = _fake_api_with_moves(
        [
            ("e4", 40),
            ("d4", 30),
            ("c4", 20),
            ("Nf3", 10),
        ]
    )

    top_moves = api.top_p_pct_moves(pct=99.0, max_moves=2)

    assert len(top_moves) == 2
    assert [entry["move"] for entry in top_moves] == ["e4", "d4"]


def test_top_p_pct_moves_skips_tiny_tail_when_pct_met():
    api = _fake_api_with_moves(
        [
            ("e4", 40),
            ("d4", 30),
            ("c4", 20),
            ("Nf3", 10),
        ]
    )

    top_moves = api.top_p_pct_moves(pct=70.0, min_game_share=0.05)

    assert [entry["move"] for entry in top_moves] == ["e4", "d4"]


def test_top_p_pct_moves_handles_zero_games():
    api = _fake_api_with_moves([])
    assert api.top_p_pct_moves() == []


@pytest.mark.asyncio
async def test_raw_explorer_uses_cache_short_circuit(monkeypatch):
    api = LichessExplorerApi(fen="cached")
    api._response = ExplorerResponse(  # type: ignore[attr-defined]
        opening=None,
        white=1,
        draws=0,
        black=0,
        moves=[],
        recentGames=[],
        topGames=[],
    )

    class FailClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):  # pragma: no cover - should not run
            raise AssertionError("network should not be invoked when cache is warm")

    monkeypatch.setattr(httpx, "AsyncClient", FailClient)

    result = await api.raw_explorer(use_cache=True)

    assert result is api._response
    assert api.last_response_source == "cache"


@pytest.mark.asyncio
async def test_raw_explorer_retries_with_retry_after_seconds(monkeypatch):
    req = httpx.Request("GET", LichessExplorerApi.BASE_URL)

    error = httpx.Response(429, headers={"Retry-After": "1"}, request=req)
    payload = {
        "opening": None,
        "white": 1,
        "draws": 0,
        "black": 0,
        "moves": [],
        "recentGames": [],
        "topGames": [],
    }
    success = httpx.Response(200, json=payload, request=req)
    responses = [error, success]
    calls: list[dict | None] = []

    class StubClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            calls.append(kwargs.get("params"))
            return responses.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", StubClient)

    slept: list[float] = []

    async def fake_sleep(delay: float):
        slept.append(delay)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    api = LichessExplorerApi(fen="retry")
    result = await api.raw_explorer(retries=2, backoff=0.1, jitter=0.0, use_cache=False)

    assert result.totalGames == 1
    assert api.last_response_source == "network"
    assert len(calls) == 2
    assert slept and slept[0] >= 1.0  # respects Retry-After header


@pytest.mark.asyncio
async def test_raw_explorer_raises_last_error_when_retries_exhausted(monkeypatch):
    req = httpx.Request("GET", LichessExplorerApi.BASE_URL)
    failure = httpx.Response(503, request=req)
    responses = [failure, failure]

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return responses.pop(0)

    monkeypatch.setattr(httpx, "AsyncClient", StubClient)

    async def fake_sleep(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)

    api = LichessExplorerApi(fen="fail")

    with pytest.raises(httpx.HTTPStatusError) as excinfo:
        await api.raw_explorer(retries=2, backoff=0.01, jitter=0.0, use_cache=False)

    err = excinfo.value
    assert isinstance(err, httpx.HTTPStatusError)
    assert err.response is not None
    assert err.response.status_code == 503


def test_parse_retry_after_http_date():
    future = datetime.now(timezone.utc) + timedelta(seconds=2)
    header = future.strftime("%a, %d %b %Y %H:%M:%S GMT")
    delay = LichessExplorerApi._parse_retry_after(header)

    assert delay is not None
    assert 0.0 <= delay <= 2.5
