import types

import chess

from rep_grow.export_repertoire_to_anki_csv import (
    _chunk_paths,
    _finalize_rows,
    _format_description,
    _limited_moves,
    _row_entries_from_lines,
    _sorted_children,
)


def test_sorted_children_handles_illegal_moves_and_sorts():
    board = chess.Board()
    node = types.SimpleNamespace(
        children={
            "e7e5": types.SimpleNamespace(),  # illegal from the starting position
            "e2e4": types.SimpleNamespace(),
        }
    )

    result = _sorted_children(node, board)

    sans = [item[0] for item in result]
    # e2e4 is legal and gets SAN; e7e5 falls back to UCI string
    assert sans == ["e4", "e7e5"]


def test_format_description_prefers_variation_then_eco_then_moves():
    moves = ["e4", "e5", "Nf3"]

    assert _format_description(moves, {"Variation": "Scotch"}, 2) == "Scotch"
    assert _format_description(moves, {"Variation": "?", "ECO": "C45"}, 2) == "C45"
    assert _format_description(moves, {}, 2) == "e4 e5"
    assert _format_description(moves, {}, 0) == ""


def test_row_entries_from_lines_applies_limits_and_dedupe():
    headers = {"Variation": "?"}
    lines = [
        (["e4", "e5"], types.SimpleNamespace(games_reached=3)),
        (["e4", "e5"], types.SimpleNamespace(games_reached=None)),
        (["d4"], types.SimpleNamespace(games_reached=1)),
    ]

    rows = _row_entries_from_lines(
        lines,
        headers=headers,
        max_plies=2,
        min_plies=1,
        description_plies=1,
        dedupe=True,
    )

    # Deduped identical lines; kept items meeting min_plies after limiting
    assert rows == [("e4", "e4 e5", 3), ("d4", "d4", 1)]


def test_finalize_rows_can_sort_and_include_games_reached():
    rows = [("desc1", "e4 e5", 1), ("desc2", "d4 d5", 5)]

    finalized = _finalize_rows(
        rows,
        root_fen=chess.STARTING_FEN,
        include_games_reached=True,
        sort_by_games_reached=True,
    )

    assert finalized == [
        ["1", "desc2", chess.STARTING_FEN, "d4 d5", "5"],
        ["2", "desc1", chess.STARTING_FEN, "e4 e5", "1"],
    ]


def test_chunk_paths_creates_incrementing_files(tmp_path):
    base = tmp_path / "anki.csv"

    paths = _chunk_paths(base, 3)

    assert [p.name for p in paths] == [
        "anki_part1.csv",
        "anki_part2.csv",
        "anki_part3.csv",
    ]


def test_limited_moves_caps_when_requested():
    assert _limited_moves(["e4", "e5", "Nf3"], 2) == ["e4", "e5"]
    assert _limited_moves(["e4", "e5"], None) == ["e4", "e5"]
