from typing import cast

from rep_grow.export_repertoire_to_anki_csv import (
    _finalize_rows,
    _row_entries_from_lines,
)
from rep_grow.repertoire import RepertoireNode


class FakeNode:
    def __init__(self, games_reached=None):
        self.games_reached = games_reached


def test_row_entries_respect_min_max_and_dedupe():
    headers = {}
    san_lines = [
        (["e4", "e5"], FakeNode(3)),
        (["e4", "e5"], FakeNode(4)),
        (["d4"], FakeNode(2)),
    ]

    rows = _row_entries_from_lines(
        cast(list[tuple[list[str], RepertoireNode]], san_lines),
        headers=headers,
        max_plies=2,
        min_plies=2,
        description_plies=1,
        dedupe=True,
    )

    assert rows == [("e4", "e4 e5", 3)]


def test_finalize_rows_sorts_and_includes_games():
    entries = [
        ("Queen's Pawn", "d4 d5", 2),
        ("English", "c4", 5),
    ]

    rows = _finalize_rows(
        entries,
        root_fen="root-fen",
        include_games_reached=True,
        sort_by_games_reached=True,
    )

    assert rows == [
        ["1", "English", "root-fen", "c4", "5"],
        ["2", "Queen's Pawn", "root-fen", "d4 d5", "2"],
    ]
