from __future__ import annotations

import asyncio
import random
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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

    async def raw_explorer(
        self,
        retries: int = 6,
        backoff: float = 1.0,
        jitter: float = 0.3,
    ):
        """Fetch explorer data with exponential backoff and Retry-After support."""

        last_error: httpx.HTTPStatusError | None = None
        delay = max(0.1, backoff)
        transient_statuses = {429, 500, 502, 503, 504}

        async with httpx.AsyncClient() as client:
            for attempt in range(1, retries + 1):
                response = await client.get(self.BASE_URL, params=self.params)
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    last_error = exc
                    status = exc.response.status_code if exc.response else None
                    if status in transient_statuses and attempt < retries:
                        wait_time = self._retry_delay_seconds(
                            exc.response,
                            delay,
                            jitter,
                        )
                        await asyncio.sleep(wait_time)
                        delay = min(delay * 2, 30.0)
                        continue
                    raise
                self._response = ExplorerResponse(**response.json())
                return self._response

        if last_error:
            raise last_error
        raise RuntimeError("Explorer API did not return a response")

    @staticmethod
    def _retry_delay_seconds(
        response: httpx.Response | None,
        fallback: float,
        jitter: float,
    ) -> float:
        header_value = response.headers.get("Retry-After") if response else None
        delay = fallback
        if header_value is not None:
            parsed = LichessExplorerApi._parse_retry_after(header_value)
            if parsed is not None:
                delay = max(parsed, fallback)
        return max(0.1, delay + random.uniform(0.0, max(0.0, jitter)))

    @staticmethod
    def _parse_retry_after(value: str) -> float | None:
        if value.isdigit():
            return float(value)
        try:
            retry_dt = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if retry_dt.tzinfo is None:
            retry_dt = retry_dt.replace(tzinfo=timezone.utc)
        delta = (retry_dt - datetime.now(timezone.utc)).total_seconds()
        return max(0.0, delta)

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

    def top_p_pct_moves(
        self,
        pct: float = 90.0,
        max_moves: int | None = 8,
        min_game_share: float = 0.01,
    ) -> list[ExplorerMoveTotal]:
        """Return moves that cover pct% of games while capping the tail.

        pct
            Target coverage expressed as a percentage of the total games.
        max_moves
            Optional hard limit to keep the resulting branching factor small.
            When ``None`` all qualifying moves are returned.
        min_game_share
            Lower bound (between 0 and 1) for the share contributed by the
            move that triggers the stop condition. This prevents dozens of
            near-zero moves from being appended simply to chase the final few
            percentage points.
        """

        totals = sorted(self.totals, key=lambda item: item[1], reverse=True)
        if not totals:
            return []

        total_games = sum(total for _, total in totals)
        if total_games <= 0:
            return []

        threshold = total_games * max(0.0, pct) / 100.0
        cumulative = 0
        result: list[ExplorerMoveTotal] = []

        for move, total in totals:
            if total <= 0:
                continue
            share = total / total_games
            previous_cumulative = cumulative
            cumulative += total
            result.append({"move": move, "total": total})

            hit_cap = max_moves is not None and len(result) >= max_moves

            already_met_pct = previous_cumulative >= threshold and threshold > 0
            if already_met_pct and share < max(0.0, min_game_share):
                result.pop()
                cumulative -= total
                break

            if cumulative >= threshold or hit_cap or threshold == 0:
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
