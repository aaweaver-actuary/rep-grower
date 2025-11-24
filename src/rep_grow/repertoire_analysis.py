from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, TYPE_CHECKING
from types import SimpleNamespace
import weakref

import chess

if TYPE_CHECKING:  # pragma: no cover
    from .repertoire import Repertoire, RepertoireNode

from . import _core


@dataclass(frozen=True)
class MoveFingerprint:
    piece: str
    from_square: str
    to_square: str

    @classmethod
    def from_move(cls, board: chess.Board, move: chess.Move) -> MoveFingerprint:
        piece = board.piece_at(move.from_square)
        if piece is None:  # pragma: no cover - defensive guard
            raise ValueError(
                "Move lacks originating piece; repertoire may be inconsistent"
            )
        return cls(
            piece=piece.symbol().upper(),
            from_square=chess.square_name(move.from_square),
            to_square=chess.square_name(move.to_square),
        )


def player_nodes(repertoire: Repertoire) -> list[RepertoireNode]:
    return [
        node
        for node in repertoire.nodes_by_fen.values()
        if chess.Board(node.fen).turn == repertoire.side
    ]


_PLAYER_RANKING_CACHE: weakref.WeakKeyDictionary[
    Repertoire, dict[str, list[dict[str, Any]]]
] = weakref.WeakKeyDictionary()


def _player_node_payload(repertoire: Repertoire) -> list[SimpleNamespace]:
    payload: list[SimpleNamespace] = []
    for node in player_nodes(repertoire):
        payload.append(SimpleNamespace(fen=node.fen, moves=list(node.children.keys())))
    return payload


def _player_move_analysis_payload(
    repertoire: Repertoire,
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    nodes_payload = _player_node_payload(repertoire)
    if not nodes_payload:
        try:
            del _PLAYER_RANKING_CACHE[repertoire]
        except KeyError:
            pass
        return [], {}
    freq_payload, ranking_payload = _core.player_move_analysis(nodes_payload)
    _PLAYER_RANKING_CACHE[repertoire] = ranking_payload
    return freq_payload, ranking_payload


def _build_frequency_map(payload: list[dict[str, Any]]) -> Dict[MoveFingerprint, int]:
    counts: Dict[MoveFingerprint, int] = {}
    for entry in payload:
        fingerprint = MoveFingerprint(
            piece=str(entry["piece"]),
            from_square=str(entry["from_square"]),
            to_square=str(entry["to_square"]),
        )
        counts[fingerprint] = int(entry["frequency"])
    return counts


def player_move_frequencies(repertoire: Repertoire) -> Dict[MoveFingerprint, int]:
    freq_payload, _ = _player_move_analysis_payload(repertoire)
    return _build_frequency_map(freq_payload)


def player_move_rankings(
    repertoire: Repertoire,
    *,
    frequencies: Dict[MoveFingerprint, int] | None = None,
) -> dict[str, List[dict]]:
    ranking_payload = _PLAYER_RANKING_CACHE.get(repertoire)
    if frequencies is None or ranking_payload is None:
        freq_payload, fresh_rankings = _player_move_analysis_payload(repertoire)
        ranking_payload = fresh_rankings
        if frequencies is None:
            frequencies = _build_frequency_map(freq_payload)
    assert ranking_payload is not None
    assert frequencies is not None

    rankings: dict[str, list[dict]] = {}
    for node in player_nodes(repertoire):
        node_payload = ranking_payload.get(node.fen, [])
        ranked: list[dict] = []
        board = chess.Board(node.fen)
        for entry in node_payload:
            move_uci = str(entry["uci"])
            move = chess.Move.from_uci(move_uci)
            fingerprint = MoveFingerprint.from_move(board, move)
            child = node.children.get(move_uci)
            freq = frequencies.get(fingerprint, 0)
            ranked.append(
                {
                    "move": move,
                    "uci": move_uci,
                    "san": str(entry["san"]),
                    "frequency": freq,
                    "child": child,
                }
            )
        ranked.sort(key=lambda item: (-item["frequency"], item["san"]))
        rankings[node.fen] = ranked
    return rankings
