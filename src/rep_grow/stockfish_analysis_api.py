from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Iterable, Sequence

import chess
import chess.engine

from .lichess_analysis_api import EvalResponse


class StockfishAnalysisApi:
    """Local Stockfish-backed drop-in replacement for LichessAnalysisApi."""

    BASE_URL = "stockfish"  # preserved for strategy compatibility
    BEST_SCORE_THRESHOLD = 25  # centipawns

    def __init__(
        self,
        fen: str,
        multi_pv: int = 10,
        variant: str = "standard",
        *,
        engine_path: str | Path | None = "/opt/homebrew/bin/stockfish",
        depth: int = 20,
        think_time: float | None = None,
    ):
        if multi_pv < 1:
            raise ValueError("multi_pv must be at least 1")
        if depth <= 0 and (think_time is None or think_time <= 0):
            raise ValueError("Specify a positive depth or think_time")

        self.fen = fen
        self.multi_pv = multi_pv
        self.variant = variant
        self.engine_path = Path(engine_path) if engine_path else Path("stockfish")
        self.depth = depth
        self.think_time = think_time
        self._response: EvalResponse | None = None

    def params(self):
        return {
            "fen": self.fen,
            "multiPv": str(self.multi_pv),
            "variant": self.variant,
            "enginePath": str(self.engine_path),
            "depth": str(self.depth),
            "thinkTime": str(self.think_time) if self.think_time else None,
        }

    async def raw_evaluation(self) -> EvalResponse:
        result = await asyncio.to_thread(self._evaluate_position)
        self._response = EvalResponse(**result)
        return self._response

    def _evaluate_position(self) -> dict:
        board = chess.Board(self.fen)
        limit = (
            chess.engine.Limit(depth=self.depth)
            if self.depth > 0
            else chess.engine.Limit(time=self.think_time)
        )
        try:
            engine = chess.engine.SimpleEngine.popen_uci(str(self.engine_path))
        except FileNotFoundError as exc:  # pragma: no cover - user environment
            raise RuntimeError(
                f"Stockfish binary not found at {self.engine_path!s}."
            ) from exc

        with engine:
            info = engine.analyse(board, limit=limit, multipv=self.multi_pv)

        if isinstance(info, dict):
            info_list: Sequence[dict] = [info]
        else:
            info_list = info

        pvs = [self._convert_entry(board, entry) for entry in info_list]
        depth = max((entry.get("depth", 0) for entry in info_list), default=self.depth)
        nodes = max((entry.get("nodes", 0) for entry in info_list), default=0)
        knodes = int(nodes / 1000)

        return {
            "depth": depth,
            "fen": self.fen,
            "knodes": knodes,
            "pvs": pvs,
        }

    def _convert_entry(self, board: chess.Board, entry: dict) -> dict:
        pv_moves: Sequence[chess.Move] = entry.get("pv") or []
        uci_moves = self._moves_to_uci(board, pv_moves)
        score: chess.engine.PovScore | None = entry.get("score")
        pov_score = score.pov(board.turn) if score else None
        cp = pov_score.score() if pov_score else None
        mate = pov_score.mate() if pov_score else None
        numeric_score = cp if cp is not None else mate
        return {
            "cp": cp,
            "mate": mate,
            "score": numeric_score,
            "moves": " ".join(uci_moves),
        }

    def _moves_to_uci(
        self, board: chess.Board, moves: Iterable[chess.Move]
    ) -> list[str]:  # board kept for signature parity
        return [move.uci() for move in moves]

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
        return self.moves_within(self.BEST_SCORE_THRESHOLD)
