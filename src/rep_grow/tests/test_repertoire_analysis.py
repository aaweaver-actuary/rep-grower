import weakref
from types import SimpleNamespace

import chess
import pytest

from rep_grow import repertoire_analysis
from rep_grow.repertoire import Repertoire
from rep_grow.repertoire_analysis import (
    MoveFingerprint,
    player_move_frequencies,
    player_move_rankings,
)


def _build_repertoire() -> Repertoire:
    rep = Repertoire(side=chess.WHITE)
    rep.branch_from(rep.root_node, ["e4", "e5", "Nf3"])
    return rep


def test_player_move_frequencies_calls_core_and_maps_counts(
    monkeypatch: pytest.MonkeyPatch,
):
    rep = _build_repertoire()
    seen: dict[str, list[SimpleNamespace]] = {}

    def fake_player_move_analysis(payload):
        seen["payload"] = payload
        return (
            [
                {"piece": "P", "from_square": "e2", "to_square": "e4", "frequency": 3},
                {"piece": "N", "from_square": "g1", "to_square": "f3", "frequency": 2},
            ],
            {},
        )

    monkeypatch.setattr(
        repertoire_analysis, "_PLAYER_RANKING_CACHE", weakref.WeakKeyDictionary()
    )
    monkeypatch.setattr(
        repertoire_analysis._core, "player_move_analysis", fake_player_move_analysis
    )

    frequencies = player_move_frequencies(rep)

    payload: list[SimpleNamespace] = seen["payload"]
    payload_snapshot = sorted((item.fen, tuple(sorted(item.moves))) for item in payload)
    after_e5 = next(
        node
        for node in rep.nodes_by_fen.values()
        if node.move and node.move.uci() == "e7e5"
    )

    expected_snapshot = sorted(
        [
            (rep.root_node.fen, ("e2e4",)),
            (after_e5.fen, ("g1f3",)),
        ]
    )

    assert payload_snapshot == expected_snapshot

    assert frequencies == {
        MoveFingerprint(piece="P", from_square="e2", to_square="e4"): 3,
        MoveFingerprint(piece="N", from_square="g1", to_square="f3"): 2,
    }


def test_player_move_rankings_uses_cached_rankings(monkeypatch: pytest.MonkeyPatch):
    rep = _build_repertoire()
    ranking_payload = {rep.root_node.fen: [{"uci": "e2e4", "san": "e4"}]}
    freq_payload = [
        {"piece": "P", "from_square": "e2", "to_square": "e4", "frequency": 5},
    ]

    call_count = 0

    def fake_player_move_analysis(payload):
        nonlocal call_count
        call_count += 1
        return freq_payload, ranking_payload

    monkeypatch.setattr(
        repertoire_analysis, "_PLAYER_RANKING_CACHE", weakref.WeakKeyDictionary()
    )
    monkeypatch.setattr(
        repertoire_analysis._core, "player_move_analysis", fake_player_move_analysis
    )

    frequencies = player_move_frequencies(rep)
    rankings = player_move_rankings(rep, frequencies=frequencies)

    assert call_count == 1
    child = rep.root_node.children["e2e4"]
    after_e5 = next(
        node
        for node in rep.nodes_by_fen.values()
        if node.move and node.move.uci() == "e7e5"
    )
    assert rankings == {
        rep.root_node.fen: [
            {
                "move": chess.Move.from_uci("e2e4"),
                "uci": "e2e4",
                "san": "e4",
                "frequency": 5,
                "child": child,
            },
        ],
        after_e5.fen: [],
    }

    def fail_player_move_analysis(_):
        raise AssertionError("should use cached rankings")

    monkeypatch.setattr(
        repertoire_analysis._core, "player_move_analysis", fail_player_move_analysis
    )
    cached_rankings = player_move_rankings(rep, frequencies=frequencies)
    assert cached_rankings == rankings
    assert call_count == 1


def test_player_move_analysis_payload_clears_cache_when_no_player_nodes(
    monkeypatch: pytest.MonkeyPatch,
):
    rep = Repertoire(side=chess.BLACK)

    monkeypatch.setattr(
        repertoire_analysis, "_PLAYER_RANKING_CACHE", weakref.WeakKeyDictionary()
    )
    repertoire_analysis._PLAYER_RANKING_CACHE[rep] = {"stale": []}

    def fail_player_move_analysis(_):
        raise AssertionError("should not call core when no player nodes")

    monkeypatch.setattr(
        repertoire_analysis._core, "player_move_analysis", fail_player_move_analysis
    )

    frequencies = player_move_frequencies(rep)

    assert frequencies == {}
    assert rep not in repertoire_analysis._PLAYER_RANKING_CACHE
