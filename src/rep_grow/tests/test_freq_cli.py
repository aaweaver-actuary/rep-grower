from __future__ import annotations

import json
from pathlib import Path

import chess
from click.testing import CliRunner

from rep_grow.repertoire import Repertoire
from rep_grow.frequencies import click_main as freq_cli


def build_freq_fixture(path: Path) -> Path:
    rep = Repertoire(side=chess.WHITE, initial_san="")
    rep.play_initial_moves()
    root = rep.root_node
    rep.branch_from(root, ["e4", "e5", "Nf3", "Nc6", "Bb5"])
    rep.branch_from(root, ["e4", "c5", "Nc3", "Nc6", "Nf3"])
    rep.branch_from(root, ["d4", "d5", "c4", "c6", "Nc3"])
    rep.export_pgn(str(path))
    return path


def test_freq_cli_outputs_json(tmp_path: Path):
    input_path = build_freq_fixture(tmp_path / "freq_input.pgn")

    runner = CliRunner()
    result = runner.invoke(
        freq_cli,
        [
            str(input_path),
            "--side",
            "white",
        ],
    )
    assert result.exit_code == 0, result.output

    payload = json.loads(result.output)
    assert payload["side"] == "white"
    assert payload["total_nodes"] >= 1
    assert any(
        entry["san"] in {"e4", "d4"}
        for entry in payload["rankings"][next(iter(payload["rankings"]))]
    )


def test_freq_cli_writes_file(tmp_path: Path):
    input_path = build_freq_fixture(tmp_path / "freq_input_file.pgn")
    output_path = tmp_path / "freq_output.json"

    runner = CliRunner()
    result = runner.invoke(
        freq_cli,
        [
            str(input_path),
            "--side",
            "white",
            "--output",
            str(output_path),
            "--indent",
            "0",
        ],
    )
    assert result.exit_code == 0, result.output
    assert output_path.exists()

    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["rankings"]
