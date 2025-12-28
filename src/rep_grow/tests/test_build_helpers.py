from pathlib import Path
from typing import cast

import chess
import pytest

from rep_grow.build import (
    BuildRunConfig,
    _expand_repertoire_pass,
    _iteration_label,
    _ramped_value,
    _player_progress,
    _run_single_iteration,
)
from rep_grow.repertoire import Repertoire, RepertoireConfig


class FakeRepertoire:
    def __init__(self):
        self.calls: list[dict] = []

    async def expand_leaves_by_turn(self, **kwargs):
        self.calls.append(kwargs)

    def export_pgn(self, *_args, **_kwargs):  # pragma: no cover - patched out
        raise AssertionError("export_pgn should be patched in these tests")


@pytest.fixture()
def run_config() -> BuildRunConfig:
    rep_config = RepertoireConfig(
        stockfish_multi_pv=1,
        stockfish_depth=1,
        stockfish_engine_path="stockfish",
        stockfish_best_score_threshold=20,
        stockfish_pool_size=1,
        explorer_pct=95.0,
        explorer_enabled=True,
        explorer_min_game_share=0.05,
        min_games_reached=10,
        analysis_provider="stockfish",
        lichess_eval_path=None,
    )
    return BuildRunConfig(
        side_color=chess.WHITE,
        initial_san="e4",
        pgn_file=None,
        output_dir=Path("/tmp"),
        prune_output_dir=Path("/tmp"),
        target_player_moves=2,
        min_games_stop=1,
        max_player_moves=3,
        max_total_iterations=5,
        iterations_first=1,
        iterations_per_cycle=1,
        max_cycles=1,
        repertoire_config=rep_config,
        preferred_moves=(),
        ramp_iterations=None,
        explorer_pct_start=95.0,
        explorer_pct_final=95.0,
        explorer_min_game_share_start=0.05,
        explorer_min_game_share_final=0.05,
        best_score_threshold_start=20,
        best_score_threshold_final=20,
    )


def test_expand_repertoire_pass_skips_progress_when_no_targets(run_config):
    rep = FakeRepertoire()
    _expand_repertoire_pass(
        cast(Repertoire, rep),
        run_config=run_config,
        total_targets=0,
        label="ignored",
    )
    assert rep.calls == [
        {
            "max_player_moves": run_config.max_player_moves,
            "min_games_reached": run_config.repertoire_config.min_games_reached,
        }
    ]


def test_iteration_label_formats(iterations=3):
    assert _iteration_label(1, total_targets=4, iterations=iterations) == (
        "Build iteration 1 progress (4 nodes)"
    )
    assert _iteration_label(2, total_targets=0, iterations=1) == "Build iteration 2"


def test_ramped_value_handles_none_and_caps():
    assert _ramped_value(10, 20, None, 0) == 10.0
    assert _ramped_value(10, 20, 2, 0) == 10.0
    assert _ramped_value(10, 20, 2, 1) == 15.0
    assert _ramped_value(10, 20, 2, 5) == 20.0


def test_run_single_iteration_calls_helpers(monkeypatch, run_config):
    rep = FakeRepertoire()

    monkeypatch.setattr(
        "rep_grow.build._apply_ramp_settings",
        lambda _rep, _cfg, idx: rep.calls.append({"ramp": idx}),
    )
    monkeypatch.setattr(
        "rep_grow.build._leaf_turn_counts",
        lambda _rep, _max_moves, _min_reached: (2, 1),
    )
    move_counts = iter([10, 15, 20, 25])
    monkeypatch.setattr(
        "rep_grow.build._repertoire_move_count",
        lambda _rep: next(move_counts),
    )
    monkeypatch.setattr(
        "rep_grow.build._expand_repertoire_pass",
        lambda *_args, **_kwargs: rep.calls.append({"expanded": True}),
    )
    monkeypatch.setattr(
        "rep_grow.build._export_iteration_snapshot",
        lambda *_args, **_kwargs: rep.calls.append({"exported": True}),
    )

    new_index, reached_cap = _run_single_iteration(
        cast(Repertoire, rep),
        iteration_index=0,
        iterations=2,
        run_config=run_config,
        initial_moves_slug="slug",
    )

    assert new_index == 1
    assert reached_cap is False
    assert {"ramp": 1} in rep.calls
    assert {"expanded": True} in rep.calls
    assert {"exported": True} in rep.calls

    capped_config = run_config.__class__(
        **{**run_config.__dict__, "max_total_iterations": 1}
    )
    new_index, reached_cap = _run_single_iteration(
        cast(Repertoire, rep),
        iteration_index=0,
        iterations=1,
        run_config=capped_config,
        initial_moves_slug="slug",
    )
    assert new_index == 1
    assert reached_cap is True


def test_player_progress_filters_by_games_and_depth():
    class DummyNode:
        def __init__(self, fen: str, games: int, depth: int):
            self.fen = fen
            self.games_reached = games
            self._depth = depth

    class DummyRep:
        def __init__(self, nodes: list[DummyNode]):
            self.leaf_nodes = nodes
            self.side = chess.WHITE

        def player_move_count(self, node: DummyNode) -> int:
            return node._depth

    board = chess.Board()
    start_fen = board.fen()
    board2 = chess.Board()
    for move in ["d4", "d5", "c4", "c6"]:
        board2.push_san(move)
    fen_white_to_move_depth4 = board2.fen()
    board3 = chess.Board()
    board3.push_san("e4")
    fen_black_to_move = board3.fen()

    nodes = [
        DummyNode(start_fen, games=2, depth=1),  # player node, counts
        DummyNode(
            fen_white_to_move_depth4, games=0, depth=4
        ),  # player node but filtered by games
        DummyNode(fen_black_to_move, games=5, depth=2),  # opponent turn
    ]

    rep = DummyRep(nodes)
    progress = _player_progress(
        cast(Repertoire, rep), target_player_moves=3, min_games_stop=1
    )

    assert progress.remaining_targets == 1
    assert progress.min_depth == 1
    assert progress.max_depth == 4
