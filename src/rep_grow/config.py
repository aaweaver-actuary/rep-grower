from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Config:
    stockfish_db_default: str = "~/.stockfish.db"
    stockfish_db_env: str = "REP_GROW_STOCKFISH_DB"
