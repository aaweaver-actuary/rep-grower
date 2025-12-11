from __future__ import annotations

import csv
from pathlib import Path

import chess
from click.testing import CliRunner
import pytest

from rep_grow.fen import canonical_fen
from rep_grow.export_repertoire_to_anki_csv import click_main


@pytest.fixture
def fixture_pgn_path() -> Path:
    return Path(__file__).parent / "fixtures" / "anki_export_fixture.pgn"


def _read_rows(path: Path) -> list[list[str]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.reader(handle))


def test_exporter_outputs_expected_rows(tmp_path, fixture_pgn_path):
    runner = CliRunner()
    output_path = tmp_path / "anki_export.csv"
    result = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(fixture_pgn_path),
            "--side",
            "white",
            "--output",
            str(output_path),
        ],
    )
    assert result.exit_code == 0, result.output

    rows = _read_rows(output_path)
    assert len(rows) == 3
    assert sorted(int(row[0]) for row in rows) == [1, 2, 3]

    root_fen = canonical_fen(chess.STARTING_FEN)
    for row in rows:
        assert len(row) == 4
        assert row[2] == root_fen
        assert row[3]
        assert row[1] == " ".join(row[3].split()[:8])

    expected_moves = {
        "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 Nf6 O-O Be7",
        "e4 e5 Nf3 Nc6 Bb5 a6 Ba4 b5 Bb3 Nf6 d3 Be7 O-O",
        "e4 e5 Nf3 d6 d4 exd4 Nxd4 Nf6",
    }
    assert {row[3] for row in rows} == expected_moves


def test_exporter_caps_and_dedupes(tmp_path, fixture_pgn_path):
    runner = CliRunner()
    capped_output = tmp_path / "capped.csv"
    result_no_dedupe = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(fixture_pgn_path),
            "--side",
            "white",
            "--output",
            str(capped_output),
            "--max-plies",
            "2",
        ],
    )
    assert result_no_dedupe.exit_code == 0, result_no_dedupe.output
    capped_rows = _read_rows(capped_output)
    assert len(capped_rows) == 3
    assert all(row[3] == "e4 e5" for row in capped_rows)

    deduped_output = tmp_path / "deduped.csv"
    result_dedupe = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(fixture_pgn_path),
            "--side",
            "white",
            "--output",
            str(deduped_output),
            "--max-plies",
            "2",
            "--dedupe",
        ],
    )
    assert result_dedupe.exit_code == 0, result_dedupe.output
    deduped_rows = _read_rows(deduped_output)
    assert len(deduped_rows) == 1
    assert deduped_rows[0][3] == "e4 e5"


def test_exporter_chunks_output(tmp_path, fixture_pgn_path):
    runner = CliRunner()
    output_path = tmp_path / "anki_export.csv"
    result = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(fixture_pgn_path),
            "--side",
            "white",
            "--output",
            str(output_path),
            "--chunk-size",
            "2",
        ],
    )
    assert result.exit_code == 0, result.output

    part1 = tmp_path / "anki_export_part1.csv"
    part2 = tmp_path / "anki_export_part2.csv"
    assert part1.exists()
    assert part2.exists()

    rows_part1 = _read_rows(part1)
    rows_part2 = _read_rows(part2)
    assert len(rows_part1) == 2
    assert len(rows_part2) == 1

    all_rows = rows_part1 + rows_part2
    assert sorted(int(row[0]) for row in all_rows) == [1, 2, 3]


def test_exporter_includes_and_sorts_by_games_reached(tmp_path):
    runner = CliRunner()
    output_path = tmp_path / "games.csv"
    pgn_text = """[Event "?"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]

1. e4 { [rg:games=50] } e5 { [rg:games=20] } 2. Nf3 { [rg:games=25] } Nc6 { [rg:games=5] } (2... d6 { [rg:games=40] }) 3. Bb5 { [rg:games=5] } *
"""
    pgn_path = tmp_path / "reach_counts.pgn"
    pgn_path.write_text(pgn_text, encoding="utf-8")

    result = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(pgn_path),
            "--side",
            "white",
            "--output",
            str(output_path),
            "--include-games-reached",
            "--sort-by-games-reached",
        ],
    )
    assert result.exit_code == 0, result.output

    rows = _read_rows(output_path)
    assert len(rows) == 2
    assert all(len(row) == 5 for row in rows)

    counts = [int(row[4]) for row in rows]
    assert counts == sorted(counts, reverse=True)

    move_lines = [row[3] for row in rows]
    assert move_lines[0].endswith("d6")  # highest count first
    assert move_lines[1].endswith("Bb5")
