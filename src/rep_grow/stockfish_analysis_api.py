from __future__ import annotations

import asyncio
import os
from pathlib import Path

from . import _core
from .lichess_analysis_api import EvalResponse
from .db import DuckDb, DbQueryContext


class StockfishAnalysisApi:
    """Local Stockfish-backed drop-in replacement for LichessAnalysisApi."""

    BASE_URL = "stockfish"  # preserved for strategy compatibility

    def __init__(
        self,
        fen: str,
        multi_pv: int = 10,
        variant: str = "standard",
        *,
        engine_path: str | Path | None = "/opt/homebrew/bin/stockfish",
        depth: int = 20,
        think_time: float | None = None,
        best_score_threshold: int = 20,
        db_path: str | Path | None = None,
        pool_size: int | None = None,
    ):
        if multi_pv < 1:
            raise ValueError("multi_pv must be at least 1")
        if depth <= 0 and (think_time is None or think_time <= 0):
            raise ValueError("Specify a positive depth or think_time")
        if pool_size is not None and pool_size < 1:
            raise ValueError("pool_size must be at least 1 when provided")

        self.fen = fen
        self.multi_pv = multi_pv
        self.variant = variant
        self.engine_path = Path(engine_path) if engine_path else Path("stockfish")
        self.depth = depth
        self.think_time = think_time
        self.best_score_threshold = best_score_threshold
        self.pool_size = pool_size
        self._last_response_source: str = "uninitialized"

        # Initialize database and check for cached response
        self._db = DuckDb(db_path=db_path)
        self._ctx = DbQueryContext(
            fen=self.fen, multipv=self.multi_pv, depth=self.depth
        )

        cached = self._db.get(self._ctx)
        if cached is not None:
            # If the position is cached, load it
            self._response: EvalResponse | None = EvalResponse(**cached)
            self._last_response_source = "cache"
        else:
            # Otherwise, no response yet
            self._response = None

    def params(self):
        return {
            "fen": self.fen,
            "multiPv": str(self.multi_pv),
            "variant": self.variant,
            "enginePath": str(self.engine_path),
            "depth": str(self.depth),
            "thinkTime": str(self.think_time) if self.think_time else None,
            "poolSize": str(self.pool_size) if self.pool_size else None,
        }

    async def raw_evaluation(self, *, use_cache: bool = True) -> EvalResponse:
        if use_cache and self._response is not None:
            self._last_response_source = "cache"
            return self._response

        result = await asyncio.to_thread(self._evaluate_position)
        # Persist latest evaluation so repeated calls can skip engine work.
        self._db.put(result, self._ctx)
        self._response = EvalResponse(**result)
        self._last_response_source = "engine"
        return self._response

    def _evaluate_position(self) -> dict:
        pool_size = self.pool_size or self._default_pool_size()
        try:
            return _core.stockfish_evaluate(
                self.fen,
                str(self.engine_path),
                self.depth,
                self.multi_pv,
                self.think_time,
                pool_size,
            )
        except RuntimeError as exc:  # pragma: no cover - surfaces Python RuntimeError
            raise RuntimeError(str(exc)) from exc

    @staticmethod
    def _default_pool_size() -> int:
        cpu_count = os.cpu_count() or 1
        return max(1, min(4, cpu_count))

    @property
    def response(self) -> EvalResponse:
        if self._response is None:
            raise ValueError("Response not fetched yet. Call raw_evaluation() first.")
        return self._response

    @property
    def moves(self):
        return self.response.moves

    @property
    def best_move(self):
        moves = self.moves
        if not moves:
            return None
        return moves[0][1]

    @property
    def best_score(self):
        moves = self.moves
        if not moves:
            return None
        return moves[0][0]

    def scores_within(self, threshold: int):
        best_score = self.best_score
        if best_score is None:
            return []
        return [
            (score, move)
            for score, move in self.moves
            if abs(score - best_score) <= threshold
        ]

    def moves_within(self, threshold: int):
        return [move for _, move in self.scores_within(threshold)]

    @property
    def best_moves(self):
        return self.moves_within(self.best_score_threshold)

    @property
    def last_response_source(self) -> str:
        return self._last_response_source
