import pytest
import chess

from rep_grow.repertoire import Repertoire


@pytest.fixture(autouse=True)
def isolate_stockfish_db(tmp_path, monkeypatch):
    """Force each test to use an isolated DuckDB cache to avoid lock contention."""
    db_file = tmp_path / "stockfish.db"
    monkeypatch.setenv("REP_GROW_STOCKFISH_DB", str(db_file))
    yield


@pytest.fixture
def repertoire_factory():
    def _build(
        *,
        side: chess.Color = chess.WHITE,
        initial_san: str = "",
        branches: list[list[str]] | None = None,
    ) -> Repertoire:
        rep = Repertoire(side=side, initial_san=initial_san)
        rep.play_initial_moves()
        for branch in branches or []:
            rep.branch_from(rep.root_node, branch)
        return rep

    return _build
