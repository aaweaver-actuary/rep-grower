from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


DEFAULT_EXPLORER_RATINGS = [0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500]


@dataclass(frozen=True)
class StockfishRequest:
    fen: str
    multi_pv: int = 10
    variant: str = "standard"
    depth: int = 20
    think_time: float | None = None
    best_score_threshold: int = 20
    engine_path: str | Path | None = "/opt/homebrew/bin/stockfish"
    pool_size: int | None = None

    def params(self) -> dict[str, Any]:
        return {
            "fen": self.fen,
            "multiPv": str(self.multi_pv),
            "variant": self.variant,
            "enginePath": str(self.engine_path) if self.engine_path else None,
            "depth": str(self.depth),
            "thinkTime": str(self.think_time) if self.think_time else None,
            "poolSize": str(self.pool_size) if self.pool_size else None,
        }


@dataclass(frozen=True)
class LichessAnalysisRequest:
    fen: str
    multi_pv: int = 10
    variant: str = "standard"
    best_score_threshold: int = 20

    def params(self) -> dict[str, Any]:
        return {"fen": self.fen, "multiPv": str(self.multi_pv), "variant": self.variant}


@dataclass(frozen=True)
class ExplorerRequest:
    fen: str
    variant: str = "standard"
    play: str = ""
    speeds: str = "ultraBullet,bullet,blitz,rapid"
    ratings: list[int] | str = field(
        default_factory=lambda: list(DEFAULT_EXPLORER_RATINGS)
    )
    since: str = "1952-01"
    until: str = "3000-12"
    moves: str = "15"
    topGames: int = 0
    recentGames: int = 0
    history: str = "false"

    def ratings_str(self) -> str:
        if isinstance(self.ratings, str):
            return self.ratings
        return ",".join(str(r) for r in self.ratings)

    def params(self) -> dict[str, str]:
        return {
            "variant": self.variant,
            "fen": self.fen,
            "play": self.play,
            "speeds": self.speeds,
            "ratings": self.ratings_str(),
            "since": self.since,
            "until": self.until,
            "moves": self.moves,
            "topGames": str(self.topGames),
            "recentGames": str(self.recentGames),
            "history": self.history,
        }
