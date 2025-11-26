#!/usr/bin/env python3
"""Benchmark harness for measuring grow-iteration throughput."""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import sys
import time
from pathlib import Path
from typing import Iterable

import chess

from rep_grow.grow import _leaf_turn_counts
from rep_grow.repertoire import Repertoire, RepertoireConfig


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument(
        "--initial-san", help="Root line written in SAN (e.g. 'e4 e5')."
    )
    source.add_argument(
        "--pgn-file",
        type=Path,
        help="Existing PGN to seed the repertoire (multi-game files allowed).",
    )
    parser.add_argument(
        "--side",
        choices=["white", "black"],
        default="white",
        help="Which side we are preparing the repertoire for.",
    )
    parser.add_argument(
        "--iterations",
        type=int,
        default=5,
        help="Number of measured grow iterations to run.",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="How many warmup iterations to run (not counted in the summary).",
    )
    parser.add_argument(
        "--engine-path",
        default="/opt/homebrew/bin/stockfish",
        help="Path to the Stockfish binary.",
    )
    parser.add_argument(
        "--engine-depth",
        type=int,
        default=20,
        help="Depth to use when calling Stockfish (set 0 when using --think-time).",
    )
    parser.add_argument(
        "--engine-pool-size",
        type=int,
        default=None,
        help="How many persistent Stockfish workers to keep alive.",
    )
    parser.add_argument(
        "--think-time",
        type=float,
        default=None,
        help="Optional think time in seconds (takes precedence when set).",
    )
    parser.add_argument(
        "--engine-multi-pv",
        type=int,
        default=10,
        help="Multi-PV setting forwarded to Stockfish.",
    )
    parser.add_argument(
        "--best-score-threshold",
        type=int,
        default=20,
        help="Centipawn distance from best move to keep when adding variations.",
    )
    parser.add_argument(
        "--explorer-pct",
        type=float,
        default=95.0,
        help="Top-p percentage for explorer move selection.",
    )
    parser.add_argument(
        "--explorer-min-game-share",
        type=float,
        default=0.05,
        help="Minimum share for the move that crosses the pct threshold.",
    )
    parser.add_argument(
        "--max-player-moves",
        type=int,
        default=None,
        help="Stop expanding a branch once this many player moves are present.",
    )
    parser.add_argument(
        "--summary-json",
        type=Path,
        help="Optional path to dump the timing summary as JSON.",
    )
    parsed = parser.parse_args(list(argv) if argv is not None else None)
    if parsed.iterations < 1:
        parser.error("--iterations must be at least 1")
    if parsed.warmup < 0:
        parser.error("--warmup cannot be negative")
    if parsed.max_player_moves is not None and parsed.max_player_moves < 1:
        parser.error("--max-player-moves must be positive when provided")
    if parsed.engine_pool_size is not None and parsed.engine_pool_size < 1:
        parser.error("--engine-pool-size must be positive when provided")
    if parsed.engine_depth <= 0 and (
        parsed.think_time is None or parsed.think_time <= 0
    ):
        parser.error("Provide a positive --engine-depth or a positive --think-time")
    return parsed


def _build_config(args: argparse.Namespace) -> RepertoireConfig:
    return RepertoireConfig(
        stockfish_multi_pv=args.engine_multi_pv,
        stockfish_depth=args.engine_depth,
        stockfish_engine_path=args.engine_path,
        stockfish_think_time=args.think_time,
        stockfish_best_score_threshold=args.best_score_threshold,
        stockfish_pool_size=args.engine_pool_size,
        explorer_pct=args.explorer_pct,
        explorer_min_game_share=args.explorer_min_game_share,
    )


def _load_repertoire(args: argparse.Namespace) -> Repertoire:
    config = _build_config(args)
    if args.pgn_file:
        if not args.pgn_file.exists():
            raise FileNotFoundError(args.pgn_file)
        side_color = chess.WHITE if args.side == "white" else chess.BLACK
        return Repertoire.from_pgn_file(
            side=side_color, pgn_path=str(args.pgn_file), config=config
        )
    rep = Repertoire.from_str(args.side, args.initial_san or "", config=config)
    rep.play_initial_moves()
    return rep


async def _run_iterations(
    rep: Repertoire,
    *,
    warmup: int,
    iterations: int,
    max_player_moves: int | None,
) -> list[float]:
    durations: list[float] = []
    total_passes = warmup + iterations
    for idx in range(1, total_passes + 1):
        stage = "warmup" if idx <= warmup else "sample"
        player_nodes, opponent_nodes = _leaf_turn_counts(rep, max_player_moves)
        print(
            f"Pass {idx:02d}/{total_passes} [{stage}] -> player={player_nodes} opponent={opponent_nodes}",
            flush=True,
        )
        start = time.perf_counter()
        await rep.expand_leaves_by_turn(max_player_moves=max_player_moves)
        elapsed = time.perf_counter() - start
        if stage == "sample":
            durations.append(elapsed)
            print(f"    duration: {elapsed:.3f}s", flush=True)
        else:
            print("    (warmup timing discarded)", flush=True)
    return durations


def _emit_summary(durations: list[float]) -> dict[str, float]:
    avg = statistics.fmean(durations)
    best = min(durations)
    worst = max(durations)
    spread = statistics.pstdev(durations) if len(durations) > 1 else 0.0
    summary = {
        "samples": len(durations),
        "average_seconds": avg,
        "best_seconds": best,
        "worst_seconds": worst,
        "stdev_seconds": spread,
    }
    print(
        "\nSummary:\n"
        f"  samples: {summary['samples']}\n"
        f"  average: {summary['average_seconds']:.3f}s\n"
        f"  best:    {summary['best_seconds']:.3f}s\n"
        f"  worst:   {summary['worst_seconds']:.3f}s\n"
        f"  stdev:   {summary['stdev_seconds']:.3f}s\n"
    )
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        rep = _load_repertoire(args)
    except (
        Exception
    ) as exc:  # pragma: no cover - argument validation already tested elsewhere
        print(f"Failed to initialize repertoire: {exc}", file=sys.stderr)
        return 1
    durations = asyncio.run(
        _run_iterations(
            rep,
            warmup=args.warmup,
            iterations=args.iterations,
            max_player_moves=args.max_player_moves,
        )
    )
    summary = _emit_summary(durations)
    if args.summary_json:
        args.summary_json.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        print(f"Wrote summary to {args.summary_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
