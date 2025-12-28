from typing import cast

import chess
import pytest

from rep_grow.grow import (
    GrowRunConfig,
    _expand_repertoire_pass,
    _export_iteration_snapshot,
    _export_final_repertoire,
    _iteration_label,
    _run_single_iteration,
)
from rep_grow.repertoire import Repertoire, RepertoireConfig


class FakeRepertoire:
    def __init__(self):
        self.calls: list[dict] = []
        self.initial_san = "e4"
        self.pgn = "PGN"

    async def expand_leaves_by_turn(self, **kwargs):
        self.calls.append(kwargs)

    def export_pgn(self, path: str):
        self.calls.append({"export": path})


@pytest.fixture()
def run_config() -> GrowRunConfig:
    rep_config = RepertoireConfig(
        stockfish_multi_pv=1,
        stockfish_depth=1,
        stockfish_engine_path="stockfish",
        stockfish_best_score_threshold=20,
        stockfish_pool_size=1,
        explorer_pct=95.0,
        explorer_min_game_share=0.05,
        min_games_reached=10,
        analysis_provider="stockfish",
        lichess_eval_path=None,
    )
    return GrowRunConfig(
        side_color=chess.WHITE,
        initial_san="e4",
        pgn_file=None,
        iterations=2,
        output_dir="/tmp",
        max_player_moves=3,
        repertoire_config=rep_config,
    )


def test_iteration_label_formats():
    assert (
        _iteration_label(1, total_targets=4, iterations=3)
        == "Iteration 1 progress (4 nodes)"
    )
    assert _iteration_label(1, total_targets=0, iterations=1).startswith(
        "Expanding repertoire"
    )


def test_expand_repertoire_pass_without_targets(run_config):
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


def test_export_helpers(run_config, tmp_path):
    rep = FakeRepertoire()
    snap = _export_iteration_snapshot(
        cast(Repertoire, rep),
        run_config=run_config,
        initial_moves_slug="slug",
        iteration=1,
    )
    assert "slug__iteration_1.pgn" in snap

    _export_final_repertoire(
        cast(Repertoire, rep),
        run_config=run_config,
        initial_moves_slug="slug",
    )
    exported = [c for c in rep.calls if "export" in c]
    assert exported


def test_run_single_iteration_invokes_helpers(monkeypatch, run_config):
    rep = FakeRepertoire()

    monkeypatch.setattr(
        "rep_grow.grow._leaf_turn_counts", lambda *_args, **_kwargs: (2, 1)
    )
    move_counts = iter([10, 15])
    monkeypatch.setattr(
        "rep_grow.grow._repertoire_move_count", lambda _rep: next(move_counts)
    )
    monkeypatch.setattr(
        "rep_grow.grow._expand_repertoire_pass",
        lambda *_args, **_kwargs: rep.calls.append({"expanded": True}),
    )
    monkeypatch.setattr(
        "rep_grow.grow._export_iteration_snapshot",
        lambda *_args, **_kwargs: rep.calls.append({"exported": True}),
    )

    _run_single_iteration(
        cast(Repertoire, rep),
        iteration=1,
        iterations=2,
        run_config=run_config,
        initial_moves_slug="slug",
    )

    assert {"expanded": True} in rep.calls
    assert {"exported": True} in rep.calls
