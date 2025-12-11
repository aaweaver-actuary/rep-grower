from __future__ import annotations

import chess

from rep_grow.fen import canonical_fen, same_position, start_fen


def test_start_fen_respects_side_to_move():
    white_start = start_fen(chess.WHITE)
    black_start = start_fen(False)

    assert white_start == chess.STARTING_FEN

    expected_black = chess.Board()
    expected_black.turn = chess.BLACK
    assert black_start == canonical_fen(expected_black.fen())
    assert white_start != black_start


def test_same_position_ignores_move_counters():
    board = chess.Board()
    board.halfmove_clock = 7
    board.fullmove_number = 3
    noisy_fen = board.fen()

    assert same_position(noisy_fen, chess.STARTING_FEN)
    assert canonical_fen(noisy_fen) == canonical_fen(chess.STARTING_FEN)
