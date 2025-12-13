from __future__ import annotations

import asyncio
import os
from pathlib import Path

from . import _core
from .lichess_analysis_api import EvalResponse
from .db import DuckDb, DbQueryContext, DuckDbStockfishStore
from .fetcher import CachedFetcher
from .requests import StockfishRequest


class StockfishAnalysisApi(CachedFetcher[DbQueryContext, EvalResponse]):
    """Local Stockfish-backed drop-in replacement for LichessAnalysisApi."""

    BASE_URL = "stockfish"  # preserved for strategy compatibility

    def __init__(
        self,
        fen: str | None = None,
        multi_pv: int = 10,
        variant: str = "standard",
        *,
        engine_path: str | Path | None = "/opt/homebrew/bin/stockfish",
        depth: int = 20,
        think_time: float | None = None,
        best_score_threshold: int = 20,
        db_path: str | Path | None = None,
        pool_size: int | None = None,
        request: StockfishRequest | None = None,
        cache_store=None,
    ):
        if request is not None:
            req = request
        else:
            if fen is None:
                raise ValueError("fen is required when request is not provided")
            req = StockfishRequest(
                fen=fen,
                multi_pv=multi_pv,
                variant=variant,
                engine_path=engine_path,
                depth=depth,
                think_time=think_time,
                best_score_threshold=best_score_threshold,
                pool_size=pool_size,
            )

        if req.multi_pv < 1:
            raise ValueError("multi_pv must be at least 1")
        if req.depth <= 0 and (req.think_time is None or req.think_time <= 0):
            raise ValueError("Specify a positive depth or think_time")
        if req.pool_size is not None and req.pool_size < 1:
            raise ValueError("pool_size must be at least 1 when provided")

        self._request = req
        self.engine_path = (
            Path(req.engine_path) if req.engine_path else Path("stockfish")
        )
        self.best_score_threshold = req.best_score_threshold
        self._last_response_source = "uninitialized"

        ctx = DbQueryContext(fen=req.fen, multipv=req.multi_pv, depth=req.depth)

        store = cache_store
        if store is None:
            store = DuckDbStockfishStore(DuckDb(db_path=db_path))

        super().__init__(ctx=ctx, cache_store=store)

    def params(self):
        return self._request.params()

    async def raw_evaluation(self, *, use_cache: bool = True) -> EvalResponse:
        if use_cache and self._response is not None:
            self._last_response_source = "cache"
            return self._response

        result = await asyncio.to_thread(self._evaluate_position)
        # Persist latest evaluation so repeated calls can skip engine work.
        self._response = EvalResponse(**result)
        self._record(self._response)
        self._last_response_source = "engine"
        return self._response

    def _evaluate_position(self) -> dict:
        pool_size = self.pool_size or self._default_pool_size()
        try:
            return _core.stockfish_evaluate(
                self._request.fen,
                str(self.engine_path),
                self._request.depth,
                self._request.multi_pv,
                self._request.think_time,
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

    @property
    def fen(self) -> str:
        return self._request.fen

    @property
    def multi_pv(self) -> int:
        return self._request.multi_pv

    @property
    def variant(self) -> str:
        return self._request.variant

    @property
    def depth(self) -> int:
        return self._request.depth

    @property
    def think_time(self) -> float | None:
        return self._request.think_time

    @property
    def pool_size(self) -> int | None:
        return self._request.pool_size

    def _hydrate(self, payload: dict) -> EvalResponse:
        return EvalResponse(**payload)

    def _serialize(self, response: EvalResponse) -> dict:
        return response.model_dump()  # type: ignore[arg-type]
