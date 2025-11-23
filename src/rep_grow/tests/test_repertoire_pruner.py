from __future__ import annotations

import chess

from rep_grow.repertoire import Repertoire, RepertoireNode, canonical_fen
from rep_grow.repertoire_pruner import MoveFingerprint, RepertoirePruner


def build_sample_repertoire() -> Repertoire:
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    root = rep.root_node
    # Four representative player lines to provide overlapping moves.
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    rep.branch_from(root, ["e4", "c5", "Nf3", "d6", "d4"])
    rep.branch_from(root, ["d4", "d5", "c4", "Nc6", "Nc3"])
    rep.branch_from(root, ["Nf3", "d5", "g3"])

    return rep


def test_move_frequencies_count_each_player_edge():
    rep = build_sample_repertoire()
    pruner = RepertoirePruner(rep)

    counts = pruner.player_move_frequencies()

    assert counts[MoveFingerprint("P", "e2", "e4")] == 1
    assert counts[MoveFingerprint("N", "g1", "f3")] == 3
    assert counts[MoveFingerprint("P", "d2", "d4")] == 2
    assert MoveFingerprint("P", "e7", "e5") not in counts


def test_move_frequencies_count_multiple_edges_into_same_child():
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()

    board_a = chess.Board()
    board_a.push_san("h3")
    board_a.push_san("h6")

    board_b = chess.Board()
    board_b.push_san("a3")
    board_b.push_san("a6")

    parent_a = RepertoireNode(fen=canonical_fen(board_a.fen()))
    parent_b = RepertoireNode(fen=canonical_fen(board_b.fen()))

    move = chess.Move.from_uci("g1f3")
    child_board = chess.Board()
    child_board.push(move)
    child_node = RepertoireNode(fen=canonical_fen(child_board.fen()))

    parent_a.add_child(move, child_node)
    parent_b.add_child(move, child_node)
    child_node.add_parent(parent_a.fen)
    child_node.add_parent(parent_b.fen)

    rep.nodes_by_fen[parent_a.fen] = parent_a
    rep.nodes_by_fen[parent_b.fen] = parent_b
    rep.nodes_by_fen[child_node.fen] = child_node

    pruner = RepertoirePruner(rep)
    counts = pruner.player_move_frequencies()

    assert counts[MoveFingerprint("N", "g1", "f3")] == 2
