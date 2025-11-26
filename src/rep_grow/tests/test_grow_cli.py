from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import chess
import chess.pgn as chess_pgn
from click.testing import CliRunner
import pytest

from rep_grow.grow import click_main
from rep_grow.repertoire import nodes_from_root


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
            self.last_expand_kwargs: dict[str, object] = {}
            self.play_called = False
            self.exported_paths: list[str] = []
            start = chess.Board()
            after_move = chess.Board()
            after_move.push_san("e4")
            self._leaf_nodes = [
                SimpleNamespace(fen=start.fen()),
                SimpleNamespace(fen=after_move.fen()),
            ]
            self.nodes_by_fen = {}

        @property
        def leaf_nodes(self):
            return self._leaf_nodes

        async def expand_leaves_by_turn(self, **kwargs):  # pragma: no cover
            self.expand_calls += 1
            self.last_expand_kwargs = kwargs

        def export_pgn(self, path: str):
            self.exported_paths.append(path)

        def play_initial_moves(self):
            self.play_called = True

        def player_move_count(self, _node):  # pragma: no cover - simple stub
            return 0

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


def test_cli_passes_max_player_moves(cli_runner, tmp_path, stub_repertoire):
    engine_path = existing_binary()
    output_dir = tmp_path / "cap"
    output_dir.mkdir()
    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--initial-san",
            "e4",
            "--iterations",
            "1",
            "--max-player-moves",
            "5",
            "--output-dir",
            str(output_dir),
            "--engine-path",
            engine_path,
        ],
    )
    assert result.exit_code == 0, result.output

    inst = stub_repertoire["last_instance"]
    assert inst.last_expand_kwargs.get("max_player_moves") == 5


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


def test_cli_pgn_flow_merges_multiple_games(cli_runner, tmp_path):
    engine_path = existing_binary()
    pgn_path = tmp_path / "multi.pgn"
    pgn_path.write_text(
        """
[Event "Game 1"]
1. e4 e5 2. Nf3 Nc6 *

[Event "Game 2"]
1. d4 d5 2. c4 e6 *
""".strip()
        + "\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--pgn-file",
            str(pgn_path),
            "--iterations",
            "0",
            "--output-dir",
            str(output_dir),
            "--engine-path",
            engine_path,
        ],
    )
    assert result.exit_code == 0, result.output

    exports = list(output_dir.glob("*.pgn"))
    assert len(exports) == 1
    with exports[0].open("r", encoding="utf-8") as handle:
        merged = chess_pgn.read_game(handle)
    assert merged is not None

    first_moves = {
        variation.move.uci()
        for variation in merged.variations
        if variation.move is not None
    }
    assert {"e2e4", "d2d4"}.issubset(first_moves)

    d4_node = next(
        variation
        for variation in merged.variations
        if variation.move and variation.move.uci() == "d2d4"
    )
    d4_replies = {
        child.move.uci() for child in d4_node.variations if child.move is not None
    }
    assert "d7d5" in d4_replies
    d5_node = next(
        child
        for child in d4_node.variations
        if child.move and child.move.uci() == "d7d5"
    )
    c4_node = next(
        child
        for child in d5_node.variations
        if child.move and child.move.uci() == "c2c4"
    )
    e6_moves = {
        child.move.uci() for child in c4_node.variations if child.move is not None
    }
    assert "e7e6" in e6_moves


def test_cli_pgn_flow_grows_varying_depth_branches(cli_runner, tmp_path, monkeypatch):
    engine_path = existing_binary()
    pgn_path = tmp_path / "depths.pgn"
    pgn_path.write_text(
        """
[Event "Main Line"]
1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 4. Ba4 Nf6 *

[Event "New Branch"]
1. e4 d5 *
""".strip()
        + "\n",
        encoding="utf-8",
    )

    output_dir = tmp_path / "out"
    output_dir.mkdir()

    recorded_depths: list[list[int]] = []
    recorded_growth: list[list[int]] = []

    async def fake_expand(self, *args, **kwargs):
        leaves = [(node, nodes_from_root(self, node)) for node in self.leaf_nodes]
        recorded_depths.append(sorted(depth for _, depth in leaves))
        growth: list[int] = []
        for node, start_depth in leaves:
            board = chess.Board(node.fen)
            move = next(iter(board.legal_moves))
            san = board.san(move)
            new_node = self.branch_from(node, [san])
            growth.append(nodes_from_root(self, new_node) - start_depth)
        recorded_growth.append(growth)
        return {}

    monkeypatch.setattr(
        "rep_grow.repertoire.Repertoire.expand_leaves_by_turn",
        fake_expand,
    )

    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--pgn-file",
            str(pgn_path),
            "--iterations",
            "1",
            "--output-dir",
            str(output_dir),
            "--engine-path",
            engine_path,
        ],
    )
    assert result.exit_code == 0, result.output

    assert recorded_depths, "expected grower to inspect existing leaf depths"
    assert recorded_growth, "expected grower to attempt expanding each branch"
    assert recorded_depths[0] == [2, 8]
    assert len(recorded_growth[0]) == 2
    assert all(delta == 1 for delta in recorded_growth[0])
