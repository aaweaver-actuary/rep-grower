from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


@dataclass(frozen=True)
class GrowOptions:
    initial_san: str
    pgn_file: str
    iterations: int
    output_dir: str
    side: str
    engine_path: str
    engine_depth: int
    engine_pool_size: int | None
    engine_multi_pv: int
    best_score_threshold: int
    explorer_pct: float
    explorer_min_game_share: float
    max_player_moves: int | None
    min_games_reached: int | None
    analysis_provider: str
    lichess_eval_path: str | None = None

    def has_exactly_one_source(self) -> bool:
        return bool(self.initial_san) ^ bool(self.pgn_file)


@dataclass(frozen=True)
class BuildOptions:
    initial_san: str
    pgn_file: str
    side: str
    output_dir: str
    prune_output_dir: str | None
    initial_iterations: int
    iterations_per_cycle: int
    target_player_moves: int
    min_games_stop: int
    max_cycles: int
    max_total_iterations: int
    engine_path: str
    engine_depth: int
    engine_pool_size: int | None
    engine_multi_pv: int
    best_score_threshold: int
    analysis_provider: str
    explorer_enabled: bool
    explorer_pct: float
    explorer_min_game_share: float
    explorer_pct_final: float | None
    explorer_min_game_share_final: float | None
    best_score_threshold_final: int | None
    ramp_iterations: int | None
    max_player_moves: int | None
    min_games_reached: int | None
    lichess_eval_path: str | None
    preferred_moves: Iterable[str]
    analysis_providers: Iterable[str] | None = None


@dataclass(frozen=True)
class PruneOptions:
    pgn_file: str
    side: str
    output_path: str
    preferred_moves: Iterable[str]


@dataclass(frozen=True)
class SplitOptions:
    pgn_file: str
    side: str
    output_path: str
    max_moves: int
    trim_event_prefix: bool
