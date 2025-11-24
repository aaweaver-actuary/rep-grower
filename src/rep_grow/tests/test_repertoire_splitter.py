from __future__ import annotations

import chess

from rep_grow.repertoire import Repertoire
from rep_grow.repertoire_splitter import RepertoireSplitter


def build_split_sample() -> Repertoire:
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()
    root = rep.root_node
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    rep.branch_from(root, ["e4", "c5", "Nc3", "Nc6", "Nf3"])
    rep.branch_from(root, ["d4", "d5", "c4", "e6", "Nc3"])
    rep.branch_from(root, ["d4", "Nf6", "c4", "g6", "Nc3"])
    return rep


def test_splitter_respects_move_cap():
    rep = build_split_sample()
    splitter = RepertoireSplitter(rep)

    events = splitter.split_events(max_moves=3)

    assert len(events) >= 2
    assert all(event.move_count <= 3 for event in events)


def test_splitter_event_headers_reflect_prefix():
    rep = build_split_sample()
    splitter = RepertoireSplitter(rep)

    events = splitter.split_events(max_moves=4)
    target = next(event for event in events if event.prefix_moves)

    game = splitter.build_game(target, event_index=2)

    assert game.headers["Event"].startswith("1.e4") or game.headers["Event"].startswith(
        "1.d4"
    )
    assert game.headers["SetUp"] == "1"
    assert game.headers["FEN"] == target.node.fen
    assert game.headers["Round"] == "2"
