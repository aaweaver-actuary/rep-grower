from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import duckdb

from .cache import CacheContext, CacheStore
from .config import Config


@dataclass(frozen=True)
class TableSchema:
    name: str
    ddl: str


DEFAULT_FILEPATH = "~/.stockfish.db"
ENV_VAR = "REP_GROW_STOCKFISH_DB"


def _resolve_db_path(
    db_path: str | os.PathLike[str] | None = None,
    config: Config | None = None,
) -> str:
    cfg = config or Config(
        stockfish_db_default=DEFAULT_FILEPATH, stockfish_db_env=ENV_VAR
    )
    candidate = db_path or os.environ.get(
        cfg.stockfish_db_env, cfg.stockfish_db_default
    )
    return str(Path(candidate).expanduser())


@dataclass
class DbQueryContext(CacheContext):
    fen: str
    multipv: int
    depth: int

    def key(self) -> str:
        return f"{self.fen}|{self.multipv}|{self.depth}"

    def __hash__(self) -> int:
        import hashlib

        key_bytes = self.key().encode("utf-8")
        hash_bytes = hashlib.sha256(key_bytes).digest()
        return int.from_bytes(hash_bytes, byteorder="big")


@dataclass
class ExplorerQueryContext(CacheContext):
    fen: str
    variant: str
    play: str
    speeds: str
    ratings: str
    since: str
    until: str
    moves: str
    top_games: int
    recent_games: int
    history: str

    def key(self) -> str:
        parts = (
            self.fen,
            self.variant,
            self.play,
            self.speeds,
            self.ratings,
            self.since,
            self.until,
            self.moves,
            str(self.top_games),
            str(self.recent_games),
            self.history,
        )
        return "|".join(parts)

    def __hash__(self) -> int:
        import hashlib

        key_bytes = self.key().encode("utf-8")
        hash_bytes = hashlib.sha256(key_bytes).digest()
        return int.from_bytes(hash_bytes, byteorder="big")


class DuckDb:
    """Wrapper around DuckDB connection for storing Stockfish evaluations, so they can be reused and retrieved quickly by hashing FENs."""

    def __init__(
        self,
        db_path: str | os.PathLike[str] | None = None,
        *,
        config: Config | None = None,
    ):
        self._config = config or Config(
            stockfish_db_default=DEFAULT_FILEPATH, stockfish_db_env=ENV_VAR
        )
        self.db_path = _resolve_db_path(db_path, self._config)
        self._conn = None

        self.initialize_db()

    @property
    def conn(self) -> duckdb.DuckDBPyConnection:
        if self._conn is None:
            self._conn = duckdb.connect(database=self.db_path, read_only=False)
        return self._conn

    def __enter__(self):
        return self.conn

    def __exit__(self, exc_type, exc_value, traceback):
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __call__(self, query: str, parameters: tuple = ()) -> duckdb.DuckDBPyRelation:
        conn = self.conn
        params = parameters if parameters else None
        return conn.sql(query, params=params)

    @staticmethod
    def _hash_fen(fen: str) -> int:
        import hashlib

        key_bytes = fen.encode("utf-8")
        hash_bytes = hashlib.sha256(key_bytes).digest()
        return int.from_bytes(hash_bytes, byteorder="big")

    def initialize_db(self):
        """Create necessary tables if they don't exist."""
        schemas = [
            TableSchema(
                name="positions",
                ddl="""
                CREATE TABLE IF NOT EXISTS positions (
                    eval_id HUGEINT PRIMARY KEY,
                    fen TEXT,
                    multipv INTEGER DEFAULT 10,
                    depth INTEGER DEFAULT 20,
                    evaluation JSON,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """,
            ),
            TableSchema(
                name="explorer",
                ddl="""
                CREATE TABLE IF NOT EXISTS explorer (
                    explorer_id HUGEINT PRIMARY KEY,
                    fen TEXT,
                    variant TEXT,
                    play TEXT,
                    speeds TEXT,
                    ratings TEXT,
                    since TEXT,
                    until TEXT,
                    moves TEXT,
                    top_games INTEGER,
                    recent_games INTEGER,
                    history TEXT,
                    response JSON,
                    last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                """,
            ),
        ]
        for schema in schemas:
            self(schema.ddl)

    def get(self, ctx: DbQueryContext) -> dict[str, Any] | None:
        """Retrieve evaluation for the given FEN, or None if not found."""
        eval_id = hash(ctx)
        query = "SELECT evaluation FROM positions WHERE eval_id = ?;"
        result = self(query, (eval_id,))

        row = result.fetchone()
        if row is None:
            return None

        evaluation = row[0]
        if isinstance(evaluation, (bytes, bytearray)):
            evaluation = evaluation.decode("utf-8")
        if isinstance(evaluation, str):
            return json.loads(evaluation)
        return evaluation

    def put(self, evaluation: dict, ctx: DbQueryContext) -> None:
        """Store evaluation for the given FEN. If it already exists, update it."""
        eval_id = hash(ctx)
        payload = json.dumps(evaluation)
        timestamp = datetime.now()
        query = """
            INSERT INTO positions (eval_id, fen, multipv, depth, evaluation, last_updated)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (eval_id) DO UPDATE
            SET evaluation = EXCLUDED.evaluation,
                last_updated = EXCLUDED.last_updated;
            """
        self(
            query,
            (eval_id, ctx.fen, ctx.multipv, ctx.depth, payload, timestamp),
        )

    def get_explorer(self, ctx: ExplorerQueryContext) -> dict[str, Any] | None:
        """Retrieve explorer response for the given context, or None if missing."""
        explorer_id = hash(ctx)
        query = "SELECT response FROM explorer WHERE explorer_id = ?;"
        result = self(query, (explorer_id,))

        row = result.fetchone()
        if row is None:
            return None

        payload = row[0]
        if isinstance(payload, (bytes, bytearray)):
            payload = payload.decode("utf-8")
        if isinstance(payload, str):
            return json.loads(payload)
        return payload

    def put_explorer(self, response: dict, ctx: ExplorerQueryContext) -> None:
        """Store explorer response for the given context; updates on conflict."""
        explorer_id = hash(ctx)
        payload = json.dumps(response)
        timestamp = datetime.now()
        query = """
            INSERT INTO explorer (
                explorer_id,
                fen,
                variant,
                play,
                speeds,
                ratings,
                since,
                until,
                moves,
                top_games,
                recent_games,
                history,
                response,
                last_updated
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (explorer_id) DO UPDATE
            SET response = EXCLUDED.response,
                last_updated = EXCLUDED.last_updated;
            """
        self(
            query,
            (
                explorer_id,
                ctx.fen,
                ctx.variant,
                ctx.play,
                ctx.speeds,
                ctx.ratings,
                ctx.since,
                ctx.until,
                ctx.moves,
                ctx.top_games,
                ctx.recent_games,
                ctx.history,
                payload,
                timestamp,
            ),
        )


class DuckDbStockfishStore(CacheStore[DbQueryContext]):
    """Cache store adapter for Stockfish evaluations."""

    def __init__(self, db: DuckDb):
        self._db = db

    def get(self, ctx: DbQueryContext) -> dict[str, Any] | None:
        return self._db.get(ctx)

    def put(self, payload: dict[str, Any], ctx: DbQueryContext) -> None:
        self._db.put(payload, ctx)


class DuckDbExplorerStore(CacheStore[ExplorerQueryContext]):
    """Cache store adapter for Lichess Explorer responses."""

    def __init__(self, db: DuckDb):
        self._db = db

    def get(self, ctx: ExplorerQueryContext) -> dict[str, Any] | None:
        return self._db.get_explorer(ctx)

    def put(self, payload: dict[str, Any], ctx: ExplorerQueryContext) -> None:
        self._db.put_explorer(payload, ctx)
