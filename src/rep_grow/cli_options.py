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

    def has_exactly_one_source(self) -> bool:
        return bool(self.initial_san) ^ bool(self.pgn_file)


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
