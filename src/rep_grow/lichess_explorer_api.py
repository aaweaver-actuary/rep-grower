from __future__ import annotations

import httpx
from pydantic import BaseModel, Field
from typing import Optional, TypedDict
from typing_extensions import Annotated


class ExplorerMoveTotal(TypedDict):
    move: str
    total: int


class LichessExplorerApi:
    BASE_URL = "https://explorer.lichess.ovh/lichess"

    def __init__(
        self,
        fen: str,
        variant: str = "standard",
        play: str = "",
        speeds: str = "ultraBullet,bullet,blitz,rapid",
        ratings: list[int] = [0, 1000, 1200, 1400, 1600, 1800, 2000, 2200, 2500],
        since: str = "1952-01",
        until: str = "3000-12",
        moves: str = "15",
        topGames: int = 0,
        recentGames: int = 0,
        history: str = "false",
    ):
        self.fen = fen
        self.variant = variant
        self.play = play
        self.speeds = speeds
        self._ratings = ratings
        self.since = since
        self.until = until
        self.moves = moves
        self._topGames = topGames
        self._recentGames = recentGames
        self.history = history
        self._response = None

    @property
    def ratings(self) -> str:
        return ",".join(str(r) for r in self._ratings)

    @property
    def topGames(self) -> str:
        return str(self._topGames)

    @property
    def recentGames(self) -> str:
        return str(self._recentGames)

    @property
    def params(self) -> dict[str, str]:
        return {
            "variant": self.variant,
            "fen": self.fen,
            "play": self.play,
            "speeds": self.speeds,
            "ratings": self.ratings,
            "since": self.since,
            "until": self.until,
            "moves": self.moves,
            "topGames": self.topGames,
            "recentGames": self.recentGames,
            "history": self.history,
        }

    async def raw_explorer(self):
        async with httpx.AsyncClient() as client:
            response = await client.get(self.BASE_URL, params=self.params)
            response.raise_for_status()
            print(response.json())
            self._response = ExplorerResponse(**response.json())

    @property
    def response(self) -> ExplorerResponse:
        if self._response is None:
            raise ValueError("Response not fetched yet. Call raw_explorer() first.")
        return self._response

    @property
    def move_list(self) -> list[tuple[str, int, int, int]]:
        return [
            (m["san"], m["white"], m["draws"], m["black"]) for m in self.response.moves
        ]

    @property
    def totals(self) -> list[tuple[str, int]]:
        return [
            (m["san"], m["white"] + m["draws"] + m["black"])
            for m in self.response.moves
        ]

    def top_p_pct_moves(self, pct: float = 95.0) -> list[ExplorerMoveTotal]:
        """Return moves that account for the top pct% of games."""
        total_games = self.response.totalGames
        threshold = total_games * (pct / 100.0)
        cumulative = 0
        result: list[ExplorerMoveTotal] = []
        for move, total in self.totals:
            cumulative += total
            result.append({"move": move, "total": total})
            if cumulative >= threshold:
                break
        return result


class ExplorerResponse(BaseModel):
    opening: Annotated[Optional[dict], Field(description="Opening information")]
    white: Annotated[int, Field(description="Number of games won by white")]
    draws: Annotated[int, Field(description="Number of drawn games")]
    black: Annotated[int, Field(description="Number of games won by black")]
    moves: Annotated[list[dict], Field(description="List of move statistics")]
    recentGames: Annotated[list[dict], Field(description="List of recent games")]
    topGames: Annotated[list[dict], Field(description="List of top games")]

    def to_dict(self):
        return self.model_dump()  # type: ignore

    def json(self):
        return self.model_dump_json(indent=2)  # type: ignore

    def to_json(self):
        return self.json()

    def write_json(self, filepath: str):
        with open(filepath, "w") as f:
            f.write(self.to_json())

    @property
    def totalGames(self) -> int:
        return self.white + self.black + self.draws
