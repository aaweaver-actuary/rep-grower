from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Dict

import chess
import chess.pgn as chess_pgn

from .repertoire import Repertoire, RepertoireNode


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


class RepertoirePruner:
    def __init__(self, repertoire: Repertoire):
        self.repertoire = repertoire
        self._pgn_node_map: dict[int, RepertoireNode] | None = None

    def player_move_frequencies(self) -> Dict[MoveFingerprint, int]:
        counts: Dict[MoveFingerprint, int] = defaultdict(int)
        for parent in self.repertoire.nodes_by_fen.values():
            board = chess.Board(parent.fen)
            if board.turn != self.repertoire.side:
                continue
            for move_uci in parent.children.keys():
                move = chess.Move.from_uci(move_uci)
                fingerprint = MoveFingerprint.from_move(board, move)
                counts[fingerprint] += 1
        return dict(counts)

    def nodes_by_player(self) -> list[RepertoireNode]:
        return [
            node
            for node in self.repertoire.nodes_by_fen.values()
            if chess.Board(node.fen).turn == self.repertoire.side
        ]

    def player_move_rankings(
        self,
        frequencies: Dict[MoveFingerprint, int] | None = None,
    ) -> dict[str, list[dict]]:
        frequencies = frequencies or self.player_move_frequencies()
        rankings: dict[str, list[dict]] = {}
        for node in self.nodes_by_player():
            board = chess.Board(node.fen)
            ranked: list[dict] = []
            for move_uci, child in node.children.items():
                move = chess.Move.from_uci(move_uci)
                fingerprint = MoveFingerprint.from_move(board, move)
                freq = frequencies.get(fingerprint, 0)
                ranked.append(
                    {
                        "move": move,
                        "uci": move_uci,
                        "san": board.san(move),
                        "frequency": freq,
                        "child": child,
                    }
                )
            ranked.sort(key=lambda item: (-item["frequency"], item["san"]))
            rankings[node.fen] = ranked
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
