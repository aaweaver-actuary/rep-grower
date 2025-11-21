from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import chess
from click.testing import CliRunner
import pytest

from rep_grow.grow import click_main


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_repertoire(monkeypatch):
    created: dict[str, object] = {}

    class DummyRep:
        def __init__(self, initial_san: str, side: chess.Color, config):
            self.initial_san = initial_san
            self.side = side
            self.config = config
            self.pgn = "stub"
            self.expand_calls = 0
            self.play_called = False
            self.exported_paths: list[str] = []
            start = chess.Board()
            after_move = chess.Board()
            after_move.push_san("e4")
            self._leaf_nodes = [
                SimpleNamespace(fen=start.fen()),
                SimpleNamespace(fen=after_move.fen()),
            ]

        @property
        def leaf_nodes(self):
            return self._leaf_nodes

        async def expand_leaves_by_turn(self):  # pragma: no cover - used in tests
            self.expand_calls += 1

        def export_pgn(self, path: str):
            self.exported_paths.append(path)

        def play_initial_moves(self):
            self.play_called = True

    def make_rep(initial_san: str, side: chess.Color, config):
        inst = DummyRep(initial_san, side, config)
        created["last_instance"] = inst
        return inst

    def fake_from_str(cls, side: str, initial_san: str, *, config):
        entry = {
            "side": side,
            "initial_san": initial_san,
            "config": config,
        }
        created["from_str"] = entry
        color = chess.WHITE if side.lower() == "white" else chess.BLACK
        return make_rep(initial_san, color, config)

    def fake_from_pgn_file(
        cls,
        *,
        side: chess.Color,
        pgn_path: str | Path,
        config,
    ):
        entry = {
            "side": side,
            "pgn_path": str(pgn_path),
            "config": config,
        }
        created["from_pgn"] = entry
        return make_rep("e4 e5 Nf3", side, config)

    monkeypatch.setattr("rep_grow.grow.Repertoire.from_str", classmethod(fake_from_str))
    monkeypatch.setattr(
        "rep_grow.grow.Repertoire.from_pgn_file", classmethod(fake_from_pgn_file)
    )

    return created


def existing_binary() -> str:
    for candidate in ("/bin/sh", "/usr/bin/env"):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("No standard shell binary found for tests")


def test_cli_requires_exactly_one_source(cli_runner):
    engine_path = existing_binary()
    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--engine-path",
            engine_path,
        ],
    )
    assert result.exit_code == 2
    assert "either --initial-san or --pgn-file" in result.output

    with cli_runner.isolated_filesystem() as tmp_dir:
        pgn = Path(tmp_dir) / "game.pgn"
        pgn.write_text("1. e4 e5 *", encoding="utf-8")
        result_both = cli_runner.invoke(
            click_main,
            [
                "--side",
                "white",
                "--engine-path",
                engine_path,
                "--initial-san",
                "e4",
                "--pgn-file",
                str(pgn),
            ],
        )
    assert result_both.exit_code == 2
    assert "but not both" in result_both.output


def test_cli_initial_san_flow(cli_runner, tmp_path, stub_repertoire):
    engine_path = existing_binary()
    output_dir = tmp_path / "out"
    output_dir.mkdir()
    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--initial-san",
            "e4 e5",
            "--iterations",
            "2",
            "--output-dir",
            str(output_dir),
            "--engine-path",
            engine_path,
            "--engine-depth",
            "24",
            "--engine-multi-pv",
            "4",
            "--best-score-threshold",
            "30",
            "--explorer-pct",
            "92",
            "--explorer-min-game-share",
            "0.02",
        ],
    )
    assert result.exit_code == 0, result.output

    inst = stub_repertoire["last_instance"]
    assert inst.play_called is True
    assert inst.expand_calls == 2
    assert len(inst.exported_paths) == 3  # 2 iterations + final export

    expected_base = "1_e4_e5"
    assert inst.exported_paths[0].endswith(f"{expected_base}__iteration_1.pgn")
    assert inst.exported_paths[1].endswith(f"{expected_base}__iteration_2.pgn")
    assert inst.exported_paths[-1].endswith(f"{expected_base}.pgn")

    cfg = stub_repertoire["from_str"]["config"]
    assert cfg.stockfish_multi_pv == 4
    assert cfg.stockfish_depth == 24
    assert cfg.stockfish_best_score_threshold == 30
    assert cfg.explorer_pct == 92
    assert cfg.explorer_min_game_share == 0.02


def test_cli_pgn_flow(cli_runner, tmp_path, stub_repertoire):
    engine_path = existing_binary()
    pgn_path = tmp_path / "game.pgn"
    pgn_path.write_text("1. e4 e5 2. Nf3 Nc6 *", encoding="utf-8")

    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "black",
            "--pgn-file",
            str(pgn_path),
            "--iterations",
            "1",
            "--output-dir",
            str(tmp_path),
            "--engine-path",
            engine_path,
        ],
    )
    assert result.exit_code == 0, result.output

    entry = stub_repertoire["from_pgn"]
    assert entry["side"] == chess.BLACK
    assert Path(entry["pgn_path"]) == pgn_path

    inst = stub_repertoire["last_instance"]
    assert inst.expand_calls == 1
    assert len(inst.exported_paths) == 2
