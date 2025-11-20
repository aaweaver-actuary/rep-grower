from __future__ import annotations

import httpx
from pydantic import BaseModel, Field


class LichessAnalysisApi:
    BASE_URL = "https://lichess.org/api/cloud-eval"
    BEST_SCORE_THRESHOLD = 20  # centipawns

    def __init__(self, fen: str, multi_pv: int = 10, variant: str = "standard"):
        self.fen = fen
        self.multi_pv = multi_pv
        self.variant = variant
        self._response = None

    def params(self):
        return {"fen": self.fen, "multiPv": str(self.multi_pv), "variant": self.variant}

    async def raw_evaluation(self) -> EvalResponse:
        async with httpx.AsyncClient() as client:
            response = await client.get(self.BASE_URL, params=self.params())
            try:
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(
                    f"Error fetching Lichess analysis: {e.response.status_code} - {e.response.text}"
                ) from e
            self._response = EvalResponse(**response.json())
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
        moves = self.moves
        if not moves:
            return None
        return moves[0][1]  # Return the move part of the best score-move tuple

    @property
    def best_score(self):
        moves = self.moves
        if not moves:
            return None
        return moves[0][0]  # Return the score part of the best score-move tuple

    def scores_within(self, threshold: int):
        """Return moves with scores within the given threshold of the best score."""
        best_score = self.best_score
        if best_score is None:
            return []
        return [
            (score, move)
            for score, move in self.moves
            if abs(score - best_score) <= threshold
        ]

    def moves_within(self, threshold: int):
        """Return moves whose scores are within the given threshold of the best score."""
        return [move for _, move in self.scores_within(threshold)]

    @property
    def best_moves(self):
        return self.moves_within(self.BEST_SCORE_THRESHOLD)


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

    @property
    def moves(self):
        """Return a list of tuples (score, next move in SAN) for each principal variation."""
        move_list = []
        for entry in self.pvs:
            score = entry.get("score")
            if score is None:
                score = entry.get("cp")
            if score is None:
                score = entry.get("mate")
            move_list.append((score, entry["moves"].split(" ")[0]))
        move_list.sort(key=lambda x: x[0], reverse=True)
        return move_list
