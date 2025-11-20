from __future__ import annotations

import asyncio
import chess
import chess.pgn as chess_pgn

from dataclasses import dataclass, field
from typing import Iterable

from .stockfish_analysis_api import StockfishAnalysisApi


def nodes_from_root(rep: Repertoire, node: RepertoireNode) -> int:
    """Return the number of moves from the root to the given node."""
    count = 0
    current = node
    while not current.is_root:
        if not current.parents:
            break
        parent_fen = next(iter(current.parents))
        current = rep.nodes_by_fen[parent_fen]
        count += 1
    return count


@dataclass
class RepertoireNode:
    fen: str
    move: chess.Move | None = None
    parents: set[str] = field(default_factory=set)
    children: dict[str, RepertoireNode] = field(default_factory=dict)
    pgn_nodes: list[chess_pgn.GameNode] = field(default_factory=list)

    def add_parent(self, parent_fen: str):
        self.parents.add(parent_fen)

    def add_child(self, move: chess.Move, child: RepertoireNode):
        self.children[move.uci()] = child

    @property
    def is_root(self) -> bool:
        return len(self.parents) == 0

    @property
    def is_leaf(self) -> bool:
        return len(self.children) == 0


@dataclass
class Repertoire:
    side: chess.Color
    initial_san: str = ""
    moves: list[str] = field(default_factory=list)
    board: chess.Board = field(default_factory=chess.Board)
    game: chess_pgn.Game = field(default_factory=chess_pgn.Game)

    def __post_init__(self):
        if not isinstance(self.initial_san, str):
            self.initial_san = ""
        self.game.setup(self.board)
        root = RepertoireNode(fen=self.board.fen(), move=None, pgn_nodes=[self.game])
        self.nodes_by_fen: dict[str, RepertoireNode] = {root.fen: root}
        self.root_node = root
        self.current_node = root

    @classmethod
    def from_str(cls, side: str, initial_san: str):
        color = chess.WHITE if side.lower() == "white" else chess.BLACK
        return cls(side=color, initial_san=initial_san)

    @property
    def fen(self) -> str:
        """Return the FEN of the current board position."""
        return self.board.fen()

    @property
    def turn(self) -> chess.Color:
        """Return the side to move."""
        return self.board.turn

    @property
    def is_player_turn(self) -> bool:
        """Return True if it's the repertoire side's turn to move."""
        return self.board.turn == self.side

    @property
    def pgn(self) -> str:
        """Return the PGN of the repertoire game."""
        return str(self.game)

    def play_initial_moves(self):
        """Play the initial moves up to the repertoire position."""
        pgn_node = self.game
        node = self.root_node
        for san in self.initial_san.split():
            self.moves.append(san)
            move = self.board.parse_san(san)
            self.board.push(move)
            node, pgn_node = self._link_child(node, move, self.board.fen(), pgn_node)
        self.current_node = node

    @property
    def root_nodes(self) -> list[RepertoireNode]:
        """Return all root nodes in the repertoire."""
        return [node for node in self.nodes_by_fen.values() if node.is_root]

    @property
    def leaf_nodes(self) -> list[RepertoireNode]:
        """Return all leaf nodes in the repertoire."""
        return [node for node in self.nodes_by_fen.values() if node.is_leaf]

    async def get_engine_moves(self, node: RepertoireNode | None = None):
        target_node = node or self.current_node
        api = StockfishAnalysisApi(target_node.fen, multi_pv=10)
        await api.raw_evaluation()
        return api.best_moves

    def branch_from(
        self, node: RepertoireNode, san_moves: Iterable[str]
    ) -> RepertoireNode:
        board = chess.Board(node.fen)
        pgn_node = self._pgn_node_for(node)
        current = node
        for san in san_moves:
            move = board.parse_san(san)
            board.push(move)
            current, pgn_node = self._link_child(current, move, board.fen(), pgn_node)
        return current

    def _pgn_node_for(self, node: RepertoireNode) -> chess_pgn.GameNode:
        if not node.pgn_nodes:
            raise ValueError(f"No PGN node recorded for FEN {node.fen}")
        return node.pgn_nodes[0]

    def _ensure_pgn_variation(
        self, parent: chess_pgn.GameNode, move: chess.Move
    ) -> chess_pgn.GameNode:
        for variation in parent.variations:
            if variation.move == move:
                return variation
        return parent.add_variation(move)

    def _link_child(
        self,
        parent: RepertoireNode,
        move: chess.Move,
        child_fen: str,
        pgn_node: chess_pgn.GameNode,
    ) -> tuple[RepertoireNode, chess_pgn.GameNode]:
        child = self.nodes_by_fen.get(child_fen)
        if child is None:
            child = RepertoireNode(fen=child_fen, move=move)
            self.nodes_by_fen[child_fen] = child
        child.add_parent(parent.fen)
        parent.add_child(move, child)
        child_pgn_node = self._ensure_pgn_variation(pgn_node, move)
        if child_pgn_node not in child.pgn_nodes:
            child.pgn_nodes.append(child_pgn_node)
        return child, child_pgn_node

    def _mainline_node(self, node: RepertoireNode | None = None) -> chess_pgn.GameNode:
        return self._pgn_node_for(node or self.current_node)

    async def add_engine_variations_for_node(
        self,
        node: RepertoireNode | None = None,
        multi_pv: int | None = None,
        graph_lock: asyncio.Lock | None = None,
    ) -> list[str]:
        """Attach engine candidate moves at the given node (default current) as PGN variations."""

        target_node = node or self.current_node
        api = StockfishAnalysisApi(target_node.fen, multi_pv=multi_pv or 10)
        await api.raw_evaluation()

        base_board = chess.Board(target_node.fen)
        pgn_node = self._pgn_node_for(target_node)
        existing_moves = {var.move for var in pgn_node.variations}
        added_moves: list[str] = []

        for uci_move in api.best_moves:
            try:
                move = chess.Move.from_uci(uci_move)
            except ValueError:
                continue
            if move in existing_moves:
                continue
            board_copy = base_board.copy()
            if move not in board_copy.legal_moves:
                continue
            board_copy.push(move)
            if graph_lock is not None:
                async with graph_lock:
                    self._link_child(target_node, move, board_copy.fen(), pgn_node)
                    existing_moves.add(move)
                    added_moves.append(uci_move)
            else:
                self._link_child(target_node, move, board_copy.fen(), pgn_node)
                existing_moves.add(move)
                added_moves.append(uci_move)

        return added_moves

    async def add_engine_variations(
        self,
        nodes: Iterable[RepertoireNode] | None = None,
        multi_pv: int | None = None,
        max_concurrency: int | None = None,
    ) -> dict[str, list[str]]:
        """Expand all given (or leaf) nodes in parallel using a worker queue."""

        targets = list(nodes or self.leaf_nodes)
        if not targets:
            return {}

        max_concurrency = max_concurrency or min(4, len(targets))
        graph_lock = asyncio.Lock()
        queue: asyncio.Queue[RepertoireNode] = asyncio.Queue()
        for node in targets:
            queue.put_nowait(node)

        results: dict[str, list[str]] = {}

        async def worker() -> None:
            while True:
                try:
                    node = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    moves = await self.add_engine_variations_for_node(
                        node=node,
                        multi_pv=multi_pv,
                        graph_lock=graph_lock,
                    )
                    results[node.fen] = moves
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max_concurrency)]
        await queue.join()
        await asyncio.gather(*workers, return_exceptions=True)
        return results
