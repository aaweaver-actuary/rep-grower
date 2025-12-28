from __future__ import annotations

import os
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field

from .requests import LichessAnalysisRequest
from .fetcher import CachedFetcher
from .db import DbQueryContext, DuckDb, DuckDbStockfishStore


@dataclass(frozen=True)
class _ScoredMove:
    move: str
    score: int
    mate: int | None

    @property
    def mate_distance(self) -> int | None:
        return abs(self.mate) if self.mate is not None else None

    @property
    def is_winning_mate(self) -> bool:
        return self.mate is not None and self.mate > 0

    @property
    def sort_key(self) -> tuple[int, int]:
        # Winning mates sort above all other lines, preferring shorter mates.
        if self.is_winning_mate:
            return (2, -(self.mate_distance or 0))
        # Losing mates sort below scored lines but remain stable by distance.
        if self.mate is not None:
            return (0, -(self.mate_distance or 0))
        # Centipawn scores retain their original ordering semantics.
        return (1, self.score)


class LichessAnalysisApi(CachedFetcher[DbQueryContext, "EvalResponse"]):
    BASE_URL = "https://lichess.org/api/cloud-eval"

    def __init__(
        self,
        fen: str,
        multi_pv: int = 10,
        variant: str = "standard",
        *,
        best_score_threshold: int = 20,
        request: LichessAnalysisRequest | None = None,
        db_path: str | os.PathLike[str] | None = None,
        cache_store=None,
    ):
        self._request = request or LichessAnalysisRequest(
            fen=fen,
            multi_pv=multi_pv,
            variant=variant,
            best_score_threshold=best_score_threshold,
        )
        self.best_score_threshold = self._request.best_score_threshold

        ctx = DbQueryContext(
            fen=self._request.fen,
            multipv=self._request.multi_pv,
            depth=0,
            provider="lichess",
        )

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

        async with httpx.AsyncClient() as client:
            response = await client.get(self.BASE_URL, params=self.params())
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(
                    f"Error fetching Lichess analysis: {e.response.status_code} - {e.response.text}"
                ) from e
            self._response = EvalResponse(**response.json())
            self._record(self._response)
            self._last_response_source = "network"
        return self._response

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
        scored = self.response._scored_moves()
        if not scored:
            return None
        return scored[0].move

    @property
    def best_score(self):
        scored = self.response._scored_moves()
        if not scored:
            return None
        return scored[0].score

    def _hydrate(self, payload: dict) -> "EvalResponse":
        return EvalResponse(**payload)

    def _serialize(self, response: "EvalResponse") -> dict:
        return response.model_dump()

    def scores_within(self, threshold: int):
        """Return moves with scores within the given threshold of the best score."""
        scored = self.response._scored_moves()
        if not scored:
            return []

        mate_candidates = [
            sm
            for sm in scored
            if sm.is_winning_mate
            and sm.mate_distance is not None
            and sm.mate_distance < 10
        ]
        if mate_candidates:
            closest = min(
                sm.mate_distance
                for sm in mate_candidates
                if sm.mate_distance is not None
            )
            return [
                (sm.score, sm.move)
                for sm in scored
                if sm.is_winning_mate and sm.mate_distance == closest
            ]

        best_score = scored[0].score
        return [
            (sm.score, sm.move)
            for sm in scored
            if abs(sm.score - best_score) <= threshold
        ]

    def moves_within(self, threshold: int):
        """Return moves whose scores are within the given threshold of the best score."""
        return [move for _, move in self.scores_within(threshold)]

    @property
    def best_moves(self):
        return self.moves_within(self.best_score_threshold)

    @property
    def fen(self) -> str:
        return self._request.fen

    @property
    def multi_pv(self) -> int:
        return self._request.multi_pv

    @property
    def variant(self) -> str:
        return self._request.variant


class EvalResponse(BaseModel):
    depth: int = Field(..., description="The depth of the evaluation")
    fen: str = Field(..., description="The FEN string of the position")
    knodes: int = Field(..., description="The number of kilo-nodes searched")
    pvs: list[dict] = Field(
        ...,
        description="List of principal variations, each containing the evaluation in centipawns or mate in N moves, as well as the move sequence producing that evaluation",
    )

    def as_json(self):
        return self.model_dump()

    @property
    def raw_evaluations(self):
        return self.pvs

    def _scored_moves(self) -> list[_ScoredMove]:
        scored: list[_ScoredMove] = []
        for entry in self.pvs:
            moves_text = entry.get("moves") or ""
            move = moves_text.split(" ")[0] if moves_text else ""
            if not move:
                continue

            mate = entry.get("mate")
            score = entry.get("score")
            cp = entry.get("cp")
            resolved_score = score if score is not None else cp
            if resolved_score is None and mate is not None:
                resolved_score = mate
            if resolved_score is None:
                continue

            scored.append(_ScoredMove(move=move, score=int(resolved_score), mate=mate))

        scored.sort(key=lambda sm: sm.sort_key, reverse=True)
        return scored

    @property
    def moves(self):
        """Return score/move pairs sorted with winning mates ahead of centipawn lines."""
        return [(item.score, item.move) for item in self._scored_moves()]
