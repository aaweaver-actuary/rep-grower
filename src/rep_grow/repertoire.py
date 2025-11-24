from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import chess
import chess.pgn as chess_pgn

from dataclasses import dataclass, field
from typing import Iterable

import httpx

from .stockfish_analysis_api import StockfishAnalysisApi
from .lichess_explorer_api import LichessExplorerApi
from .repertoire_analysis import player_move_rankings as _player_move_rankings


logger = logging.getLogger(__name__)


def nodes_from_root(rep: Repertoire, node: RepertoireNode) -> int:
    """Return the number of moves from the root to the given node."""
    count = 0
    current = node
    visited: set[str] = set()
    while not current.is_root:
        if not current.parents:
            break
        parent_fen = next(iter(current.parents))
        if parent_fen in visited:
            break
        visited.add(parent_fen)
        parent = rep.nodes_by_fen.get(parent_fen)
        if parent is None:
            break
        current = parent
        count += 1
    return count


def _normalized_board_fen(board: chess.Board) -> str:
    clone = board.copy(stack=False)
    clone.halfmove_clock = 0
    clone.fullmove_number = 1
    return clone.fen()


def canonical_fen(fen: str) -> str:
    board = chess.Board(fen)
    return _normalized_board_fen(board)


def _env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


class ExplorerRateLimiter:
    """Simple concurrency gate with a minimum delay between Explorer calls."""

    def __init__(self, max_concurrent: int = 1, min_delay: float = 1.0):
        self._max_concurrent = max(1, max_concurrent)
        self._min_delay = max(0.0, min_delay)
        self._semaphore: asyncio.Semaphore | None = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def __aenter__(self):
        loop = asyncio.get_running_loop()
        if self._semaphore is None or loop is not self._loop:
            self._semaphore = asyncio.Semaphore(self._max_concurrent)
            self._loop = loop
        await self._semaphore.acquire()
        return self

    async def __aexit__(self, exc_type, exc, tb):
        try:
            if self._min_delay:
                await asyncio.sleep(self._min_delay)
        finally:
            assert self._semaphore is not None
            self._semaphore.release()


@dataclass
class RepertoireConfig:
    stockfish_multi_pv: int = 10
    stockfish_depth: int = 20
    stockfish_think_time: float | None = None
    stockfish_engine_path: str | Path | None = "/opt/homebrew/bin/stockfish"
    stockfish_best_score_threshold: int = 20
    explorer_pct: float = 90.0
    explorer_max_moves: int | None = None
    explorer_min_game_share: float = 0.05


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
    config: RepertoireConfig = field(default_factory=RepertoireConfig)

    def __post_init__(self):
        if not isinstance(self.initial_san, str):
            self.initial_san = ""
        if self.config is None:
            self.config = RepertoireConfig()
        self.game.setup(self.board)
        root_fen = _normalized_board_fen(self.board)
        root = RepertoireNode(fen=root_fen, move=None, pgn_nodes=[self.game])
        self.nodes_by_fen: dict[str, RepertoireNode] = {root_fen: root}
        self.root_node = root
        self.current_node = root
        self._explorer_rate_limiter: ExplorerRateLimiter | None = None

    @classmethod
    def from_str(
        cls,
        side: str,
        initial_san: str,
        *,
        config: RepertoireConfig | None = None,
    ):
        color = chess.WHITE if side.lower() == "white" else chess.BLACK
        return cls(
            side=color, initial_san=initial_san, config=config or RepertoireConfig()
        )

    @classmethod
    def from_pgn_file(
        cls,
        side: chess.Color,
        pgn_path: str | Path,
        *,
        config: RepertoireConfig | None = None,
    ) -> Repertoire:
        path = Path(pgn_path)
        with open(path, "r", encoding="utf-8") as handle:
            game = chess_pgn.read_game(handle)
        if game is None:
            raise ValueError(f"No PGN game found in {path}")

        root_board = game.board()
        board_for_san = root_board.copy(stack=False)
        mainline_san: list[str] = []
        for move in game.mainline_moves():
            mainline_san.append(board_for_san.san(move))
            board_for_san.push(move)

        rep = cls(
            side=side,
            initial_san=" ".join(mainline_san),
            board=root_board.copy(stack=False),
            game=game,
            config=config or RepertoireConfig(),
        )
        rep.play_initial_moves()
        rep._ingest_pgn_tree(game, rep.root_node, root_board.copy(stack=False))
        return rep

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

    def player_move_rankings(self) -> dict[str, list[dict[str, object]]]:
        """Return the sorted move frequency map for every player decision."""

        rankings = _player_move_rankings(self)
        result: dict[str, list[dict[str, object]]] = {}
        for fen, entries in rankings.items():
            result[fen] = [
                {
                    "uci": entry["uci"],
                    "san": entry["san"],
                    "frequency": entry["frequency"],
                }
                for entry in entries
            ]
        return result

    async def get_engine_moves(self, node: RepertoireNode | None = None):
        target_node = node or self.current_node
        api = self._stockfish_api(target_node.fen)
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
        canonical_child_fen = canonical_fen(child_fen)
        child = self.nodes_by_fen.get(canonical_child_fen)
        if child is None:
            child = RepertoireNode(fen=canonical_child_fen, move=move)
            self.nodes_by_fen[canonical_child_fen] = child
        if child is not self.root_node:
            child.add_parent(parent.fen)
        parent.add_child(move, child)
        parent_pgn_nodes = parent.pgn_nodes or [pgn_node]
        for parent_variation in parent_pgn_nodes:
            variation_node = self._ensure_pgn_variation(parent_variation, move)
            if variation_node not in child.pgn_nodes:
                child.pgn_nodes.append(variation_node)
        child_pgn_node = self._ensure_pgn_variation(pgn_node, move)
        if child_pgn_node not in child.pgn_nodes:
            child.pgn_nodes.append(child_pgn_node)
        return child, child_pgn_node

    def _ingest_pgn_tree(
        self,
        pgn_node: chess_pgn.GameNode,
        rep_node: RepertoireNode,
        board: chess.Board,
    ) -> None:
        for variation in pgn_node.variations:
            move = variation.move
            if move is None:
                continue
            board.push(move)
            child, _ = self._link_child(rep_node, move, board.fen(), pgn_node)
            self._ingest_pgn_tree(variation, child, board)
            board.pop()

    def _mainline_node(self, node: RepertoireNode | None = None) -> chess_pgn.GameNode:
        return self._pgn_node_for(node or self.current_node)

    def _get_explorer_limiter(self) -> ExplorerRateLimiter:
        if self._explorer_rate_limiter is None:
            concurrency = max(1, _env_int("REP_GROW_EXPLORER_MAX_CONCURRENCY", 1))
            delay = max(0.0, _env_float("REP_GROW_EXPLORER_MIN_DELAY", 1.0))
            self._explorer_rate_limiter = ExplorerRateLimiter(
                max_concurrent=concurrency,
                min_delay=delay,
            )
        return self._explorer_rate_limiter

    def _stockfish_api(
        self,
        fen: str,
        *,
        multi_pv: int | None = None,
        depth: int | None = None,
    ) -> StockfishAnalysisApi:
        return StockfishAnalysisApi(
            fen,
            multi_pv=multi_pv or self.config.stockfish_multi_pv,
            engine_path=self.config.stockfish_engine_path,
            depth=depth or self.config.stockfish_depth,
            think_time=self.config.stockfish_think_time,
            best_score_threshold=self.config.stockfish_best_score_threshold,
        )

    def _resolve_pct(self, pct: float | None) -> float:
        return pct if pct is not None else self.config.explorer_pct

    async def add_engine_variations_for_node(
        self,
        node: RepertoireNode | None = None,
        multi_pv: int | None = None,
        graph_lock: asyncio.Lock | None = None,
    ) -> list[str]:
        """Attach engine candidate moves at the given node (default current) as PGN variations."""

        target_node = node or self.current_node
        api = self._stockfish_api(target_node.fen, multi_pv=multi_pv)
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

    async def add_explorer_variations_for_node(
        self,
        node: RepertoireNode | None = None,
        pct: float | None = None,
        graph_lock: asyncio.Lock | None = None,
    ) -> list[str]:
        """Attach Lichess Explorer moves covering pct% of games at the node."""
        target_node = node or self.current_node
        api = LichessExplorerApi(fen=target_node.fen)
        limiter = self._get_explorer_limiter()
        async with limiter:
            await api.raw_explorer()
        target_pct = self._resolve_pct(pct)
        moves = api.top_p_pct_moves(
            pct=target_pct,
            max_moves=self.config.explorer_max_moves,
            min_game_share=self.config.explorer_min_game_share,
        )

        pgn_node = self._pgn_node_for(target_node)
        existing_moves = {var.move for var in pgn_node.variations}
        added_moves: list[str] = []

        for entry in moves:
            san_move = entry.get("move")
            if not san_move:
                continue
            board_copy = chess.Board(target_node.fen)
            try:
                move = board_copy.parse_san(san_move)
            except ValueError:
                continue
            if move in existing_moves:
                continue
            board_copy.push(move)
            if graph_lock:
                async with graph_lock:
                    self._link_child(target_node, move, board_copy.fen(), pgn_node)
            else:
                self._link_child(target_node, move, board_copy.fen(), pgn_node)
            existing_moves.add(move)
            added_moves.append(san_move)

        return added_moves

    async def add_explorer_variations(
        self,
        nodes: Iterable[RepertoireNode] | None = None,
        pct: float | None = None,
        max_concurrency: int | None = None,
    ) -> dict[str, list[str]]:
        """Expand nodes by fetching explorer moves via a worker queue."""
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
                    moves = await self.add_explorer_variations_for_node(
                        node=node,
                        pct=pct,
                        graph_lock=graph_lock,
                    )
                    results[node.fen] = moves
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    if isinstance(exc, httpx.HTTPStatusError):
                        status = (
                            exc.response.status_code
                            if exc.response is not None
                            else "?"
                        )
                        logger.warning(
                            "Explorer API error (%s) while expanding %s: %s",
                            status,
                            node.fen,
                            exc,
                        )
                    else:
                        logger.warning(
                            "Explorer expansion failed for %s: %s", node.fen, exc
                        )
                    results[node.fen] = []
                finally:
                    queue.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(max_concurrency)]
        await queue.join()
        await asyncio.gather(*workers, return_exceptions=True)
        return results

    async def expand_leaves_by_turn(
        self,
        *,
        multi_pv: int | None = None,
        pct: float | None = None,
        max_concurrency: int | None = None,
    ) -> dict[str, list[str]]:
        """Expand leaf nodes, routing player turns to the engine and others to explorer."""

        player_nodes: list[RepertoireNode] = []
        opponent_nodes: list[RepertoireNode] = []

        for node in self.leaf_nodes:
            board = chess.Board(node.fen)
            if board.turn == self.side:
                player_nodes.append(node)
            else:
                opponent_nodes.append(node)

        results: dict[str, list[str]] = {}

        if player_nodes:
            engine_results = await self.add_engine_variations(
                nodes=player_nodes,
                multi_pv=multi_pv,
                max_concurrency=max_concurrency,
            )
            results.update(engine_results)

        if opponent_nodes:
            explorer_results = await self.add_explorer_variations(
                nodes=opponent_nodes,
                pct=pct,
                max_concurrency=max_concurrency,
            )
            results.update(explorer_results)

        return results

    def export_pgn(self, filepath: str) -> None:
        """Export the repertoire PGN to the given file."""
        with open(filepath, "w", encoding="utf-8") as f:
            exporter = chess_pgn.FileExporter(f)
            self.game.accept(exporter)

    def __hash__(self) -> int:
        # Allow usage in weak key caches that rely on object identity.
        return object.__hash__(self)
