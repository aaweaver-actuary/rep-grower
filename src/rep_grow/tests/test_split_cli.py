from __future__ import annotations

from pathlib import Path

import chess
import chess.pgn as chess_pgn
from click.testing import CliRunner

from rep_grow.repertoire import Repertoire
from rep_grow.split import click_main as split_cli


def build_cli_fixture(path: Path) -> Path:
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()
    root = rep.root_node
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"])
    rep.branch_from(root, ["e4", "e5", "Bc4", "Bc5", "c3", "Nf6"])
    rep.branch_from(root, ["d4", "d5", "c4", "c6", "Nc3", "Nf6"])
    rep.branch_from(root, ["c4", "e5", "Nc3", "Nf6", "g3", "d5"])
    rep.export_pgn(str(path))
    return path


def build_shared_prefix_fixture(path: Path) -> Path:
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()
    root = rep.root_node
    common = ["e4", "e5", "Nf3", "Nc6", "Nc3"]
    suffixes = ["Bb4", "Bc5", "Nf6", "a6", "d6", "f5", "h6"]
    for last in suffixes:
        rep.branch_from(root, common + [last])
    rep.export_pgn(str(path))
    return path


def read_games(path: Path) -> list[chess_pgn.Game]:
    games: list[chess_pgn.Game] = []
    with open(path, "r", encoding="utf-8") as handle:
        while True:
            game = chess_pgn.read_game(handle)
            if game is None:
                break
            games.append(game)
    return games


def test_split_cli_generates_multiple_games(tmp_path: Path):
    input_path = build_cli_fixture(tmp_path / "split_input.pgn")
    output_path = tmp_path / "split_output.pgn"

    runner = CliRunner()
    result = runner.invoke(
        split_cli,
        [
            str(input_path),
            "--output",
            str(output_path),
            "--side",
            "white",
            "--max-moves",
            "3",
        ],
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists()

    games = read_games(output_path)
    assert len(games) >= 2
    assert all("SetUp" not in game.headers for game in games)
    assert all("FEN" not in game.headers for game in games)
    assert any(game.headers.get("Event", "").startswith("1.e4") for game in games)
    assert any(game.headers.get("Event", "").startswith("1.d4") for game in games)


def test_split_cli_trim_event_prefix(tmp_path: Path):
    input_path = build_shared_prefix_fixture(tmp_path / "split_input.pgn")
    output_path = tmp_path / "split_output.pgn"

    runner = CliRunner()
    result = runner.invoke(
        split_cli,
        [
            str(input_path),
            "--output",
            str(output_path),
            "--side",
            "white",
            "--max-moves",
            "3",
            "--trim-event-prefix",
        ],
    )
    assert result.exit_code == 0, result.output
    games = read_games(output_path)
    events = [game.headers.get("Event") for game in games]
    assert set(events) == {
        "3...Bb4",
        "3...Bc5",
        "3...Nf6",
        "3...a6",
        "3...d6",
        "3...f5",
        "3...h6",
    }
