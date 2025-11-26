from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace
from typing import Iterable, Sequence

import chess
import chess.pgn as chess_pgn

from .repertoire import Repertoire, RepertoireNode
from . import _core


@dataclass(frozen=True)
class SplitEvent:
    """Represents a PGN game generated from a shared-prefix position."""

    node: RepertoireNode
    prefix_moves: tuple[chess.Move, ...]
    move_count: int


class RepertoireSplitter:
    """Split a repertoire graph into PGN-sized sub-games."""

    def __init__(self, repertoire: Repertoire):
        self.repertoire = repertoire

    def split_events(self, max_moves: int = 1000) -> list[SplitEvent]:
        """Return split events where each subtree stays within ``max_moves``."""

        max_moves = max(1, int(max_moves))
        payload = self._split_payload()
        raw_events = _core.split_repertoire_nodes(
            self.repertoire.root_node.fen,
            payload,
            max_moves,
        )
        events: list[SplitEvent] = []
        for fen, prefix_uci, move_count in raw_events:
            node = self.repertoire.nodes_by_fen.get(fen)
            if node is None:
                continue
            moves = tuple(chess.Move.from_uci(uci) for uci in prefix_uci)
            events.append(
                SplitEvent(
                    node=node,
                    prefix_moves=moves,
                    move_count=int(move_count),
                )
            )
        return events

    def build_game(
        self,
        event: SplitEvent,
        *,
        event_index: int = 1,
        event_name: str | None = None,
    ) -> chess_pgn.Game:
        """Construct a PGN Game for the given split event."""

        root_board = chess.Board(self.repertoire.root_node.fen)
        board = root_board.copy(stack=False)
        game = chess_pgn.Game()
        game.setup(board.copy(stack=False))

        prefix_node: chess_pgn.GameNode = game
        for move in event.prefix_moves:
            if move not in board.legal_moves:
                raise ValueError(
                    f"Illegal prefix move {move.uci()} for split event starting at {self.repertoire.root_node.fen}"
                )
            board.push(move)
            prefix_node = prefix_node.add_variation(move)

        headers = {key: value for key, value in self.repertoire.game.headers.items()}
        headers.setdefault("Event", headers.get("Event", "Repertoire Split"))
        default_event = self._format_prefix(event.prefix_moves) or headers["Event"]
        headers["Event"] = event_name or default_event
        root_fen = self.repertoire.root_node.fen
        if root_fen == chess.STARTING_FEN:
            headers.pop("SetUp", None)
            headers.pop("FEN", None)
        else:
            headers["SetUp"] = "1"
            headers["FEN"] = root_fen
        round_header = headers.get("Round")
        if not round_header or round_header == "?":
            headers["Round"] = str(event_index)

        for key, value in headers.items():
            game.headers[key] = value
        if root_fen == chess.STARTING_FEN:
            game.headers.pop("SetUp", None)
            game.headers.pop("FEN", None)

        visited = {event.node.fen}
        self._apply_node_metadata(prefix_node, event.node)
        self._copy_subtree(prefix_node, event.node, board, visited)
        return game

    def write_events(
        self,
        events: Sequence[SplitEvent],
        *,
        output_path: str,
        event_names: Sequence[str | None] | None = None,
    ) -> None:
        """Write all split events into a multi-game PGN file."""

        with open(output_path, "w", encoding="utf-8") as handle:
            for idx, event in enumerate(events, start=1):
                name = None
                if event_names is not None and idx - 1 < len(event_names):
                    name = event_names[idx - 1]
                game = self.build_game(event, event_index=idx, event_name=name)
                exporter = chess_pgn.FileExporter(handle)
                game.accept(exporter)
                handle.write("\n\n")

    def _split_payload(self) -> list[SimpleNamespace]:
        payload: list[SimpleNamespace] = []
        for node in self.repertoire.nodes_by_fen.values():
            children = [
                SimpleNamespace(uci=move_uci, fen=child.fen)
                for move_uci, child in node.children.items()
            ]
            payload.append(SimpleNamespace(fen=node.fen, children=children))
        return payload

    def _copy_subtree(
        self,
        target_node: chess_pgn.GameNode,
        source_node: RepertoireNode,
        board: chess.Board,
        visited: set[str],
    ) -> None:
        children = self._sorted_children(source_node)
        for move_uci, child in children:
            if child.fen in visited:
                continue
            move = chess.Move.from_uci(move_uci)
            if move not in board.legal_moves:
                continue
            board.push(move)
            visited.add(child.fen)
            new_child = target_node.add_variation(move)
            self._apply_node_metadata(new_child, child)
            self._copy_subtree(new_child, child, board, visited)
            visited.remove(child.fen)
            board.pop()

    def _sorted_children(
        self, node: RepertoireNode
    ) -> list[tuple[str, RepertoireNode]]:
        board = chess.Board(node.fen)
        decorated: list[tuple[str, str, RepertoireNode]] = []
        for move_uci, child in node.children.items():
            move = chess.Move.from_uci(move_uci)
            try:
                san = board.san(move)
            except ValueError:
                san = move_uci
            decorated.append((san, move_uci, child))
        decorated.sort(key=lambda item: item[0])
        return [(move_uci, child) for _, move_uci, child in decorated]

    def _apply_node_metadata(
        self, target_node: chess_pgn.GameNode, source_node: RepertoireNode
    ) -> None:
        if not source_node.pgn_nodes:
            return
        source_pgn = source_node.pgn_nodes[0]
        target_node.comment = source_pgn.comment
        target_node.nags = set(source_pgn.nags)

    def _format_prefix(self, moves: Iterable[chess.Move]) -> str:
        sequence = list(moves)
        if not sequence:
            return self.repertoire.game.headers.get("Event", "Start Position")
        board = chess.Board(self.repertoire.root_node.fen)
        tokens = self._tokenize_moves(board, sequence)
        return " ".join(tokens)

    def _tokenize_moves(
        self, board: chess.Board, moves: Iterable[chess.Move]
    ) -> list[str]:
        tokens: list[str] = []
        for move in moves:
            san = board.san(move)
            if board.turn == chess.WHITE:
                tokens.append(f"{board.fullmove_number}.{san}")
            else:
                if tokens and tokens[-1].startswith(f"{board.fullmove_number}."):
                    tokens.append(san)
                else:
                    tokens.append(f"{board.fullmove_number}...{san}")
            board.push(move)
        return tokens

    def _shared_prefix_moves(self, events: Sequence[SplitEvent]) -> list[chess.Move]:
        sequences = [list(event.prefix_moves) for event in events if event.prefix_moves]
        if not sequences:
            return []
        prefix = sequences[0][:]
        for seq in sequences[1:]:
            limit = min(len(prefix), len(seq))
            idx = 0
            while idx < limit and prefix[idx] == seq[idx]:
                idx += 1
            prefix = prefix[:idx]
            if not prefix:
                break
        return prefix

    def compact_event_names(self, events: Sequence[SplitEvent]) -> list[str | None]:
        shared = self._shared_prefix_moves(events)
        if not shared:
            return [None] * len(events)
        names: list[str | None] = []
        for event in events:
            if len(event.prefix_moves) <= len(shared):
                names.append(None)
                continue
            board = chess.Board(self.repertoire.root_node.fen)
            for move in shared:
                board.push(move)
            suffix_moves = event.prefix_moves[len(shared) :]
            tokens = self._tokenize_moves(board, suffix_moves)
            if not tokens:
                names.append(None)
            else:
                names.append(" ".join(tokens))
        return names
