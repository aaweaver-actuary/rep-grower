from rep_grow.db import DuckDb, DbQueryContext

FEN = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1"


def sample_evaluation(score: int = 80) -> dict:
    return {
        "depth": 22,
        "fen": FEN,
        "knodes": 512,
        "pvs": [{"score": score, "moves": "e2e4 e7e5"}],
    }


def test_db_query_context_hash_includes_all_fields():
    ctx_a = DbQueryContext(fen=FEN, multipv=2, depth=18)
    ctx_b = DbQueryContext(fen=FEN, multipv=2, depth=18)
    ctx_c = DbQueryContext(fen=FEN, multipv=3, depth=18)
    ctx_d = DbQueryContext(fen=FEN, multipv=2, depth=19)

    assert hash(ctx_a) == hash(ctx_b)
    assert hash(ctx_a) != hash(ctx_c)
    assert hash(ctx_a) != hash(ctx_d)


def test_duckdb_get_returns_none_when_missing(tmp_path):
    db = DuckDb(str(tmp_path / "cache.duckdb"))
    ctx = DbQueryContext(fen=FEN, multipv=2, depth=18)

    assert db.get(ctx) is None


def test_duckdb_round_trip_persists_payload(tmp_path):
    db = DuckDb(str(tmp_path / "cache.duckdb"))
    ctx = DbQueryContext(fen=FEN, multipv=2, depth=18)
    payload = sample_evaluation()

    db.put(payload, ctx)
    result = db.get(ctx)

    assert result is not None
    assert result == payload


def test_duckdb_overwrites_existing_entries(tmp_path):
    db = DuckDb(str(tmp_path / "cache.duckdb"))
    ctx = DbQueryContext(fen=FEN, multipv=2, depth=18)

    db.put(sample_evaluation(score=30), ctx)
    db.put(sample_evaluation(score=10), ctx)

    result = db.get(ctx)
    assert result is not None
    assert result["pvs"][0]["score"] == 10


def test_duckdb_handles_multiple_contexts(tmp_path):
    db = DuckDb(str(tmp_path / "cache.duckdb"))
    ctx_main = DbQueryContext(fen=FEN, multipv=2, depth=18)
    ctx_other = DbQueryContext(fen=FEN, multipv=3, depth=18)

    db.put(sample_evaluation(score=45), ctx_main)
    db.put(sample_evaluation(score=5), ctx_other)

    main_result = db.get(ctx_main)
    other_result = db.get(ctx_other)

    assert main_result is not None
    assert other_result is not None
    assert main_result["pvs"][0]["score"] == 45
    assert other_result["pvs"][0]["score"] == 5


def test_duckdb_deduplicates_identical_contexts(tmp_path):
    db = DuckDb(str(tmp_path / "cache.duckdb"))
    ctx = DbQueryContext(fen=FEN, multipv=2, depth=18)

    db.put(sample_evaluation(score=20), ctx)
    db.put(sample_evaluation(score=25), ctx)

    row = db.conn.sql("SELECT COUNT(*) FROM positions").fetchone()
    assert row is not None
    assert row[0] == 1


def test_duckdb_persists_across_instances(tmp_path):
    cache_path = tmp_path / "cache.duckdb"
    ctx = DbQueryContext(fen=FEN, multipv=3, depth=22)
    payload = sample_evaluation(score=65)

    db_first = DuckDb(str(cache_path))
    db_first.put(payload, ctx)
    db_first.conn.close()
    db_first._conn = None

    db_second = DuckDb(str(cache_path))
    cached = db_second.get(ctx)
    assert cached == payload
