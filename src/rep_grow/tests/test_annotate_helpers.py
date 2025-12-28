from typing import cast

import pytest

from rep_grow.annotate_reach_counts import _annotate_nodes
from rep_grow.repertoire import Repertoire, RepertoireNode


pytestmark = pytest.mark.anyio


@pytest.fixture()
def anyio_backend():
    return "asyncio"


class FakePgnNode:
    def __init__(self) -> None:
        self.comment = ""


class FakeNode:
    def __init__(self, fen: str, games_reached: int | None = None) -> None:
        self.fen = fen
        self.games_reached = games_reached
        self.pgn_nodes = [FakePgnNode()]


class FakeRepertoire:
    def __init__(self, pgn: str = "PGN") -> None:
        self.pgn = pgn


async def test_annotate_nodes_skips_when_games_present(monkeypatch):
    calls: list[str] = []

    async def fake_fetch(fen: str) -> int | None:
        calls.append(fen)
        return 42

    node = FakeNode("fen1", games_reached=5)
    updated = await _annotate_nodes(
        [cast(RepertoireNode, node)],
        repertoire=cast(Repertoire, FakeRepertoire()),
        max_concurrency=1,
        force=False,
        retries=1,
        backoff=0.0,
        jitter=0.0,
        progress_every=None,
        progress_bar=False,
        checkpoint_every=None,
        checkpoint_base=None,
        total_nodes=1,
        dry_run=False,
        fetch_total_games=fake_fetch,
    )

    assert updated == 0
    assert calls == []


async def test_annotate_nodes_updates_and_checkpoint(tmp_path):
    results = {"fen1": 7, "fen2": 9}

    async def fake_fetch(fen: str) -> int | None:
        return results.get(fen)

    nodes = [FakeNode("fen1"), FakeNode("fen2")]
    checkpoint_base = tmp_path / "annotated.pgn"

    updated = await _annotate_nodes(
        [cast(RepertoireNode, node) for node in nodes],
        repertoire=cast(Repertoire, FakeRepertoire("PGN")),
        max_concurrency=2,
        force=True,
        retries=1,
        backoff=0.0,
        jitter=0.0,
        progress_every=1,
        progress_bar=False,
        checkpoint_every=1,
        checkpoint_base=checkpoint_base,
        total_nodes=len(nodes),
        dry_run=False,
        fetch_total_games=fake_fetch,
    )

    assert updated == 2
    assert nodes[0].games_reached == 7
    assert nodes[1].games_reached == 9

    first_checkpoint = checkpoint_base.with_name(
        f"{checkpoint_base.stem}_checkpoint_1{checkpoint_base.suffix}"
    )
    second_checkpoint = checkpoint_base.with_name(
        f"{checkpoint_base.stem}_checkpoint_2{checkpoint_base.suffix}"
    )
    assert first_checkpoint.read_text(encoding="utf-8") == "PGN"
    assert second_checkpoint.read_text(encoding="utf-8") == "PGN"
