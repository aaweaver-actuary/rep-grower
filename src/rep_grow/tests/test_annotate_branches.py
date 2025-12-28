from __future__ import annotations

from typing import cast

import httpx
import pytest

from rep_grow import annotate_reach_counts as arc
from rep_grow.repertoire import Repertoire, RepertoireNode

pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


class DummyResponse:
    def __init__(self, total_games: int | None = None) -> None:
        if total_games is not None:
            self.totalGames = total_games  # noqa: N815


class DummyExplorer:
    def __init__(
        self,
        fen: str,
        *,
        raises: Exception | None = None,
        response: DummyResponse | None = None,
    ):
        self._raises = raises
        self.response = response
        self.fen = fen

    async def raw_explorer(self, *args, **kwargs):  # noqa: ANN002, ANN003
        if self._raises:
            raise self._raises
        return None


async def test_fetch_total_games_falls_back_when_typeerror(monkeypatch):
    # raw_explorer signature without kwargs triggers TypeError on first call; fallback call populates response
    class TypeErrorExplorer(DummyExplorer):
        async def raw_explorer(self):  # noqa: ANN001, D401
            self.response = DummyResponse(12)

    monkeypatch.setattr(arc, "LichessExplorerApi", TypeErrorExplorer)

    result = await arc._fetch_total_games("fen", retries=1, backoff=0.0, jitter=0.0)

    assert result == 12


async def test_fetch_total_games_handles_http_error(monkeypatch):
    monkeypatch.setattr(
        arc,
        "LichessExplorerApi",
        lambda fen: DummyExplorer(fen, raises=httpx.HTTPError("boom")),
    )

    result = await arc._fetch_total_games("fen", retries=1, backoff=0.0, jitter=0.0)

    assert result is None


async def test_fetch_total_games_handles_general_exception(monkeypatch):
    monkeypatch.setattr(
        arc,
        "LichessExplorerApi",
        lambda fen: DummyExplorer(fen, raises=RuntimeError("oops")),
    )

    result = await arc._fetch_total_games("fen", retries=1, backoff=0.0, jitter=0.0)

    assert result is None


async def test_fetch_total_games_typeerror_then_fallback_failure(monkeypatch):
    class FailingFallbackExplorer(DummyExplorer):
        async def raw_explorer(self):  # noqa: ANN001, D401
            raise RuntimeError("fallback fail")

    # First call with kwargs triggers TypeError; fallback call raises RuntimeError
    monkeypatch.setattr(
        arc, "LichessExplorerApi", lambda fen: FailingFallbackExplorer(fen)
    )

    result = await arc._fetch_total_games("fen", retries=1, backoff=0.0, jitter=0.0)

    assert result is None


async def test_fetch_total_games_returns_none_when_missing_total_games(monkeypatch):
    monkeypatch.setattr(
        arc,
        "LichessExplorerApi",
        lambda fen: DummyExplorer(fen, response=DummyResponse(total_games=None)),
    )

    result = await arc._fetch_total_games("fen", retries=1, backoff=0.0, jitter=0.0)

    assert result is None


async def test_annotate_single_node_handles_fetch_exception():
    class DummyNode:
        def __init__(self):
            self.fen = "fen"
            self.games_reached = None
            self.pgn_nodes = []

    node = cast(RepertoireNode, DummyNode())

    async def failing_fetch(_fen: str):
        raise RuntimeError("fail")

    updated = await arc._annotate_single_node(
        node, force=True, fetch_total_games=failing_fetch
    )

    assert updated is False
    assert node.games_reached is None


async def test_annotate_single_node_returns_false_when_none():
    class DummyNode:
        def __init__(self):
            self.fen = "fen"
            self.games_reached = None
            self.pgn_nodes = []

    node = cast(RepertoireNode, DummyNode())

    async def none_fetch(_fen: str):
        return None

    updated = await arc._annotate_single_node(
        node, force=True, fetch_total_games=none_fetch
    )

    assert updated is False
    assert node.games_reached is None


async def test_annotate_nodes_progress_bar_prints_and_skips_checkpoint(tmp_path):
    class DummyNode:
        def __init__(self):
            self.fen = "fen"
            self.games_reached = None
            self.pgn_nodes = [type("P", (), {"comment": ""})()]

    node = cast(RepertoireNode, DummyNode())

    async def fetch(_fen: str):
        return 3

    class FakeRepertoire:
        def __init__(self):
            self.pgn = "PGN"

    updated = await arc._annotate_nodes(
        [node],
        repertoire=cast(Repertoire, FakeRepertoire()),
        max_concurrency=1,
        force=True,
        retries=1,
        backoff=0.0,
        jitter=0.0,
        progress_every=1,
        progress_bar=True,
        checkpoint_every=2,  # should not write because updated=1
        checkpoint_base=tmp_path / "annotated.pgn",
        total_nodes=1,
        dry_run=False,
        fetch_total_games=fetch,
    )

    assert updated == 1
    assert not (tmp_path / "annotated_checkpoint_1.pgn").exists()
