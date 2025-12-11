from __future__ import annotations

import chess

from . import _core


def canonical_fen(fen: str) -> str:
    """Return a normalized FEN (resets clocks) using the Rust-backed helper."""

    return _core.canonicalize_fen(fen)


def start_fen(side: chess.Color | bool) -> str:
    """Return the canonical starting FEN for the given side to move."""

    board = chess.Board()
    if isinstance(side, bool):
        board.turn = chess.WHITE if side else chess.BLACK
    else:
        board.turn = side
    return canonical_fen(board.fen())


def same_position(fen_a: str, fen_b: str) -> bool:
    """Compare two FEN strings after canonicalization."""

    return canonical_fen(fen_a) == canonical_fen(fen_b)
