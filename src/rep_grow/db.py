from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
import json
from pathlib import Path
from typing import Any

import duckdb

DEFAULT_FILEPATH = "~/.stockfish.db"
ENV_VAR = "REP_GROW_STOCKFISH_DB"


def _resolve_db_path(db_path: str | os.PathLike[str] | None = None) -> str:
    candidate = db_path or os.environ.get(ENV_VAR, DEFAULT_FILEPATH)
    return str(Path(candidate).expanduser())


@dataclass
class DbQueryContext:
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


class DuckDb:
    """Wrapper around DuckDB connection for storing Stockfish evaluations, so they can be reused and retrieved quickly by hashing FENs."""

    def __init__(self, db_path: str | os.PathLike[str] | None = None):
        self.db_path = _resolve_db_path(db_path)
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
        """Create necessary table if it doesn't exist."""
        query = """
            CREATE TABLE IF NOT EXISTS positions (
                eval_id HUGEINT PRIMARY KEY,
                fen TEXT,
                multipv INTEGER DEFAULT 10,
                depth INTEGER DEFAULT 20,
                evaluation JSON,
                last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        self(query)

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
