import chess

from rep_grow.fen import canonical_fen
from rep_grow.pgn_metadata import extract_reach_count, upsert_reach_count_tag
from rep_grow.repertoire import Repertoire


def test_extract_reach_count_and_cleaning():
    count, cleaned = extract_reach_count("Idea [rg:games=123] note [rg:games=456]")
    assert count == 456
    assert cleaned == "Idea  note"


def test_upsert_reach_count_tag_replaces_existing():
    updated = upsert_reach_count_tag("Old [rg:games=10]", 25)
    assert updated.endswith("[rg:games=25]")
    assert "10" not in updated


def test_repertoire_ingests_reach_count_tag(tmp_path):
    pgn_text = """[Event "?"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]

1. e4 { [rg:games=321] A line } e5 *
"""
    path = tmp_path / "reach_tag.pgn"
    path.write_text(pgn_text, encoding="utf-8")

    rep = Repertoire.from_pgn_file(chess.WHITE, path)

    board = chess.Board()
    board.push_san("e4")
    fen = canonical_fen(board.fen())
    node = rep.nodes_by_fen[fen]

    assert node.games_reached == 321
    assert any("rg:games=321" in (pgn.comment or "") for pgn in node.pgn_nodes)
