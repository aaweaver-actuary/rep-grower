from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import chess
import chess.pgn as chess_pgn
import httpx

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Iterable

from . import _core
from .fen import canonical_fen
from .stockfish_analysis_api import StockfishAnalysisApi
from .analysis_queue import AnalysisQueue, AnalysisTask
from .lichess_analysis_api import LichessAnalysisApi
from .lichess_explorer_api import LichessExplorerApi
from .repertoire_analysis import player_move_rankings as _player_move_rankings
from .pgn_metadata import extract_reach_count, upsert_reach_count_tag


logger = logging.getLogger(__name__)


def _best_moves_from_eval_payload(
    payload: dict,
    best_score_threshold: int,
    max_pv: int,
) -> list[str]:
    pvs = payload.get("pvs") or []
    scored: list[tuple[tuple[int, int], str, int, int | None, int | None, bool]] = []

    def _first_move(entry: dict) -> str:
        moves_val = entry.get("moves") or entry.get("uci") or entry.get("move") or ""
        if isinstance(moves_val, list):
            moves_text = " ".join(str(m) for m in moves_val)
        else:
            moves_text = str(moves_val)
        return moves_text.split(" ")[0] if moves_text else ""

    for entry in pvs[: max(1, max_pv)]:
        move = _first_move(entry)
        if not move:
            continue
        mate = entry.get("mate")
        cp = entry.get("cp")
        score = cp if cp is not None else mate
        if score is None:
            continue
        score_int = int(score)
        mate_distance = abs(int(mate)) if mate is not None else None
        is_winning_mate = mate is not None and int(mate) > 0

        if is_winning_mate:
            sort_key = (2, -(mate_distance or 0))
        elif mate is not None:
            sort_key = (0, -(mate_distance or 0))
        else:
            sort_key = (1, score_int)

        scored.append(
            (
                sort_key,
                move,
                score_int,
                mate if mate is not None else None,
                mate_distance,
                is_winning_mate,
            )
        )

    scored.sort(key=lambda item: item[0], reverse=True)
    if not scored:
        return []

    top = scored[0]
    top_mate = top[3]
    top_mate_distance = top[4]
    if top_mate is not None and top_mate > 0 and top_mate_distance is not None:
        return [
            move
            for _, move, _, mate, dist, _ in scored
            if mate is not None and mate > 0 and dist == top_mate_distance
        ]

    best_score = top[2]
    return [
        move
        for _, move, score, _, _, _ in scored
        if abs(score - best_score) <= best_score_threshold
    ]


@dataclass(slots=True)
class ExpansionContext:
    targets: list[RepertoireNode]
    max_concurrency: int
    graph_lock: asyncio.Lock
    progress_callback: Callable[["RepertoireNode"], None] | None


@dataclass(slots=True, frozen=True)
class RuntimeConfig:
    explorer_max_concurrency: int = 1
    explorer_min_delay: float = 1.0

    @staticmethod
    def _parse_int(name: str, default: int) -> int:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return int(value)
        except ValueError:
            return default

    @staticmethod
    def _parse_float(name: str, default: float) -> float:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            return default

    @classmethod
    def from_env(cls) -> "RuntimeConfig":
        return cls(
            explorer_max_concurrency=cls._parse_int(
                "REP_GROW_EXPLORER_MAX_CONCURRENCY", 1
            ),
            explorer_min_delay=cls._parse_float("REP_GROW_EXPLORER_MIN_DELAY", 1.0),
        )


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


def _read_pgn_games(path: Path) -> list[chess_pgn.Game]:
    games: list[chess_pgn.Game] = []
    with open(path, "r", encoding="utf-8") as handle:
        while True:
            game = chess_pgn.read_game(handle)
            if game is None:
                break
            games.append(game)
    return games


def _merge_game_nodes(target: chess_pgn.GameNode, source: chess_pgn.GameNode) -> None:
    if source.comment:
        if not target.comment:
            target.comment = source.comment
        elif source.comment not in target.comment:
            target.comment = f"{target.comment}\n{source.comment}"
    target.nags.update(source.nags)

    for variation in source.variations:
        move = variation.move
        if move is None:
            continue
        matching_child = next(
            (child for child in target.variations if child.move == move), None
        )
        if matching_child is None:
            matching_child = target.add_variation(move)
        _merge_game_nodes(matching_child, variation)


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
    stockfish_pool_size: int | None = None
    explorer_pct: float = 90.0
    explorer_max_moves: int | None = None
    explorer_min_game_share: float = 0.05
    explorer_enabled: bool = True
    min_games_reached: int | None = None
    analysis_provider: str = "stockfish"
    analysis_providers: list[str] | None = None
    lichess_eval_path: str | None = None


@dataclass
class RepertoireNode:
    fen: str
    move: chess.Move | None = None
    parents: set[str] = field(default_factory=set)
    children: dict[str, RepertoireNode] = field(default_factory=dict)
    pgn_nodes: list[chess_pgn.GameNode] = field(default_factory=list)
    _pgn_node_ids: set[int] = field(default_factory=set, repr=False, compare=False)
    games_reached: int | None = None

    def add_parent(self, parent_fen: str):
        self.parents.add(parent_fen)

    def add_child(self, move: chess.Move, child: RepertoireNode):
        self.children[move.uci()] = child

    def add_pgn_node(self, node: chess_pgn.GameNode) -> None:
        node_id = id(node)
        if node_id in self._pgn_node_ids:
            return
        self._pgn_node_ids.add(node_id)
        self.pgn_nodes.append(node)

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
        root_fen = canonical_fen(self.board.fen())
        root = RepertoireNode(fen=root_fen, move=None)
        root.add_pgn_node(self.game)
        root_count, _ = extract_reach_count(self.game.comment)
        if root_count is not None:
            root.games_reached = root_count
        self.nodes_by_fen: dict[str, RepertoireNode] = {root_fen: root}
        self.root_node = root
        self.current_node = root
        self.runtime_config = RuntimeConfig.from_env()
        self._explorer_rate_limiter: ExplorerRateLimiter | None = None
        self._root_player_turn = chess.Board(root_fen).turn == self.side
        self._analysis_queue: AnalysisQueue | None = None
        self._analysis_order: list[str] | None = None
        self._analysis_counts: dict[str, int] = {"lichess": 0, "stockfish": 0}

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
        games = _read_pgn_games(path)
        if not games:
            raise ValueError(f"No PGN game found in {path}")
        game = games[0]
        for extra in games[1:]:
            _merge_game_nodes(game, extra)

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

    def player_move_count(self, node: RepertoireNode) -> int:
        depth = nodes_from_root(self, node)
        offset = 1 if self._root_player_turn else 0
        return (depth + offset) // 2

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
        return await self._analysis_best_moves(target_node)

    def branch_from(
        self, node: RepertoireNode, san_moves: Iterable[str]
    ) -> RepertoireNode:
        board = chess.Board(node.fen)
        pgn_node = self._pgn_node_for(node)
        current = node
        for san in san_moves:
            move = board.parse_san(san)
            board.push(move)
            parent = current
            current, pgn_node = self._link_child(current, move, board.fen(), pgn_node)
            # When a node is reached via transposition it can have multiple PGN
            # nodes; ensure each representation carries the new variation so the
            # PGN export shows shared future moves from every path.
            for alt_pgn in parent.pgn_nodes[1:]:
                alt_child_pgn = self._ensure_pgn_variation(alt_pgn, move)
                current.add_pgn_node(alt_child_pgn)
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
        child_pgn_node: chess_pgn.GameNode | None = None,
    ) -> tuple[RepertoireNode, chess_pgn.GameNode]:
        canonical_child_fen = canonical_fen(child_fen)
        child = self.nodes_by_fen.get(canonical_child_fen)
        if child is None:
            child = RepertoireNode(fen=canonical_child_fen, move=move)
            self.nodes_by_fen[canonical_child_fen] = child
        if child is not self.root_node:
            child.add_parent(parent.fen)
        parent.add_child(move, child)
        parent.add_pgn_node(pgn_node)

        resolved_child_pgn = child_pgn_node or self._ensure_pgn_variation(
            pgn_node, move
        )
        child.add_pgn_node(resolved_child_pgn)
        return child, resolved_child_pgn

    def _ingest_pgn_tree(
        self,
        pgn_node: chess_pgn.GameNode,
        rep_node: RepertoireNode,
        board: chess.Board,
    ) -> None:
        # Use an explicit stack to avoid Python recursion limits on very large repertoires.
        stack: list[tuple[chess_pgn.GameNode, RepertoireNode, chess.Board]] = [
            (pgn_node, rep_node, board.copy(stack=False))
        ]

        while stack:
            current_pgn, current_rep, current_board = stack.pop()
            # Iterate in reverse so natural order is preserved when using a stack (pre-order DFS).
            for variation in reversed(current_pgn.variations):
                move = variation.move
                if move is None:
                    continue

                next_board = current_board.copy(stack=False)
                next_board.push(move)

                child, _ = self._link_child(
                    current_rep,
                    move,
                    next_board.fen(),
                    current_pgn,
                    child_pgn_node=variation,
                )
                count, _ = extract_reach_count(variation.comment)
                if count is not None:
                    child.games_reached = count

                stack.append((variation, child, next_board))

    def _mainline_node(self, node: RepertoireNode | None = None) -> chess_pgn.GameNode:
        return self._pgn_node_for(node or self.current_node)

    def _sort_variations_by_reach(
        self, rep_node: RepertoireNode, pgn_node: chess_pgn.GameNode
    ) -> None:
        """Order PGN variations so higher reach counts lead the main line."""

        def reach_score(var: chess_pgn.GameNode) -> int:
            move = var.move
            if move is None:
                return -1
            child = rep_node.children.get(move.uci())
            if child is None:
                return -1
            return child.games_reached if child.games_reached is not None else -1

        # Stable sort to preserve original order for ties.
        pgn_node.variations.sort(key=reach_score, reverse=True)

        for variation in pgn_node.variations:
            move = variation.move
            if move is None:
                continue
            child = rep_node.children.get(move.uci())
            if child is None:
                continue
            self._sort_variations_by_reach(child, variation)

    def _get_explorer_limiter(self) -> ExplorerRateLimiter:
        if self._explorer_rate_limiter is None:
            self._explorer_rate_limiter = ExplorerRateLimiter(
                max_concurrent=self.runtime_config.explorer_max_concurrency,
                min_delay=self.runtime_config.explorer_min_delay,
            )
        return self._explorer_rate_limiter

    def _stockfish_api(
        self,
        fen: str,
        *,
        multi_pv: int | None = None,
        depth: int | None = None,
        best_score_threshold: int | None = None,
    ) -> StockfishAnalysisApi:
        return StockfishAnalysisApi(
            fen,
            multi_pv=multi_pv or self.config.stockfish_multi_pv,
            engine_path=self.config.stockfish_engine_path,
            depth=depth or self.config.stockfish_depth,
            think_time=self.config.stockfish_think_time,
            best_score_threshold=best_score_threshold
            or self.config.stockfish_best_score_threshold,
            pool_size=self.config.stockfish_pool_size,
        )

    def _lichess_api(
        self,
        fen: str,
        *,
        multi_pv: int | None = None,
        best_score_threshold: int | None = None,
    ) -> LichessAnalysisApi:
        return LichessAnalysisApi(
            fen,
            multi_pv=multi_pv or self.config.stockfish_multi_pv,
            best_score_threshold=best_score_threshold
            or self.config.stockfish_best_score_threshold,
        )

    def _analysis_provider_order(self) -> list[str]:
        if self.config.analysis_providers:
            return [name.lower() for name in self.config.analysis_providers if name]

        provider = (
            getattr(self.config, "analysis_provider", "stockfish") or "stockfish"
        ).lower()
        if provider == "hybrid":
            return ["lichess", "stockfish"]
        if provider not in {"stockfish", "lichess"}:
            logger.warning(
                "Unknown analysis_provider '%s'; defaulting to stockfish", provider
            )
            return ["stockfish"]
        return [provider]

    def _analysis_provider_fns(
        self,
    ) -> dict[str, Callable[[AnalysisTask], Awaitable[list[str]]]]:
        return {
            "lichess": self._run_lichess_provider,
            "stockfish": self._run_stockfish_provider,
        }

    def _get_analysis_queue(self) -> AnalysisQueue:
        order = self._analysis_provider_order()
        if self._analysis_queue is None or self._analysis_order != order:
            self._reset_analysis_counts()
            self._analysis_queue = AnalysisQueue(
                providers=self._analysis_provider_fns(),
                order=order,
                max_concurrency=4,
                on_success=self._increment_analysis_count,
            )
            self._analysis_order = order
        return self._analysis_queue

    def _reset_analysis_counts(self) -> None:
        self._analysis_counts = {"lichess": 0, "stockfish": 0}

    def _increment_analysis_count(self, provider: str, task: AnalysisTask) -> None:  # noqa: ARG002
        provider_key = provider.lower()
        current = self._analysis_counts.get(provider_key, 0)
        current += 1
        self._analysis_counts[provider_key] = current
        if current % 100 == 0:
            logger.info(
                "Analysis progress: lichess=%d stockfish=%d",
                self._analysis_counts.get("lichess", 0),
                self._analysis_counts.get("stockfish", 0),
            )

    async def _run_lichess_provider(self, task: AnalysisTask) -> list[str]:
        api = self._lichess_api(
            task.fen,
            multi_pv=task.multi_pv,
            best_score_threshold=task.best_score_threshold,
        )
        await api.raw_evaluation()
        return api.best_moves

    async def _run_stockfish_provider(self, task: AnalysisTask) -> list[str]:
        api = self._stockfish_api(
            task.fen,
            multi_pv=task.multi_pv,
            depth=task.depth,
            best_score_threshold=task.best_score_threshold,
        )
        await api.raw_evaluation()
        return api.best_moves

    async def _analysis_best_moves(
        self,
        target_node: RepertoireNode,
        *,
        multi_pv: int | None = None,
        depth: int | None = None,
    ) -> list[str]:
        task = AnalysisTask(
            fen=target_node.fen,
            multi_pv=multi_pv or self.config.stockfish_multi_pv,
            depth=depth or self.config.stockfish_depth,
            best_score_threshold=self.config.stockfish_best_score_threshold,
        )
        queue = self._get_analysis_queue()
        return await queue.evaluate(task)

    def _static_lichess_eval_best_moves(self, fens: list[str]) -> dict[str, list[str]]:
        path = getattr(self.config, "lichess_eval_path", None)
        if not path:
            return {}

        try:
            results = _core.lookup_lichess_evals(path, fens)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Static Lichess eval lookup failed for %d positions: %s",
                len(fens),
                exc,
            )
            return {}

        hits: dict[str, list[str]] = {}
        for fen, payload in zip(fens, results):
            if payload is None:
                continue
            try:
                moves = _best_moves_from_eval_payload(
                    payload,
                    self.config.stockfish_best_score_threshold,
                    self.config.stockfish_multi_pv,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug(
                    "Failed to parse static eval for %s: %s",
                    fen,
                    exc,
                )
                continue
            if moves:
                hits[fen] = moves
        return hits

    def _resolve_pct(self, pct: float | None) -> float:
        return pct if pct is not None else self.config.explorer_pct

    def _build_expansion_context(
        self,
        nodes: Iterable[RepertoireNode] | None,
        max_concurrency: int | None,
        progress_callback: Callable[[RepertoireNode], None] | None,
    ) -> ExpansionContext | None:
        targets = list(nodes or self.leaf_nodes)
        if not targets:
            return None

        resolved_concurrency = max_concurrency or min(4, len(targets))
        return ExpansionContext(
            targets=targets,
            max_concurrency=resolved_concurrency,
            graph_lock=asyncio.Lock(),
            progress_callback=progress_callback,
        )

    async def _run_expansion(
        self,
        context: ExpansionContext,
        worker: Callable[[RepertoireNode, asyncio.Lock], Awaitable[list[str]]],
        *,
        error_handler: Callable[[Exception, RepertoireNode], list[str]] | None = None,
    ) -> dict[str, list[str]]:
        queue: asyncio.Queue[RepertoireNode] = asyncio.Queue()
        for node in context.targets:
            queue.put_nowait(node)

        results: dict[str, list[str]] = {}

        async def worker_task() -> None:
            while True:
                try:
                    node = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
                try:
                    moves = await worker(node, context.graph_lock)
                    results[node.fen] = moves
                    if context.progress_callback is not None:
                        context.progress_callback(node)
                except Exception as exc:  # noqa: BLE001
                    if error_handler is None:
                        raise
                    results[node.fen] = error_handler(exc, node)
                finally:
                    queue.task_done()

        workers = [
            asyncio.create_task(worker_task()) for _ in range(context.max_concurrency)
        ]
        await queue.join()
        await asyncio.gather(*workers, return_exceptions=True)
        return results

    async def add_engine_variations_for_node(
        self,
        node: RepertoireNode | None = None,
        multi_pv: int | None = None,
        graph_lock: asyncio.Lock | None = None,
        best_moves: list[str] | None = None,
    ) -> list[str]:
        """Attach engine candidate moves at the given node (default current) as PGN variations."""

        target_node = node or self.current_node
        moves_to_add = best_moves
        if moves_to_add is None:
            moves_to_add = await self._analysis_best_moves(
                target_node,
                multi_pv=multi_pv,
            )

        base_board = chess.Board(target_node.fen)
        pgn_node = self._pgn_node_for(target_node)
        existing_moves = {var.move for var in pgn_node.variations}
        added_moves: list[str] = []

        for uci_move in moves_to_add:
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
        progress_callback: Callable[[RepertoireNode], None] | None = None,
    ) -> dict[str, list[str]]:
        """Expand all given (or leaf) nodes in parallel using a worker queue."""

        context = self._build_expansion_context(
            nodes,
            max_concurrency,
            progress_callback,
        )
        if context is None:
            return {}

        static_hits: dict[str, list[str]] = {}
        if context.targets:
            static_hits = self._static_lichess_eval_best_moves(
                [node.fen for node in context.targets]
            )

        async def worker(node: RepertoireNode, graph_lock: asyncio.Lock) -> list[str]:
            return await self.add_engine_variations_for_node(
                node=node,
                multi_pv=multi_pv,
                graph_lock=graph_lock,
                best_moves=static_hits.get(node.fen),
            )

        return await self._run_expansion(context, worker)

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
        pgn_node = self._pgn_node_for(target_node)

        total_games = None
        response = getattr(api, "response", None)
        if response is not None and hasattr(response, "totalGames"):
            total_games = response.totalGames
        if total_games is not None:
            target_node.games_reached = total_games
            for node_comment in target_node.pgn_nodes:
                node_comment.comment = upsert_reach_count_tag(
                    node_comment.comment, total_games
                )
            pgn_node.comment = upsert_reach_count_tag(pgn_node.comment, total_games)
        target_pct = self._resolve_pct(pct)
        moves = api.top_p_pct_moves(
            pct=target_pct,
            max_moves=self.config.explorer_max_moves,
            min_game_share=self.config.explorer_min_game_share,
        )
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
        progress_callback: Callable[[RepertoireNode], None] | None = None,
    ) -> dict[str, list[str]]:
        """Expand nodes by fetching explorer moves via a worker queue."""
        context = self._build_expansion_context(
            nodes,
            max_concurrency,
            progress_callback,
        )
        if context is None:
            return {}

        async def worker(node: RepertoireNode, graph_lock: asyncio.Lock) -> list[str]:
            return await self.add_explorer_variations_for_node(
                node=node,
                pct=pct,
                graph_lock=graph_lock,
            )

        def handle_error(exc: Exception, node: RepertoireNode) -> list[str]:
            if isinstance(exc, httpx.HTTPStatusError):
                status = exc.response.status_code if exc.response is not None else "?"
                logger.warning(
                    "Explorer API error (%s) while expanding %s: %s",
                    status,
                    node.fen,
                    exc,
                )
            else:
                logger.warning("Explorer expansion failed for %s: %s", node.fen, exc)
            return []

        return await self._run_expansion(context, worker, error_handler=handle_error)

    async def expand_leaves_by_turn(
        self,
        *,
        multi_pv: int | None = None,
        pct: float | None = None,
        max_concurrency: int | None = None,
        max_player_moves: int | None = None,
        min_games_reached: int | None = None,
        progress_callback: Callable[[RepertoireNode], None] | None = None,
    ) -> dict[str, list[str]]:
        """Expand leaf nodes, routing player turns to the engine and others to explorer."""
        use_explorer = getattr(self.config, "explorer_enabled", True)

        threshold = (
            min_games_reached
            if min_games_reached is not None
            else self.config.min_games_reached
        )

        leaves = self.leaf_nodes
        player_nodes: list[RepertoireNode] = []
        opponent_nodes: list[RepertoireNode] = []

        if leaves:
            mask = _core.player_turn_mask(
                self.side == chess.WHITE,
                [node.fen for node in leaves],
            )
            player_counts = [self.player_move_count(node) for node in leaves]
            for node, is_player, count in zip(leaves, mask, player_counts):
                if (
                    threshold is not None
                    and node.games_reached is not None
                    and node.games_reached < threshold
                ):
                    continue
                if max_player_moves is not None and count >= max_player_moves:
                    continue
                if is_player or not use_explorer:
                    player_nodes.append(node)
                else:
                    opponent_nodes.append(node)

        results: dict[str, list[str]] = {}

        if player_nodes:
            engine_results = await self.add_engine_variations(
                nodes=player_nodes,
                multi_pv=multi_pv,
                max_concurrency=max_concurrency,
                progress_callback=progress_callback,
            )
            results.update(engine_results)

        if opponent_nodes:
            explorer_results = await self.add_explorer_variations(
                nodes=opponent_nodes,
                pct=pct,
                max_concurrency=max_concurrency,
                progress_callback=progress_callback,
            )
            results.update(explorer_results)

        return results

    def export_pgn(self, filepath: str) -> None:
        """Export the repertoire PGN to the given file."""
        self._sort_variations_by_reach(self.root_node, self.game)
        with open(filepath, "w", encoding="utf-8") as f:
            exporter = chess_pgn.FileExporter(f)
            self.game.accept(exporter)

    def __hash__(self) -> int:
        # Allow usage in weak key caches that rely on object identity.
        return object.__hash__(self)
