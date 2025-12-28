from __future__ import annotations

from pathlib import Path

import chess
from click.testing import CliRunner
import pytest

from rep_grow.build import PlayerProgress, click_main
from rep_grow.repertoire import RepertoireConfig


@pytest.fixture
def cli_runner() -> CliRunner:
    return CliRunner()


def existing_binary() -> str:
    for candidate in ("/bin/sh", "/usr/bin/env"):
        if Path(candidate).exists():
            return candidate
    raise RuntimeError("No standard shell binary found for tests")


def test_build_requires_exactly_one_source(cli_runner):
    engine_path = existing_binary()
    result = cli_runner.invoke(
        click_main,
        ["--side", "white", "--engine-path", engine_path],
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


def test_build_rejects_invalid_numeric_flags(cli_runner):
    engine_path = existing_binary()

    cases = [
        (["--initial-iterations", "0"], "--initial-iterations"),
        (["--iterations-per-cycle", "0"], "--iterations-per-cycle"),
        (["--target-hero-moves", "0"], "--target-hero-moves"),
        (["--min-games-stop", "0"], "--min-games-stop"),
        (["--max-cycles", "0"], "--max-cycles"),
        (["--max-total-iterations", "0"], "--max-total-iterations"),
        (["--engine-pool-size", "0"], "--engine-pool-size"),
        (["--explorer-pct", "0"], "--explorer-pct"),
        (["--explorer-pct-final", "0"], "--explorer-pct-final"),
        (["--explorer-min-game-share", "0"], "--explorer-min-game-share"),
        (
            ["--explorer-min-game-share-final", "0"],
            "--explorer-min-game-share-final",
        ),
        (["--max-player-moves", "0"], "--max-player-moves"),
        (["--min-games-reached", "0"], "--min-games-reached"),
        (
            ["--target-hero-moves", "3", "--max-player-moves", "2"],
            "--max-player-moves",
        ),
        (["--ramp-iterations", "0"], "--ramp-iterations"),
    ]

    for extra_flags, needle in cases:
        result = cli_runner.invoke(
            click_main,
            [
                "--side",
                "white",
                "--initial-san",
                "e4",
                "--engine-path",
                engine_path,
                *extra_flags,
            ],
        )
        assert result.exit_code == 2
        assert needle in result.output


def test_build_runs_grow_prune_loop(cli_runner, tmp_path, monkeypatch):
    engine_path = existing_binary()
    grow_calls: list[tuple[int, int, str]] = []
    prune_calls: list[Path] = []
    progress_queue = [
        PlayerProgress(remaining_targets=1, min_depth=0, max_depth=2),
        PlayerProgress(remaining_targets=0, min_depth=1, max_depth=3),
    ]

    class DummyRep:
        def __init__(self):
            self.initial_san = "e4 e5"
            self.side = chess.WHITE
            self.pgn = "stub"
            self.exported: list[str] = []
            self.nodes_by_fen = {}

        def export_pgn(self, path: str):
            self.exported.append(path)

    dummy_rep = DummyRep()

    def fake_load(run_config):
        # ensure config received defaults
        assert run_config.initial_san == dummy_rep.initial_san
        return dummy_rep

    def fake_grow(rep, *, iterations, iteration_offset, run_config, initial_moves_slug):
        grow_calls.append((iterations, iteration_offset, initial_moves_slug))
        return iteration_offset + iterations

    def fake_prune(rep, *, preferred_moves, prune_path):
        prune_calls.append(prune_path)
        assert preferred_moves == ("Nf3",)
        return rep

    monkeypatch.setattr("rep_grow.build._load_repertoire", fake_load)
    monkeypatch.setattr("rep_grow.build._grow_iterations", fake_grow)
    progress_queue = [
        PlayerProgress(remaining_targets=1, min_depth=0, max_depth=1),
        PlayerProgress(remaining_targets=0, min_depth=0, max_depth=1),
    ]

    def fake_progress(*_args, **_kw):
        return (
            progress_queue.pop(0) if progress_queue else PlayerProgress(0, None, None)
        )

    monkeypatch.setattr("rep_grow.build._player_progress", fake_progress)
    monkeypatch.setattr("rep_grow.build._prune_and_reload", fake_prune)

    output_dir = tmp_path / "out"
    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--initial-san",
            dummy_rep.initial_san,
            "--initial-iterations",
            "2",
            "--iterations-per-cycle",
            "1",
            "--target-hero-moves",
            "3",
            "--output-dir",
            str(output_dir),
            "--engine-path",
            engine_path,
            "--preferred-move",
            "Nf3",
        ],
    )

    assert result.exit_code == 0, result.output

    assert grow_calls == [
        (2, 0, "1_e4_e5"),
        (1, 2, "1_e4_e5"),
    ]

    assert len(prune_calls) == 1
    assert prune_calls[0].name == "1_e4_e5__pruned_cycle_1.pgn"

    assert dummy_rep.exported
    assert dummy_rep.exported[-1].endswith("__build_final.pgn")


def test_build_honors_disable_explorer_flag(cli_runner, monkeypatch):
    engine_path = existing_binary()

    captured = {}

    def fake_run_build(run_config):  # pragma: no cover - invoked for assertions
        captured["explorer_enabled"] = run_config.repertoire_config.explorer_enabled
        captured["analysis_provider"] = run_config.repertoire_config.analysis_provider

    monkeypatch.setattr("rep_grow.build._run_build", fake_run_build)

    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "black",
            "--initial-san",
            "d4 Nf6",
            "--engine-path",
            engine_path,
            "--disable-explorer",
        ],
    )

    assert result.exit_code == 0, result.output
    assert captured["explorer_enabled"] is False
    assert captured["analysis_provider"] == "stockfish"


def test_build_ramps_explorer_and_thresholds(cli_runner, tmp_path, monkeypatch):
    engine_path = existing_binary()
    snapshots: list[tuple[float, float, float]] = []

    class DummyRep:
        def __init__(self):
            self.initial_san = "d4 d5"
            self.side = chess.BLACK
            self.pgn = "stub"
            self.config = RepertoireConfig(
                explorer_pct=90.0,
                explorer_min_game_share=0.05,
                stockfish_best_score_threshold=30,
            )

        @property
        def leaf_nodes(self):
            return []

        async def expand_leaves_by_turn(self, **_kwargs):  # pragma: no cover
            snapshots.append(
                (
                    self.config.explorer_pct,
                    self.config.explorer_min_game_share,
                    self.config.stockfish_best_score_threshold,
                )
            )

        def export_pgn(self, _path: str):
            pass

        def player_move_count(self, _node):  # pragma: no cover
            return 0

    dummy_rep = DummyRep()

    monkeypatch.setattr("rep_grow.build._load_repertoire", lambda _rc: dummy_rep)
    monkeypatch.setattr(
        "rep_grow.build._leaf_turn_counts", lambda *_args, **_kw: (1, 0)
    )
    monkeypatch.setattr("rep_grow.build._repertoire_move_count", lambda _rep: 0)
    monkeypatch.setattr(
        "rep_grow.build._player_progress",
        lambda *_args, **_kw: PlayerProgress(
            remaining_targets=0, min_depth=0, max_depth=1
        ),
    )

    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "black",
            "--initial-san",
            dummy_rep.initial_san,
            "--initial-iterations",
            "2",
            "--iterations-per-cycle",
            "1",
            "--target-hero-moves",
            "2",
            "--output-dir",
            str(tmp_path),
            "--engine-path",
            engine_path,
            "--explorer-pct",
            "90",
            "--explorer-pct-final",
            "50",
            "--explorer-min-game-share",
            "5",
            "--explorer-min-game-share-final",
            "1",
            "--best-score-threshold",
            "30",
            "--best-score-threshold-final",
            "10",
            "--ramp-iterations",
            "2",
        ],
    )

    assert result.exit_code == 0, result.output
    assert snapshots == [(70.0, 0.030000000000000002, 20), (50.0, 0.01, 10)]


def test_build_rejects_nonpositive_ramp(cli_runner, tmp_path):
    engine_path = existing_binary()
    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--initial-san",
            "e4",
            "--output-dir",
            str(tmp_path),
            "--engine-path",
            engine_path,
            "--ramp-iterations",
            "0",
        ],
    )

    assert result.exit_code == 2
    assert "--ramp-iterations" in result.output


def test_build_defaults_ramp_iterations_to_initial(cli_runner, tmp_path, monkeypatch):
    engine_path = existing_binary()
    snapshots: list[float] = []

    class DummyRep:
        def __init__(self):
            self.initial_san = "d4 d5"
            self.side = chess.WHITE
            self.pgn = "stub"
            self.config = RepertoireConfig(
                explorer_pct=90.0, explorer_min_game_share=0.05
            )
            self.nodes_by_fen = {}

        @property
        def leaf_nodes(self):
            return []

        async def expand_leaves_by_turn(self, **_kwargs):  # pragma: no cover
            snapshots.append(self.config.explorer_pct)

        def export_pgn(self, _path: str):
            pass

        def player_move_count(self, _node):  # pragma: no cover
            return 0

    dummy_rep = DummyRep()

    monkeypatch.setattr("rep_grow.build._load_repertoire", lambda _rc: dummy_rep)
    monkeypatch.setattr("rep_grow.build._leaf_turn_counts", lambda *_a, **_k: (1, 0))
    monkeypatch.setattr("rep_grow.build._repertoire_move_count", lambda _rep: 0)
    progress_queue = [
        PlayerProgress(remaining_targets=1, min_depth=0, max_depth=1),
        PlayerProgress(remaining_targets=0, min_depth=0, max_depth=1),
    ]

    def fake_progress_ramp(*_args, **_kw):
        return progress_queue.pop(0)

    monkeypatch.setattr("rep_grow.build._player_progress", fake_progress_ramp)
    monkeypatch.setattr("rep_grow.build._prune_and_reload", lambda *_a, **_k: dummy_rep)

    # ramp iterations should default to initial-iterations=3
    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--initial-san",
            dummy_rep.initial_san,
            "--initial-iterations",
            "3",
            "--iterations-per-cycle",
            "1",
            "--target-hero-moves",
            "4",
            "--output-dir",
            str(tmp_path),
            "--engine-path",
            engine_path,
            "--explorer-pct",
            "90",
            "--explorer-pct-final",
            "30",
        ],
    )

    assert result.exit_code == 0, result.output
    # three iterations in cycle 1 ramp down, cycle 2 iteration stays final (30)
    assert snapshots == [70.0, 50.0, 30.0, 30.0]


def test_build_no_ramp_when_finals_missing(cli_runner, tmp_path, monkeypatch):
    engine_path = existing_binary()
    snapshots: list[tuple[float, float, int]] = []

    class DummyRep:
        def __init__(self):
            self.initial_san = "d4"
            self.side = chess.WHITE
            self.pgn = "stub"
            self.config = RepertoireConfig(
                explorer_pct=80.0,
                explorer_min_game_share=0.02,
                stockfish_best_score_threshold=25,
            )

        @property
        def leaf_nodes(self):
            return []

        async def expand_leaves_by_turn(self, **_kwargs):  # pragma: no cover
            snapshots.append(
                (
                    self.config.explorer_pct,
                    self.config.explorer_min_game_share,
                    self.config.stockfish_best_score_threshold,
                )
            )

        def export_pgn(self, _path: str):
            pass

        def player_move_count(self, _node):  # pragma: no cover
            return 0

    dummy_rep = DummyRep()

    monkeypatch.setattr("rep_grow.build._load_repertoire", lambda _rc: dummy_rep)
    monkeypatch.setattr("rep_grow.build._leaf_turn_counts", lambda *_a, **_k: (1, 0))
    monkeypatch.setattr("rep_grow.build._repertoire_move_count", lambda _rep: 0)
    monkeypatch.setattr(
        "rep_grow.build._player_progress",
        lambda *_args, **_kw: PlayerProgress(
            remaining_targets=0, min_depth=0, max_depth=1
        ),
    )

    result = cli_runner.invoke(
        click_main,
        [
            "--side",
            "white",
            "--initial-san",
            dummy_rep.initial_san,
            "--initial-iterations",
            "2",
            "--iterations-per-cycle",
            "1",
            "--target-hero-moves",
            "2",
            "--output-dir",
            str(tmp_path),
            "--engine-path",
            engine_path,
        ],
    )

    assert result.exit_code == 0, result.output
    assert snapshots == [(95.0, 0.05, 20), (95.0, 0.05, 20)]
