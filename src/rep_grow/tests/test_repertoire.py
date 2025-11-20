from rep_grow.repertoire import Repertoire, RepertoireNode
import chess
import pytest


@pytest.fixture
def fake_stockfish(monkeypatch):
    class FakeStockfish:
        BEST_SCORE_THRESHOLD = 50
        moves_to_return = ["g1f3", "d2d4", "c2c4"]

        def __init__(self, fen, multi_pv=10):
            self.fen = fen
            self.multi_pv = multi_pv
            self.best_moves: list[str] = []

        async def raw_evaluation(self):
            self.best_moves = list(self.moves_to_return)
            return self

    monkeypatch.setattr("rep_grow.repertoire.StockfishAnalysisApi", FakeStockfish)
    return FakeStockfish


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


@pytest.mark.asyncio
async def test_raw_evaluation(fake_stockfish):
    san = "e4 e5 Nf3 Nc6 Bc4 a6"
    rep = Repertoire(side=chess.WHITE, initial_san=san)
    rep.play_initial_moves()

    moves = await rep.get_engine_moves()
    assert moves == fake_stockfish.moves_to_return


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


def test_repertoire_graph_deduplicates_transpositions():
    rep = Repertoire(side=chess.WHITE, initial_san="Nc3 Nf6 Nf3")
    rep.play_initial_moves()

    rep.branch_from(rep.root_node, ["Nf3", "Nf6", "Nc3"])

    board = chess.Board()
    for san in ("Nf3", "Nf6", "Nc3"):
        board.push(board.parse_san(san))
    target_fen = board.fen()

    node: RepertoireNode = rep.nodes_by_fen[target_fen]
    assert len(node.parents) >= 2
    assert node.fen == target_fen
