from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable

import chess
import chess.pgn as chess_pgn

from .repertoire import Repertoire, RepertoireNode
from .repertoire_analysis import (
    MoveFingerprint,
    player_move_frequencies as _player_move_frequencies,
    player_move_rankings as _player_move_rankings,
)


@dataclass(frozen=True)
class PreferenceSet:
    labels: set[str]

    @classmethod
    def from_iterable(cls, preferred_moves: Iterable[str] | None) -> "PreferenceSet":
        labels: set[str] = set()
        if preferred_moves:
            for raw in preferred_moves:
                if raw is None:
                    continue
                normalized = cls._normalize_label(raw)
                if normalized:
                    labels.add(normalized)
        return cls(labels=labels)

    @staticmethod
    def _normalize_label(value: str) -> str:
        trimmed = value.strip()
        if not trimmed:
            return ""
        trimmed = trimmed.rstrip("+#!?")
        return trimmed.casefold()

    def is_preferred(self, board: chess.Board, move: chess.Move) -> bool:
        if not self.labels:
            return False
        san_key = self._normalize_label(board.san(move))
        uci_key = self._normalize_label(move.uci())
        return san_key in self.labels or uci_key in self.labels


class RepertoirePruner:
    def __init__(
        self,
        repertoire: Repertoire,
        *,
        preferred_moves: Iterable[str] | PreferenceSet | None = None,
    ):
        self.repertoire = repertoire
        self._pgn_node_map: dict[int, RepertoireNode] | None = None
        if isinstance(preferred_moves, PreferenceSet):
            self._preferences = preferred_moves
        else:
            self._preferences = PreferenceSet.from_iterable(preferred_moves)

    def player_move_frequencies(self) -> Dict[MoveFingerprint, int]:
        return _player_move_frequencies(self.repertoire)

    def player_move_rankings(
        self,
        frequencies: Dict[MoveFingerprint, int] | None = None,
    ) -> dict[str, list[dict]]:
        frequencies = frequencies or self.player_move_frequencies()
        rankings = _player_move_rankings(self.repertoire, frequencies=frequencies)
        for fen, ranked in rankings.items():
            board = chess.Board(fen)
            for entry in ranked:
                preferred = self._preferences.is_preferred(board, entry["move"])
                entry["preferred"] = preferred
            ranked.sort(
                key=lambda item: (
                    -int(item["preferred"]),
                    -item["frequency"],
                    item["san"],
                )
            )
        return rankings

    def player_move_selection(self) -> dict[str, dict]:
        selection: dict[str, dict] = {}
        rankings = self.player_move_rankings()
        for fen, ranked in rankings.items():
            if not ranked:
                continue
            selection[fen] = ranked[0]
        return selection

    def pruned_game(self) -> chess_pgn.Game:
        selection = self.player_move_selection()
        root_game = self.repertoire.game
        new_game = chess_pgn.Game()
        for key, value in root_game.headers.items():
            new_game.headers[key] = value
        new_game.setup(root_game.board())

        mapping = self._map_pgn_nodes()
        self._copy_variations(root_game, new_game, selection, mapping)
        return new_game

    def _map_pgn_nodes(self) -> dict[int, RepertoireNode]:
        if self._pgn_node_map is None:
            mapping: dict[int, RepertoireNode] = {}
            for node in self.repertoire.nodes_by_fen.values():
                for pgn_node in node.pgn_nodes:
                    mapping[id(pgn_node)] = node
            self._pgn_node_map = mapping
        return self._pgn_node_map

    def _copy_variations(
        self,
        source_node: chess_pgn.GameNode,
        target_node: chess_pgn.GameNode,
        selection: dict[str, dict],
        mapping: dict[int, RepertoireNode],
    ) -> None:
        source_rep_node = mapping.get(id(source_node))
        if source_rep_node is None:
            return
        target_node.comment = source_node.comment
        target_node.nags = set(source_node.nags)

        board = chess.Board(source_rep_node.fen)
        is_player_turn = board.turn == self.repertoire.side
        selected = selection.get(source_rep_node.fen)
        selected_uci = selected["uci"] if selected else None

        for variation in source_node.variations:
            move = variation.move
            if is_player_turn and selected_uci and move.uci() != selected_uci:
                continue
            new_child = target_node.add_variation(move)
            new_child.comment = variation.comment
            new_child.nags = set(variation.nags)
            self._copy_variations(variation, new_child, selection, mapping)
