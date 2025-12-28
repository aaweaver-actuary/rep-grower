import hashlib
import io

import chess
import chess.pgn as chess_pgn
import httpx
import pytest

from rep_grow.fen import canonical_fen
from rep_grow.repertoire import Repertoire, RepertoireConfig, RepertoireNode

PGN_WITH_VARIATIONS = """[Event "?"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Nc3 Nf6 (3... Bc5) (3... d6) (3... Bb4) (3... f5) (3... a6) (3... h6) *
"""


def _write_sample_pgn(tmp_path, text: str = PGN_WITH_VARIATIONS) -> str:
    path = tmp_path / "sample.pgn"
    path.write_text(text.strip() + "\n", encoding="utf-8")
    return str(path)


def _normalized_pgn(text: str) -> str:
    game = chess_pgn.read_game(io.StringIO(text))
    if game is None:
        raise ValueError("PGN text did not contain a game")
    exporter = chess_pgn.StringExporter(columns=None)
    return game.accept(exporter)


def _collect_pgn_lines(game: chess_pgn.Game) -> list[list[str]]:
    lines: list[list[str]] = []

    def walk(node: chess_pgn.GameNode, path: list[str]):
        if not node.variations:
            lines.append(path.copy())
            return
        for variation in node.variations:
            walk(variation, path + [variation.san()])

    walk(game, [])
    return lines


@pytest.fixture
def fake_stockfish(monkeypatch):
    class FakeStockfish:
        BEST_SCORE_THRESHOLD = 50
        moves_to_return = ["g1f3", "d2d4", "c2c4"]

        def __init__(self, fen, multi_pv=10, **kwargs):  # noqa: D401, ARG002
            self.fen = fen
            self.multi_pv = multi_pv
            self.best_moves: list[str] = []

        async def raw_evaluation(self):
            self.best_moves = list(self.moves_to_return)
            return self

    monkeypatch.setattr("rep_grow.repertoire.StockfishAnalysisApi", FakeStockfish)
    return FakeStockfish


@pytest.fixture
def fake_explorer(monkeypatch):
    class FakeExplorer:
        moves_for_fen: dict[str, list[dict[str, int]]] = {}
        default_moves = [
            {"move": "Nf3", "total": 70},
            {"move": "Nc3", "total": 30},
        ]

        def __init__(self, fen, **kwargs):
            self.fen = fen

        async def raw_explorer(self):
            return self

        def top_p_pct_moves(self, pct, max_moves=None, min_game_share=None):  # noqa: ARG002
            moves = self.moves_for_fen.get(self.fen, self.default_moves)
            return list(moves)

    FakeExplorer.moves_for_fen = {}
    monkeypatch.setattr("rep_grow.repertoire.LichessExplorerApi", FakeExplorer)
    return FakeExplorer


def test_repertoire_play_initial_moves():
    initial_san = "e4 e5 Nf3 Nc6 Bc4 a6"
    repertoire = Repertoire(side=chess.WHITE, initial_san=initial_san)

    repertoire.play_initial_moves()

    expected_fen = "r1bqkbnr/1ppp1ppp/p1n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"
    assert repertoire.board.fen() == expected_fen, (
        f"Expected FEN: {expected_fen}, got: {repertoire.board.fen()}"
    )


def test_repertoire_empty_initial_moves():
    initial_san = ""
    repertoire = Repertoire(side=chess.WHITE, initial_san=initial_san)
    repertoire.play_initial_moves()

    expected_fen = chess.STARTING_FEN
    assert repertoire.board.fen() == expected_fen, (
        f"Expected FEN: {expected_fen}, got: {repertoire.board.fen()}"
    )


def test_repertoire_black_side():
    initial_san = "e4 e5 Nf3 Nc6 Bc4 a6"
    repertoire = Repertoire(side=chess.BLACK, initial_san=initial_san)
    repertoire.play_initial_moves()

    expected_fen = "r1bqkbnr/1ppp1ppp/p1n5/4p3/2B1P3/5N2/PPPP1PPP/RNBQK2R w KQkq - 0 4"
    assert repertoire.board.fen() == expected_fen, (
        f"Expected FEN: {expected_fen}, got: {repertoire.board.fen()}"
    )


def test_repertoire_from_pgn_builds_initial_state(tmp_path):
    path = _write_sample_pgn(tmp_path)
    config = RepertoireConfig(
        stockfish_best_score_threshold=25,
        explorer_pct=95.0,
    )

    rep = Repertoire.from_pgn_file(chess.WHITE, path, config=config)

    expected_moves = ["e4", "e5", "Nf3", "Nc6", "Nc3", "Nf6"]
    assert rep.initial_san.split() == expected_moves
    assert rep.moves == expected_moves

    expected_board = chess.Board()
    for san in expected_moves:
        expected_board.push_san(san)
    assert rep.board.fen() == expected_board.fen()

    board_pre_variations = chess.Board()
    for san in expected_moves[:-1]:
        board_pre_variations.push_san(san)
    node_fen = canonical_fen(board_pre_variations.fen())
    node = rep.nodes_by_fen[node_fen]
    child_moves = set(node.children.keys())
    expected_child_sans = ["Nf6", "Bc5", "d6", "Bb4", "f5", "a6", "h6"]
    expected_child_uci = {
        chess.Board(board_pre_variations.fen()).parse_san(san).uci()
        for san in expected_child_sans
    }
    assert expected_child_uci.issubset(child_moves)


def test_repertoire_from_pgn_roundtrip_hash(tmp_path):
    path = _write_sample_pgn(tmp_path)
    config = RepertoireConfig(
        stockfish_best_score_threshold=25,
        explorer_pct=95.0,
    )

    rep = Repertoire.from_pgn_file(chess.WHITE, path, config=config)

    original_norm = _normalized_pgn(PGN_WITH_VARIATIONS)
    restored_norm = _normalized_pgn(rep.pgn)

    original_hash = hashlib.sha256(original_norm.encode("utf-8")).hexdigest()
    restored_hash = hashlib.sha256(restored_norm.encode("utf-8")).hexdigest()

    assert restored_hash == original_hash


def test_repertoire_moves_list():
    initial_san = "e4 e5 Nf3 Nc6 Bc4 a6"
    repertoire = Repertoire(side=chess.WHITE, initial_san=initial_san)
    repertoire.play_initial_moves()

    expected_moves = ["e4", "e5", "Nf3", "Nc6", "Bc4", "a6"]
    assert repertoire.moves == expected_moves, (
        f"Expected moves: {expected_moves}, got: {repertoire.moves}"
    )


@pytest.mark.parametrize("side", [chess.WHITE, chess.BLACK])
def test_repertoire_turn(side):
    initial_san = "e4 e5 Nf3 Nc6 Bc4 a6"
    repertoire = Repertoire(side=side, initial_san=initial_san)
    repertoire.play_initial_moves()

    expected_turn = chess.WHITE
    assert repertoire.turn == expected_turn, (
        f"Expected turn: {expected_turn}, got: {repertoire.turn}"
    )


def test_repertoire_is_player_turn():
    initial_san = "e4 e5 Nf3 Nc6 Bc4 a6"
    repertoire = Repertoire(side=chess.WHITE, initial_san=initial_san)
    repertoire.play_initial_moves()

    assert repertoire.is_player_turn is True, (
        f"Expected is_player_turn to be True, got: {repertoire.is_player_turn}"
    )

    repertoire_black = Repertoire(side=chess.BLACK, initial_san=initial_san)
    repertoire_black.play_initial_moves()

    assert repertoire_black.is_player_turn is False, (
        f"Expected is_player_turn to be False, got: {repertoire_black.is_player_turn}"
    )


def test_export_orders_mainline_by_reach(tmp_path):
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    root = rep.root_node
    e4 = rep.branch_from(root, ["e4"])
    d4 = rep.branch_from(root, ["d4"])

    e4.games_reached = 200
    d4.games_reached = 120

    e4e5 = rep.branch_from(e4, ["e5"])
    e4c5 = rep.branch_from(e4, ["c5"])
    e4e5.games_reached = 150
    e4c5.games_reached = 60

    d4d5 = rep.branch_from(d4, ["d5"])
    d4Nf6 = rep.branch_from(d4, ["Nf6"])
    d4d5.games_reached = 80
    d4Nf6.games_reached = 40

    output = tmp_path / "sorted.pgn"
    rep.export_pgn(str(output))

    with output.open() as handle:
        game = chess_pgn.read_game(handle)

    assert game is not None

    top_level_moves = [var.san() for var in game.variations]
    assert top_level_moves[:2] == ["e4", "d4"]

    e4_line = game.variations[0]
    e4_responses = [var.san() for var in e4_line.variations]
    assert e4_responses[:2] == ["e5", "c5"]

    d4_line = game.variations[1]
    d4_responses = [var.san() for var in d4_line.variations]
    assert d4_responses[:2] == ["d5", "Nf6"]

    lines = _collect_pgn_lines(game)
    assert lines[0][:2] == ["e4", "e5"]
    assert ["d4", "d5"] in [line[:2] for line in lines]


@pytest.mark.parametrize("side", [chess.WHITE, chess.BLACK])
def test_repertoire_fen_after_no_moves(side):
    initial_san = ""
    repertoire = Repertoire(side=side, initial_san=initial_san)
    repertoire.play_initial_moves()

    expected_fen = chess.STARTING_FEN
    assert repertoire.fen == expected_fen, (
        f"Expected FEN: {expected_fen}, got: {repertoire.fen}"
    )


def test_repertoire_pgn():
    initial_san = "e4 e5 Nf3 Nc6 Bc4 a6"
    repertoire = Repertoire(side=chess.WHITE, initial_san=initial_san)
    repertoire.play_initial_moves()

    expected_pgn_start = '[Event "?"]'
    assert repertoire.pgn.startswith(expected_pgn_start), (
        f"Expected PGN to start with: {expected_pgn_start}, got: {repertoire.pgn}"
    )

    expected_moves_in_pgn = "1. e4 e5 2. Nf3 Nc6 3. Bc4 a6"
    assert expected_moves_in_pgn in repertoire.pgn, (
        f"Expected PGN to contain moves: {expected_moves_in_pgn}, got: {repertoire.pgn}"
    )


def test_merge_game_nodes_combines_comments(tmp_path):
    pgn = """[Event "G1"]

1. e4 {A} e5 *

[Event "G2"]

1. e4 {B} e5 *
"""
    path = tmp_path / "merge.pgn"
    path.write_text(pgn, encoding="utf-8")

    rep = Repertoire.from_pgn_file(side=chess.WHITE, pgn_path=path)

    game = rep.game
    mainline_iter = iter(game.mainline())
    first_move = next(mainline_iter).comment
    assert "A" in first_move
    assert "B" in first_move


@pytest.mark.asyncio
async def test_raw_evaluation(fake_stockfish):
    san = "e4 e5 Nf3 Nc6 Bc4 a6"
    rep = Repertoire(side=chess.WHITE, initial_san=san)
    rep.play_initial_moves()

    moves = await rep.get_engine_moves()
    assert moves == fake_stockfish.moves_to_return


@pytest.mark.asyncio
async def test_uses_lichess_when_configured(monkeypatch):
    calls = {"lichess": 0}

    class StubLichess:
        def __init__(
            self,
            fen,
            multi_pv=10,
            variant="standard",
            best_score_threshold=20,
            request=None,
            **kwargs,  # noqa: D401, ARG002
        ):
            calls["lichess"] += 1
            self.best_moves = ["b1c3"]

        async def raw_evaluation(self):  # pragma: no cover - trivial stub
            return self

    class FailStockfish:
        def __init__(self, *args, **kwargs):  # noqa: D401, ARG002
            raise AssertionError("Stockfish should not be used when provider=lichess")

    monkeypatch.setattr("rep_grow.repertoire.LichessAnalysisApi", StubLichess)
    monkeypatch.setattr("rep_grow.repertoire.StockfishAnalysisApi", FailStockfish)

    config = RepertoireConfig(analysis_provider="lichess")
    rep = Repertoire(side=chess.WHITE, initial_san="", config=config)
    rep.play_initial_moves()

    moves = await rep.get_engine_moves()

    assert moves == ["b1c3"]
    assert calls["lichess"] == 1


@pytest.mark.asyncio
async def test_hybrid_falls_back_to_stockfish(monkeypatch):
    calls = {"lichess": 0, "stockfish": 0}

    class FailingLichess:
        def __init__(self, *args, **kwargs):  # noqa: D401, ARG002
            calls["lichess"] += 1
            self.best_moves: list[str] = []

        async def raw_evaluation(self):
            raise RuntimeError("cloud unavailable")

    class StubStockfish:
        def __init__(self, fen, multi_pv=10, **kwargs):  # noqa: D401, ARG002
            calls["stockfish"] += 1
            self.fen = fen
            self.best_moves = ["a2a4"]

        async def raw_evaluation(self):  # pragma: no cover - trivial stub
            return self

    monkeypatch.setattr("rep_grow.repertoire.LichessAnalysisApi", FailingLichess)
    monkeypatch.setattr("rep_grow.repertoire.StockfishAnalysisApi", StubStockfish)

    config = RepertoireConfig(analysis_provider="hybrid")
    rep = Repertoire(side=chess.WHITE, initial_san="", config=config)
    rep.play_initial_moves()

    moves = await rep.get_engine_moves()

    assert moves == ["a2a4"]
    assert calls["lichess"] == 1
    assert calls["stockfish"] == 1


@pytest.mark.asyncio
async def test_custom_provider_order_supports_third_engine(monkeypatch):
    calls = {"custom": 0, "stockfish": 0}

    async def custom_provider(task):  # noqa: ARG001
        calls["custom"] += 1
        return ["h2h4"]

    class StubStockfish:
        def __init__(self, fen, multi_pv=10, **kwargs):  # noqa: D401, ARG002
            calls["stockfish"] += 1
            self.fen = fen
            self.multi_pv = multi_pv
            self.best_moves = ["a2a4"]

        async def raw_evaluation(self):  # pragma: no cover - trivial stub
            return self

    def provider_fns(self):  # type: ignore[override]
        base = {"stockfish": self._run_stockfish_provider}
        base["custom"] = custom_provider
        return base

    monkeypatch.setattr("rep_grow.repertoire.StockfishAnalysisApi", StubStockfish)
    monkeypatch.setattr(
        Repertoire, "_analysis_provider_fns", provider_fns, raising=False
    )

    config = RepertoireConfig(analysis_providers=["custom", "stockfish"])
    rep = Repertoire(side=chess.WHITE, initial_san="", config=config)
    rep.play_initial_moves()

    moves = await rep.get_engine_moves()

    assert moves == ["h2h4"]
    assert calls["custom"] == 1
    assert calls["stockfish"] == 0


@pytest.mark.asyncio
async def test_add_engine_variations_creates_pgn_variations(fake_stockfish):
    fake_stockfish.moves_to_return = ["g1f3", "d2d4"]

    san = "e4 e5"
    rep = Repertoire(side=chess.WHITE, initial_san=san)
    rep.play_initial_moves()

    initial_leaves = {node.fen for node in rep.leaf_nodes}
    added = await rep.add_engine_variations()

    assert set(added.keys()) == initial_leaves
    node = rep._mainline_node()
    node_variations = {var.move.uci() for var in node.variations}
    assert node_variations.issuperset(fake_stockfish.moves_to_return)
    assert "(" in rep.pgn, "PGN should contain variation parentheses"


@pytest.mark.asyncio
async def test_add_engine_variations_accepts_explicit_node(fake_stockfish):
    fake_stockfish.moves_to_return = ["c2c4"]

    rep = Repertoire(side=chess.WHITE, initial_san="e4")
    rep.play_initial_moves()

    branch_node = rep.branch_from(rep.root_node, ["d4", "d5"])

    added = await rep.add_engine_variations_for_node(node=branch_node)

    assert added == fake_stockfish.moves_to_return
    branch_pgn = rep._mainline_node(branch_node)
    assert any(var.move.uci() == "c2c4" for var in branch_pgn.variations)


@pytest.mark.asyncio
async def test_parallel_add_engine_variations_processes_all_leaf_nodes(fake_stockfish):
    fake_stockfish.moves_to_return = ["e7e5"]

    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    node_a = rep.branch_from(rep.root_node, ["e4"])
    node_b = rep.branch_from(rep.root_node, ["d4"])

    leaves = rep.leaf_nodes
    result = await rep.add_engine_variations(nodes=leaves, max_concurrency=2)

    assert set(result.keys()) == {node_a.fen, node_b.fen}
    for fen, moves in result.items():
        assert moves == fake_stockfish.moves_to_return
        node = rep.nodes_by_fen[fen]
        pgn_node = rep._mainline_node(node)
        assert any(var.move.uci() == "e7e5" for var in pgn_node.variations)


@pytest.mark.asyncio
async def test_add_engine_variations_reports_progress(fake_stockfish):
    fake_stockfish.moves_to_return = ["c7c5"]

    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    node_a = rep.branch_from(rep.root_node, ["e4"])
    node_b = rep.branch_from(rep.root_node, ["d4"])

    seen: list[str] = []

    def _progress(node: RepertoireNode):
        seen.append(node.fen)

    await rep.add_engine_variations(
        nodes=[node_a, node_b],
        max_concurrency=1,
        progress_callback=_progress,
    )

    assert set(seen) == {node_a.fen, node_b.fen}


@pytest.mark.asyncio
async def test_add_explorer_variations_for_node_skips_existing_moves(fake_explorer):
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    rep.branch_from(rep.root_node, ["Nf3"])

    fake_explorer.moves_for_fen = {
        rep.root_node.fen: [
            {"move": "Nf3", "total": 60},
            {"move": "Nc3", "total": 40},
        ]
    }

    added = await rep.add_explorer_variations_for_node(node=rep.root_node, pct=95.0)

    assert added == ["Nc3"]
    parent_pgn = rep._mainline_node(rep.root_node)
    board = chess.Board(rep.root_node.fen)
    assert any(board.san(var.move) == "Nc3" for var in parent_pgn.variations)


@pytest.mark.asyncio
async def test_parallel_add_explorer_variations(fake_explorer):
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    node_a = rep.branch_from(rep.root_node, ["e4"])
    node_b = rep.branch_from(rep.root_node, ["d4"])

    fake_explorer.moves_for_fen = {
        node_a.fen: [
            {"move": "c5", "total": 100},
        ],
        node_b.fen: [
            {"move": "d5", "total": 100},
        ],
    }

    result = await rep.add_explorer_variations(
        nodes=[node_a, node_b], pct=90.0, max_concurrency=2
    )

    assert result[node_a.fen] == ["c5"]
    assert result[node_b.fen] == ["d5"]

    for fen, moves in result.items():
        node = rep.nodes_by_fen[fen]
        pgn_node = rep._mainline_node(node)
        board = chess.Board(node.fen)
        for expected in moves:
            assert any(board.san(var.move) == expected for var in pgn_node.variations)


@pytest.mark.asyncio
async def test_add_explorer_variations_recovers_from_http_errors(monkeypatch):
    error_fen_board = chess.Board()
    error_fen_board.push_san("d4")
    error_fen = error_fen_board.fen()

    ok_board = chess.Board()
    ok_board.push_san("e4")

    request = httpx.Request("GET", "https://example.test")
    error_response = httpx.Response(429, request=request)

    class FlakyExplorer:
        def __init__(self, fen, **_kwargs):
            self.fen = fen

        async def raw_explorer(self):
            if self.fen == error_fen:
                raise httpx.HTTPStatusError(
                    "rate limited",
                    request=request,
                    response=error_response,
                )
            return self

        def top_p_pct_moves(self, pct, max_moves=None, min_game_share=None):  # noqa: ARG002
            if self.fen == error_fen:
                return []
            return [{"move": "c5", "total": 100}]

    monkeypatch.setattr("rep_grow.repertoire.LichessExplorerApi", FlakyExplorer)

    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()
    bad_node = rep.branch_from(rep.root_node, ["d4"])
    good_node = rep.branch_from(rep.root_node, ["e4"])

    result = await rep.add_explorer_variations(
        nodes=[bad_node, good_node],
        max_concurrency=2,
    )

    assert result[bad_node.fen] == []
    assert result[good_node.fen] == ["c5"]


def test_repertoire_graph_deduplicates_transpositions():
    rep = Repertoire(side=chess.WHITE, initial_san="Nc3 Nf6 Nf3")
    rep.play_initial_moves()

    rep.branch_from(rep.root_node, ["Nf3", "Nf6", "Nc3"])

    board = chess.Board()
    for san in ("Nf3", "Nf6", "Nc3"):
        board.push(board.parse_san(san))
    target_fen = canonical_fen(board.fen())

    node: RepertoireNode = rep.nodes_by_fen[target_fen]
    assert len(node.parents) >= 2
    assert node.fen == target_fen


def test_transposed_lines_share_future_moves_in_pgn():
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    italian_order = ["e4", "e5", "Nf3", "Nc6", "Bc4", "Bc5"]
    italian_transposed = ["e4", "e5", "Bc4", "Bc5", "Nf3", "Nc6"]
    third_line = ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"]

    node_main = rep.branch_from(rep.root_node, italian_order)
    node_transposed = rep.branch_from(rep.root_node, italian_transposed)

    assert node_main is node_transposed

    rep.branch_from(node_main, ["c3", "Nf6", "d4"])
    rep.branch_from(rep.root_node, third_line)

    lines = _collect_pgn_lines(rep.game)

    assert [
        "e4",
        "e5",
        "Nf3",
        "Nc6",
        "Bc4",
        "Bc5",
        "c3",
        "Nf6",
        "d4",
    ] in lines
    assert [
        "e4",
        "e5",
        "Bc4",
        "Bc5",
        "Nf3",
        "Nc6",
        "c3",
        "Nf6",
        "d4",
    ] in lines
    assert [
        "e4",
        "e5",
        "Nf3",
        "Nc6",
        "Bb5",
        "a6",
        "Ba4",
    ] in lines


def test_canonical_fen_ignores_move_counters():
    board = chess.Board()
    board.push(board.parse_san("Nf3"))
    fen_one = board.fen()
    board.halfmove_clock = 7
    board.fullmove_number = 9
    fen_two = board.fen()

    assert fen_one != fen_two
    assert canonical_fen(fen_one) == canonical_fen(fen_two)


def test_branch_from_reuses_existing_position():
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    result = rep.branch_from(rep.root_node, ["Nf3", "Nf6", "Ng1", "Ng8"])

    assert result is rep.root_node
    assert rep.root_node.is_root is True
    assert len(rep.nodes_by_fen) == 4


def test_player_move_rankings_map_is_json_friendly():
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()
    root = rep.root_node
    rep.branch_from(root, ["Nf3", "d5", "g3"])
    rep.branch_from(root, ["Nf3", "c5", "Nc3"])
    rep.branch_from(root, ["d4", "d5", "Nf3"])

    rankings = rep.player_move_rankings()

    assert rep.root_node.fen in rankings
    first_entry = rankings[rep.root_node.fen][0]
    assert set(first_entry.keys()) == {"uci", "san", "frequency"}
    assert first_entry["san"] in {"Nf3", "d4"}
    assert isinstance(first_entry["frequency"], int)


@pytest.mark.asyncio
async def test_expand_leaves_by_turn_routes_moves_by_side(
    fake_stockfish, fake_explorer
):
    fake_stockfish.moves_to_return = ["a2a4"]

    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    opponent_node = rep.branch_from(rep.root_node, ["e4"])
    player_node = rep.branch_from(rep.root_node, ["Nf3", "d5"])

    fake_explorer.moves_for_fen = {
        opponent_node.fen: [
            {"move": "c5", "total": 100},
        ]
    }

    await rep.expand_leaves_by_turn()

    player_pgn = rep._mainline_node(player_node)
    player_board = chess.Board(player_node.fen)
    assert any(player_board.san(var.move) == "a4" for var in player_pgn.variations)

    opponent_pgn = rep._mainline_node(opponent_node)
    opponent_board = chess.Board(opponent_node.fen)
    assert any(opponent_board.san(var.move) == "c5" for var in opponent_pgn.variations)


@pytest.mark.asyncio
async def test_expand_leaves_by_turn_handles_mixed_turn_state(
    fake_stockfish, fake_explorer
):
    fake_stockfish.moves_to_return = ["a2a4"]

    rep = Repertoire(side=chess.WHITE, initial_san="e4")
    rep.play_initial_moves()

    opponent_node = rep.current_node
    player_node = rep.branch_from(rep.root_node, ["Nf3", "d5"])

    fake_explorer.moves_for_fen = {
        opponent_node.fen: [
            {"move": "Nc6", "total": 200},
        ]
    }

    await rep.expand_leaves_by_turn()

    opponent_board = chess.Board(opponent_node.fen)
    opponent_pgn = rep._mainline_node(opponent_node)
    assert any(opponent_board.san(var.move) == "Nc6" for var in opponent_pgn.variations)

    player_board = chess.Board(player_node.fen)
    player_pgn = rep._mainline_node(player_node)
    assert any(player_board.san(var.move) == "a4" for var in player_pgn.variations)


@pytest.mark.asyncio
async def test_expand_leaves_by_turn_respects_max_player_moves(fake_stockfish):
    fake_stockfish.moves_to_return = ["a2a4"]

    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    shallow_node = rep.branch_from(rep.root_node, ["e4", "c6"])
    deep_node = rep.branch_from(rep.root_node, ["e4", "e5", "Nf3", "Nc6"])

    await rep.expand_leaves_by_turn(max_player_moves=2)

    shallow_board = chess.Board(shallow_node.fen)
    shallow_pgn = rep._mainline_node(shallow_node)
    shallow_moves = {shallow_board.san(var.move) for var in shallow_pgn.variations}
    assert "a4" in shallow_moves

    deep_board = chess.Board(deep_node.fen)
    deep_pgn = rep._mainline_node(deep_node)
    deep_moves = {deep_board.san(var.move) for var in deep_pgn.variations}
    assert "a4" not in deep_moves


@pytest.mark.asyncio
async def test_expand_leaves_by_turn_skips_low_reach_nodes(monkeypatch):
    rep = Repertoire(
        side=chess.WHITE,
        initial_san="",
        config=RepertoireConfig(min_games_reached=50),
    )
    rep.play_initial_moves()

    high_opponent = rep.branch_from(rep.root_node, ["d4"])
    low_opponent = rep.branch_from(rep.root_node, ["c4"])
    high_player = rep.branch_from(rep.root_node, ["e4", "e5"])
    low_player = rep.branch_from(rep.root_node, ["Nf3", "d5"])

    high_opponent.games_reached = 120
    low_opponent.games_reached = 10
    high_player.games_reached = 80
    low_player.games_reached = 5

    captured: dict[str, list[RepertoireNode]] = {"engine": [], "explorer": []}

    async def fake_engine(self, nodes=None, **kwargs):  # pragma: no cover - test hook
        captured["engine"] = list(nodes or [])
        return {}

    async def fake_explorer(self, nodes=None, **kwargs):  # pragma: no cover - test hook
        captured["explorer"] = list(nodes or [])
        return {}

    monkeypatch.setattr(
        rep,
        "add_engine_variations",
        fake_engine.__get__(rep, Repertoire),
    )
    monkeypatch.setattr(
        rep,
        "add_explorer_variations",
        fake_explorer.__get__(rep, Repertoire),
    )

    await rep.expand_leaves_by_turn()

    engine_fens = {node.fen for node in captured["engine"]}
    explorer_fens = {node.fen for node in captured["explorer"]}

    assert engine_fens == {high_player.fen}
    assert explorer_fens == {high_opponent.fen}


@pytest.mark.asyncio
async def test_expand_leaves_by_turn_stockfish_handles_opponent_nodes(fake_stockfish):
    fake_stockfish.moves_to_return = ["g8f6"]

    rep = Repertoire(
        side=chess.WHITE,
        initial_san="",
        config=RepertoireConfig(explorer_enabled=False),
    )
    rep.play_initial_moves()

    opponent_node = rep.branch_from(rep.root_node, ["e4"])  # black to move (opponent)

    await rep.expand_leaves_by_turn()

    board = chess.Board(opponent_node.fen)
    pgn_node = rep._mainline_node(opponent_node)
    moves = {board.san(var.move) for var in pgn_node.variations}

    assert "Nf6" in moves


@pytest.mark.asyncio
async def test_expand_leaves_by_turn_skip_explorer_when_disabled(monkeypatch):
    rep = Repertoire(
        side=chess.WHITE,
        initial_san="",
        config=RepertoireConfig(explorer_enabled=False, analysis_provider="hybrid"),
    )
    rep.play_initial_moves()

    called = {"explorer": 0, "engine": 0}

    async def fake_engine(self, nodes=None, **kwargs):  # pragma: no cover - hook
        called["engine"] += 1
        return {}

    async def fake_explorer(
        self, nodes=None, **kwargs
    ):  # pragma: no cover - should skip
        called["explorer"] += 1
        return {}

    monkeypatch.setattr(
        rep, "add_engine_variations", fake_engine.__get__(rep, Repertoire)
    )
    monkeypatch.setattr(
        rep, "add_explorer_variations", fake_explorer.__get__(rep, Repertoire)
    )

    await rep.expand_leaves_by_turn()

    assert called["engine"] == 1
    assert called["explorer"] == 0


@pytest.mark.asyncio
async def test_static_lichess_eval_prefetches_and_skips_providers(monkeypatch):
    calls = {"lookup": 0, "provider": 0}

    def fake_lookup(path, fens):  # pragma: no cover - small test hook
        calls["lookup"] += 1
        return [{"pvs": [{"cp": 30, "moves": ["e2e4"]}]} for _ in fens]

    async def fake_best(
        self, target_node, multi_pv=None, depth=None
    ):  # pragma: no cover - small test hook
        calls["provider"] += 1
        return ["a2a4"]

    monkeypatch.setattr("rep_grow.repertoire._core.lookup_lichess_evals", fake_lookup)
    monkeypatch.setattr(
        Repertoire,
        "_analysis_best_moves",
        fake_best,
        raising=False,
    )

    rep = Repertoire(
        side=chess.WHITE,
        initial_san="",
        config=RepertoireConfig(lichess_eval_path="/tmp/lichess_db_eval.jsonl.zst"),
    )
    rep.play_initial_moves()

    await rep.add_engine_variations(nodes=[rep.root_node])

    board = chess.Board(rep.root_node.fen)
    pgn_node = rep._pgn_node_for(rep.root_node)
    moves = {board.san(var.move) for var in pgn_node.variations}

    assert "e4" in moves
    assert calls["lookup"] == 1
    assert calls["provider"] == 0


@pytest.mark.asyncio
async def test_static_lichess_eval_falls_back_to_provider(monkeypatch):
    calls = {"lookup": 0, "provider": 0}

    def fake_lookup(path, fens):  # pragma: no cover - small test hook
        calls["lookup"] += 1
        return [None for _ in fens]

    async def fake_best(
        self, target_node, multi_pv=None, depth=None
    ):  # pragma: no cover - small test hook
        calls["provider"] += 1
        return ["a2a4"]

    monkeypatch.setattr("rep_grow.repertoire._core.lookup_lichess_evals", fake_lookup)
    monkeypatch.setattr(
        Repertoire,
        "_analysis_best_moves",
        fake_best,
        raising=False,
    )

    rep = Repertoire(
        side=chess.WHITE,
        initial_san="",
        config=RepertoireConfig(lichess_eval_path="/tmp/lichess_db_eval.jsonl.zst"),
    )
    rep.play_initial_moves()

    await rep.add_engine_variations(nodes=[rep.root_node])

    board = chess.Board(rep.root_node.fen)
    pgn_node = rep._pgn_node_for(rep.root_node)
    moves = {board.san(var.move) for var in pgn_node.variations}

    assert "a4" in moves
    assert calls["lookup"] == 1
    assert calls["provider"] == 1


@pytest.mark.asyncio
async def test_repertoire_config_applies_context(monkeypatch):
    captured: dict[str, float | int] = {}

    class StubStockfish:
        def __init__(
            self,
            fen,
            multi_pv,
            variant="standard",
            engine_path=None,
            depth=20,
            think_time=None,
            best_score_threshold=20,
            pool_size=None,
            **_,
        ):
            captured["multi_pv"] = multi_pv
            captured["threshold"] = best_score_threshold
            self.best_moves: list[str] = []

        async def raw_evaluation(self):  # pragma: no cover - trivial stub
            return None

    class StubExplorer:
        def __init__(self, fen, **kwargs):
            self.fen = fen

        async def raw_explorer(self):  # pragma: no cover - trivial stub
            return None

        def top_p_pct_moves(self, pct, max_moves=None, min_game_share=None):
            captured["explorer_pct"] = pct
            return []

    monkeypatch.setattr("rep_grow.repertoire.StockfishAnalysisApi", StubStockfish)
    monkeypatch.setattr("rep_grow.repertoire.LichessExplorerApi", StubExplorer)

    config = RepertoireConfig(
        stockfish_multi_pv=4,
        stockfish_best_score_threshold=25,
        explorer_pct=95.0,
    )

    rep = Repertoire(side=chess.WHITE, initial_san="e4 e5", config=config)
    rep.play_initial_moves()
    rep.branch_from(rep.root_node, ["Nf3"])

    await rep.expand_leaves_by_turn()

    assert captured["multi_pv"] == 4
    assert captured["threshold"] == 25
    assert captured["explorer_pct"] == 95.0
