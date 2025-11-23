#!/usr/bin/env python3
"""Compare DuckDB cache on/off performance and emit a JSON report."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
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
        "--iterations",
        type=int,
        default=5,
        help="Number of timed evaluations per scenario",
    )
    parser.add_argument(
        "--warmup",
        type=int,
        default=1,
        help="Cache warmup runs before the main scenarios",
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
        "--output",
        default="cache_analysis.json",
        help="Where to write the JSON report (use '-' for stdout)",
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
    if parsed.warmup < 0:
        parser.error("--warmup must be non-negative")
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


def _make_api(args: argparse.Namespace) -> StockfishAnalysisApi:
    return StockfishAnalysisApi(
        args.fen,
        multi_pv=args.multi_pv,
        engine_path=args.engine_path,
        depth=args.depth,
        think_time=args.think_time,
        best_score_threshold=args.best_score_threshold,
        db_path=args.db_path,
    )


def _summary_stats(durations: list[float]) -> dict:
    return {
        "average_sec": statistics.fmean(durations),
        "median_sec": statistics.median(durations),
        "best_sec": min(durations),
        "worst_sec": max(durations),
        "stdev_sec": statistics.pstdev(durations) if len(durations) > 1 else 0.0,
    }


def _scenario_template(use_cache: bool) -> tuple[str, bool]:
    return ("cache_enabled" if use_cache else "cache_disabled", use_cache)


async def _warmup_cache(args: argparse.Namespace) -> None:
    if args.warmup <= 0:
        return
    print(f"Priming cache with {args.warmup} warmup run(s)...")
    for _ in range(args.warmup):
        api = _make_api(args)
        await api.raw_evaluation(use_cache=True)


async def _run_scenario(args: argparse.Namespace, *, use_cache: bool) -> dict:
    label, flag = _scenario_template(use_cache)
    durations: list[float] = []
    source_counts: dict[str, int] = {}
    runs: list[dict] = []
    for idx in range(1, args.iterations + 1):
        api = _make_api(args)
        start = time.perf_counter()
        await api.raw_evaluation(use_cache=flag)
        elapsed = time.perf_counter() - start
        source = api.last_response_source
        durations.append(elapsed)
        source_counts[source] = source_counts.get(source, 0) + 1
        runs.append(
            {
                "iteration": idx,
                "elapsed_sec": elapsed,
                "source": source,
            }
        )
        print(
            f"[{label}] iteration {idx}: {elapsed:.3f}s (source={source}) depth={api.depth} multi_pv={api.multi_pv}"
        )
    return {
        "label": label,
        "use_cache": flag,
        "iterations": args.iterations,
        "summary": _summary_stats(durations),
        "source_counts": source_counts,
        "runs": runs,
    }


async def _async_main(args: argparse.Namespace) -> dict:
    await _warmup_cache(args)
    scenarios: dict[str, dict] = {}
    for use_cache in (True, False):
        scenario_result = await _run_scenario(args, use_cache=use_cache)
        scenarios[scenario_result["label"]] = scenario_result
    metadata = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "fen": args.fen,
        "iterations_per_scenario": args.iterations,
        "warmup_runs": args.warmup,
        "depth": args.depth,
        "think_time": args.think_time,
        "multi_pv": args.multi_pv,
        "best_score_threshold": args.best_score_threshold,
        "engine_path": str(args.engine_path),
        "db_path": args.db_path or os.environ.get("REP_GROW_STOCKFISH_DB"),
        "fake_eval": args.fake_eval,
        "fake_delay": args.fake_delay if args.fake_eval else None,
    }
    return {
        "metadata": metadata,
        "scenarios": scenarios,
    }


def _write_report(data: dict, destination: str) -> None:
    if destination == "-":
        json.dump(data, sys.stdout, indent=2)
        print()
        return
    path = Path(destination).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote analysis report to {path}")


def main(argv: Iterable[str] | None = None) -> int:
    args = parse_args(argv)
    _set_db_override(args.db_path)
    if args.fake_eval:
        _install_fake_eval(args.fake_delay)
    report = asyncio.run(_async_main(args))
    _write_report(report, args.output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
