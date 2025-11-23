import pytest


@pytest.fixture(autouse=True)
def isolate_stockfish_db(tmp_path, monkeypatch):
    """Force each test to use an isolated DuckDB cache to avoid lock contention."""
    db_file = tmp_path / "stockfish.db"
    monkeypatch.setenv("REP_GROW_STOCKFISH_DB", str(db_file))
    yield
