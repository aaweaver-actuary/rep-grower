from __future__ import annotations

from io import StringIO
from pathlib import Path

import chess
import chess.pgn as chess_pgn
from click.testing import CliRunner

from rep_grow.prune import click_main as prune_cli
from rep_grow.repertoire import Repertoire


def read_game(path: Path):
    return chess_pgn.read_game(StringIO(path.read_text(encoding="utf-8")))


def build_white_fixture(path: Path) -> Path:
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()
    root = rep.root_node
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Nc3", "Nf6", "h3"])
    rep.branch_from(root, ["d4", "d5", "e4", "Nc6", "Nc3"])
    rep.branch_from(root, ["e4", "c5", "Nc3", "Nc6", "Nf3"])
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6", "Ba4"])
    rep.export_pgn(str(path))
    return path


def build_black_fixture(path: Path) -> Path:
    rep = Repertoire(side=chess.BLACK, initial_san="")
    rep.play_initial_moves()
    root = rep.root_node
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Bb5", "a6"])
    rep.branch_from(root, ["e4", "c5", "Nf3", "d6"])
    rep.branch_from(root, ["d4", "e5", "e3", "Nc6"])
    rep.branch_from(root, ["e4", "e5", "Nc3", "Nc6", "d4"])
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Nc3", "d6", "d4"])
    rep.export_pgn(str(path))
    return path


def test_white_pruning_retains_only_most_frequent_moves(tmp_path: Path):
    input_path = build_white_fixture(tmp_path / "input.pgn")
    output_path = tmp_path / "pruned.pgn"

    runner = CliRunner()
    result = runner.invoke(
        prune_cli,
        [
            str(input_path),
            "--output",
            str(output_path),
            "--side",
            "white",
        ],
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists()

    game = read_game(output_path)
    root_variations = [move.move.uci() for move in game.variations]
    assert root_variations == ["e2e4"]

    node_e4 = game.variations[0]
    black_choices = sorted(child.move.uci() for child in node_e4.variations)
    assert black_choices == ["c7c5", "e7e5"]

    node_e5 = next(child for child in node_e4.variations if child.move.uci() == "e7e5")
    white_reply = [child.move.uci() for child in node_e5.variations]
    assert white_reply == ["g1f3"]

    node_nc6 = node_e5.variations[0].variations[0]
    white_third_move = [child.move.uci() for child in node_nc6.variations]
    assert white_third_move == ["b1c3"]


def test_black_pruning_only_affects_black_moves(tmp_path: Path):
    input_path = build_black_fixture(tmp_path / "black_input.pgn")
    output_path = tmp_path / "black_pruned.pgn"

    runner = CliRunner()
    result = runner.invoke(
        prune_cli,
        [
            str(input_path),
            "--output",
            str(output_path),
            "--side",
            "black",
        ],
    )
    assert result.exit_code == 0, result.output

    game = read_game(output_path)
    node_e4 = game.variations[0]
    black_variations = [child.move.uci() for child in node_e4.variations]
    assert len(black_variations) == 1

    node_e5 = node_e4.variations[0]
    white_second_moves = sorted(child.move.uci() for child in node_e5.variations)
    assert white_second_moves == ["b1c3", "g1f3"]
