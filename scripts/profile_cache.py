#!/usr/bin/env python3
"""Quick-and-dirty harness to measure DuckDB cache effectiveness."""

from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import os
import pstats
import statistics
import sys
import time
from typing import Iterable

from rep_grow.stockfish_analysis_api import StockfishAnalysisApi


def parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--fen",
        default="rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1",
        help="FEN to analyze",
    )
    parser.add_argument(
        "--iterations", type=int, default=5, help="Number of evaluations"
    )
    parser.add_argument("--engine-path", default="/opt/homebrew/bin/stockfish")
    parser.add_argument(
        "--depth", type=int, default=12, help="Search depth (0 when using think time)"
    )
    parser.add_argument(
        "--think-time",
        type=float,
        default=None,
        help="Optional think time in seconds (only used when --depth is 0)",
    )
    parser.add_argument("--multi-pv", type=int, default=4, help="MultiPV setting")
    parser.add_argument(
        "--best-score-threshold",
        type=int,
        default=20,
        help="Threshold forwarded to StockfishAnalysisApi",
    )
    parser.add_argument(
        "--db-path",
        default=None,
        help="Override DuckDB database path (otherwise uses REP_GROW_STOCKFISH_DB)",
    )
    parser.add_argument(
        "--disable-cache",
        action="store_true",
        help="Bypass DuckDB so every request consults the engine",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Wrap the run in cProfile and print the hottest functions",
    )
    parser.add_argument(
        "--fake-eval",
        action="store_true",
        help="Skip Stockfish entirely and use a synthetic evaluation (useful for DB-only timing)",
    )
    parser.add_argument(
        "--fake-delay",
        type=float,
        default=0.25,
        help="Seconds to sleep when --fake-eval is enabled",
    )
    parsed = parser.parse_args(list(argv) if argv is not None else None)
    if parsed.iterations < 1:
        parser.error("--iterations must be at least 1")
    if parsed.depth <= 0 and (parsed.think_time is None or parsed.think_time <= 0):
        parser.error("Provide a positive --depth or --think-time")
    return parsed


def _install_fake_eval(delay: float) -> None:
    def fake_eval(self: StockfishAnalysisApi) -> dict:
        time.sleep(max(0.0, delay))
        return {
            "depth": self.depth,
            "fen": self.fen,
            "knodes": 1,
            "pvs": [
                {
                    "cp": 0,
                    "mate": None,
                    "score": 0,
                    "moves": "",
                }
            ],
        }

    StockfishAnalysisApi._evaluate_position = fake_eval  # type: ignore[attr-defined]


def _set_db_override(path: str | None) -> None:
    if path:
        os.environ["REP_GROW_STOCKFISH_DB"] = path


async def _run_once(args: argparse.Namespace, iteration: int) -> tuple[float, str]:
    api = StockfishAnalysisApi(
        args.fen,
        multi_pv=args.multi_pv,
        engine_path=args.engine_path,
        depth=args.depth,
        think_time=args.think_time,
        best_score_threshold=args.best_score_threshold,
        db_path=args.db_path,
    )
    start = time.perf_counter()
    await api.raw_evaluation(use_cache=not args.disable_cache)
    elapsed = time.perf_counter() - start
    source = api.last_response_source
    print(
        f"Iteration {iteration}: {elapsed:.3f}s (source={source}) depth={api.depth} multi_pv={api.multi_pv}"
    )
    return elapsed, source


async def _main(args: argparse.Namespace) -> None:
    durations: list[float] = []
    source_counts: dict[str, int] = {}
    for idx in range(1, args.iterations + 1):
        elapsed, source = await _run_once(args, idx)
        durations.append(elapsed)
        source_counts[source] = source_counts.get(source, 0) + 1
    avg = statistics.fmean(durations)
    worst = max(durations)
    best = min(durations)
    spread = statistics.pstdev(durations) if len(durations) > 1 else 0.0
    summary = (
        ", ".join(f"{k}:{v}" for k, v in sorted(source_counts.items())) or "unknown"
    )
    print(
        "\nSummary:\n"
        f"  average: {avg:.3f}s\n"
        f"  best:    {best:.3f}s\n"
        f"  worst:   {worst:.3f}s\n"
        f"  stdev:   {spread:.3f}s\n"
        f"  sources: {summary}\n"
    )


def _profiled_run(args: argparse.Namespace) -> None:
    profiler = cProfile.Profile()
    profiler.enable()
    asyncio.run(_main(args))
    profiler.disable()
    stream = io.StringIO()
    stats = pstats.Stats(profiler, stream=stream).sort_stats("cumulative")
    stats.print_stats(15)
    print("\nTop 15 functions by cumulative time:\n")
    print(stream.getvalue())


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    _set_db_override(args.db_path)
    if args.fake_eval:
        _install_fake_eval(args.fake_delay)
    runner = (
        _profiled_run if args.profile else lambda parsed: asyncio.run(_main(parsed))
    )
    runner(args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
