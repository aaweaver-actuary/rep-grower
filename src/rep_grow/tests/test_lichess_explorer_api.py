from rep_grow.lichess_explorer_api import LichessExplorerApi
import pytest
import httpx


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

    top_moves = api.top_p_pct_moves(pct=90.0)

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
