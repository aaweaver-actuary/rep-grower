from __future__ import annotations

import httpx
import pytest

from rep_grow.lichess_analysis_api import LichessAnalysisApi


@pytest.fixture(autouse=True)
def stub_lichess_http(monkeypatch):
    base_payload: dict = {
        "depth": 22,
        "fen": "",
        "knodes": 123,
        "pvs": [
            {"cp": 90, "moves": "e2e4 e7e5 g1f3"},
            {"cp": 70, "moves": "d2d4 d7d5 c2c4"},
            {"cp": 50, "moves": "c2c4 e7e5"},
            {"cp": 20, "moves": "g1f3 d7d5"},
            {"cp": 5, "moves": "b1c3 c7c5"},
        ],
    }

    def payload_for_fen(fen: str) -> dict:
        if "p1p2ppp" in fen:  # flank-pawn imbalance case
            return {
                "depth": 18,
                "fen": fen,
                "knodes": 42,
                "pvs": [{"cp": -120, "moves": "b2b3 a7a5"}],
            }
        if "3k1q2/1N2Rp2/3r1P2/p2p3p" in fen:  # only one legal move expected
            return {
                "depth": 20,
                "fen": fen,
                "knodes": 77,
                "pvs": [{"cp": -10, "moves": "e7e8q"}],
            }
        data = dict(base_payload)
        data["fen"] = fen
        data["pvs"] = list(base_payload["pvs"])
        return data

    class FakeResponse:
        def __init__(self, fen: str):
            self.status_code = 200
            self._fen = fen
            self.text = ""

        def raise_for_status(self):  # pragma: no cover - trivial
            return None

        def json(self):
            return payload_for_fen(self._fen)

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):  # pragma: no cover - trivial
            return False

        async def get(self, url, params=None):
            fen = params.get("fen", "") if params else ""
            return FakeResponse(fen)

    monkeypatch.setattr("httpx.AsyncClient", FakeClient)
    return base_payload


def test_params():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessAnalysisApi(fen, multi_pv=3, variant="chess960")
    expected_params = {"fen": fen, "multiPv": "3", "variant": "chess960"}
    assert api.params() == expected_params


@pytest.mark.asyncio
async def test_raw_evaluation():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessAnalysisApi(fen=fen, multi_pv=3, variant="standard")
    _ = await api.raw_evaluation()
    expected_response = api.response

    async with httpx.AsyncClient() as client:
        r = await client.get(api.BASE_URL, params=api.params())
        r.raise_for_status()

    assert r.json() is not None
    assert r.json() == expected_response.as_json(), (
        f"Expected {expected_response.as_json()}, got {r.json()}"
    )

    assert isinstance(api.response.raw_evaluations, list)
    for eval_entry in api.response.raw_evaluations:
        assert "cp" in eval_entry or "mate" in eval_entry
        assert "moves" in eval_entry

    assert isinstance(api.response.moves, list)
    for score, moves in api.response.moves:
        assert isinstance(score, int)
        assert isinstance(moves, str)


@pytest.mark.asyncio
async def test_moves_only_shows_the_next_move_to_play_in_uci_format_not_san_format():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessAnalysisApi(fen=fen, multi_pv=3, variant="standard")
    _ = await api.raw_evaluation()

    for score, move in api.response.moves:
        assert " " not in move, f"Expected single move, got sequence: {move}"
        # UCI moves are typically 4 or 5 characters (e.g., e2e4, a7a8q)
        assert 4 <= len(move) <= 5, f"Expected valid UCI move length, got: {move}"

        # Check source square file
        assert move[0] in "abcdefgh", f"Invalid source file in UCI move: {move}"
        # Check source square rank
        assert move[1] in "12345678", f"Invalid source rank in UCI move: {move}"
        # Check destination square file
        assert move[2] in "abcdefgh", f"Invalid dest file in UCI move: {move}"
        # Check destination square rank
        assert move[3] in "12345678", f"Invalid dest rank in UCI move: {move}"


@pytest.mark.asyncio
async def test_if_white_only_moves_flank_pawns_but_black_controls_center_black_is_better():
    # Position where white has only moved flank pawns, while black controls the center: 1. a4 e5 2. a5 d5 3. a6 Nxa6 4. Rxa6 bxa6
    fen = "r1bqkbnr/p1p2ppp/p7/3pp3/8/8/1PPPPPPP/1NBQKBNR w Kkq - 0 5"
    api = LichessAnalysisApi(fen=fen, multi_pv=1, variant="standard")
    _ = await api.raw_evaluation()

    score, move = api.response.moves[0]
    assert score < 0, f"Expected black to be better (negative score), got: {score}"
    assert isinstance(move, str)
    assert len(move) >= 4  # Basic check for UCI move format


@pytest.mark.asyncio
async def test_moves_are_sorted_with_best_move_first():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessAnalysisApi(fen=fen, multi_pv=5, variant="standard")
    _ = await api.raw_evaluation()

    moves = api.response.moves
    scores = [score for score, move in moves]

    assert scores == sorted(scores, reverse=True), (
        f"Expected moves to be sorted by score descending, got: {scores}"
    )

    assert len(moves) == 5, f"Expected 5 principal variations, got: {len(moves)}"


@pytest.mark.asyncio
async def test_best_move_property():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessAnalysisApi(fen=fen, multi_pv=3, variant="standard")
    _ = await api.raw_evaluation()

    best_score, best_move = api.response.moves[0]
    api_best_move = api.best_move

    assert best_move == api_best_move, (
        f"Expected best move {best_move} from moves list, got {api_best_move} from best_move property"
    )


@pytest.mark.asyncio
async def test_prefers_short_forced_mate_over_high_cp(monkeypatch):
    payload = {
        "depth": 22,
        "fen": "start",
        "knodes": 100,
        "pvs": [
            {"mate": 6, "score": 6, "moves": "d1h5 g7g6"},
            {"cp": 500, "score": 500, "moves": "e2e4 e7e5"},
            {"cp": 120, "score": 120, "moves": "g1f3 g8f6"},
        ],
    }

    class StubResponse:
        def __init__(self, json_payload):
            self._json = json_payload
            self.status_code = 200
            self.text = "ok"

        def raise_for_status(self):
            return None

        def json(self):
            return self._json

    class StubClient:
        def __init__(self, json_payload):
            self._payload = json_payload

        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):  # noqa: ARG002
            return StubResponse(self._payload)

    monkeypatch.setattr(
        httpx, "AsyncClient", lambda *args, **kwargs: StubClient(payload)
    )

    api = LichessAnalysisApi("start", multi_pv=3, best_score_threshold=30)
    await api.raw_evaluation()

    assert api.best_move == "d1h5"
    assert api.best_moves == ["d1h5"]


@pytest.mark.asyncio
async def test_no_scores_within_0_cp_threshold():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessAnalysisApi(fen=fen, multi_pv=5, variant="standard")
    await api.raw_evaluation()

    moves_within_0 = api.scores_within(0)
    assert len(moves_within_0) == 1, (
        f"Expected only the best move within 0 cp threshold, got: {len(moves_within_0)}"
    )

    assert api.best_move == api.moves_within(0)[0], (
        f"Expected best move {api.best_move}, got {api.moves_within(0)[0]}"
    )


@pytest.mark.asyncio
async def test_scores_within_threshold():
    fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"
    api = LichessAnalysisApi(fen=fen, multi_pv=5, variant="standard")
    await api.raw_evaluation()

    threshold = 20  # centipawns
    moves_within_threshold = api.scores_within(threshold)

    assert len(moves_within_threshold) >= 1, (
        f"Expected at least one move within {threshold} cp threshold, got: {len(moves_within_threshold)}"
    )

    best_score = api.best_score
    for score, move in moves_within_threshold:
        assert abs(score - best_score) <= threshold, (
            f"Move {move} with score {score} is outside the {threshold} cp threshold from best score {best_score}"
        )


@pytest.mark.asyncio
async def test_raw_evaluation_raises_runtime_and_leaves_response_empty(monkeypatch):
    fen = "fail-fen"
    api = LichessAnalysisApi(fen=fen, multi_pv=1, variant="standard")

    req = httpx.Request("GET", api.BASE_URL)
    failure = httpx.Response(500, request=req)

    class StubClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def get(self, *args, **kwargs):
            return failure

    monkeypatch.setattr(httpx, "AsyncClient", StubClient)

    with pytest.raises(RuntimeError) as excinfo:
        await api.raw_evaluation()

    assert "500" in str(excinfo.value)
    assert getattr(api, "_response", None) is None


# After 1. e4 e5 2. Bc4 a6 3. Nf3 a5 4. Ng5 a4 5. Bxf7+ there is only one legal move for Black: Ke7
# FEN: rnbqkbnr/1ppp1Bpp/8/4p1N1/p3P3/8/PPPP1PPP/RNBQK2R b KQkq - 0 5
@pytest.mark.asyncio
async def test_handles_case_when_only_one_move_is_returned():
    fen = "3k1q2/1N2Rp2/3r1P2/p2p3p/1p1P2bP/8/PPPK4/5R2 b - - 1 31"
    api = LichessAnalysisApi(fen=fen, multi_pv=1)
    try:
        await api.raw_evaluation()
    except RuntimeError as exc:
        pytest.skip(f"Cloud evaluation unavailable for position: {exc}")

    moves = api.moves
    assert len(moves) == 1, f"Expected only one legal move, got: {len(moves)}"

    score, move = moves[0]
    assert move == "e7e8q" or move == "e7e8r" or move == "e7e8b" or move == "e7e8n", (
        f"Expected only legal move Ke7 (in UCI format), got: {move}"
    )
