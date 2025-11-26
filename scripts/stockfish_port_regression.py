#!/usr/bin/env python3
"""Compare the legacy Python Stockfish path against the new Rust pool."""

from __future__ import annotations

import argparse
import math
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import chess
import chess.engine

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from rep_grow import _core  # noqa: E402  # pylint: disable=wrong-import-position


@dataclass(frozen=True)
class RegressionConfig:
    engine_path: Path
    depth: int
    multi_pv: int
    think_time: float | None
    pool_size: int
    tolerance: float
    positions: Sequence[str]
    min_success_ratio: float = 0.8


@dataclass(frozen=True)
class TrialResult:
    fen: str
    python_seconds: float
    rust_seconds: float
    best_move: str
    rust_not_slower: bool


def default_positions() -> list[str]:
    sequences: list[tuple[str, ...]] = [
        (),
        ("e4", "c5", "Nf3", "d6", "d4", "cxd4", "Nxd4", "Nf6", "Nc3", "a6"),
        ("d4", "Nf6", "c4", "g6", "Nc3", "d5", "cxd5", "Nxd5", "e4", "Nxc3", "bxc3"),
        (
            "c4",
            "e5",
            "Nc3",
            "Nf6",
            "Nf3",
            "Nc6",
            "g3",
            "d5",
            "cxd5",
            "Nxd5",
            "Bg2",
            "Nb6",
        ),
        (
            "e4",
            "e5",
            "Nf3",
            "Nc6",
            "Bc4",
            "Bc5",
            "c3",
            "Nf6",
            "d4",
            "exd4",
            "cxd4",
            "Bb4+",
            "Nc3",
            "Nxe4",
            "O-O",
            "Bxc3",
            "d5",
            "Ne7",
        ),
    ]
    positions: list[str] = []
    for san_moves in sequences:
        board = chess.Board()
        for san in san_moves:
            board.push_san(san)
        positions.append(board.fen())
    return positions


def _python_stockfish_evaluate(
    fen: str,
    engine_path: Path,
    *,
    depth: int,
    multi_pv: int,
    think_time: float | None,
) -> dict:
    board = chess.Board(fen)
    limit = (
        chess.engine.Limit(time=think_time)
        if think_time and think_time > 0
        else chess.engine.Limit(depth=max(1, depth))
    )
    with chess.engine.SimpleEngine.popen_uci(str(engine_path)) as engine:
        engine.configure({"MultiPV": multi_pv})
        info = engine.analyse(board, limit, multipv=multi_pv)
    entries = info if isinstance(info, list) else [info]
    payload_pvs: list[dict] = []
    max_nodes = 0
    depth_hint = depth
    for entry in entries:
        pv_moves = entry.get("pv")
        if not pv_moves:
            continue
        score = entry.get("score")
        if score is None:
            continue
        oriented = score.pov(board.turn)
        cp = oriented.score()
        mate = oriented.mate()
        pv_dict: dict[str, object] = {
            "moves": " ".join(move.uci() for move in pv_moves),
        }
        if cp is not None:
            pv_dict["cp"] = int(cp)
            pv_dict["score"] = int(cp)
        if mate is not None:
            pv_dict["mate"] = int(mate)
            if cp is None:
                pv_dict["score"] = int(mate)
        payload_pvs.append(pv_dict)
        depth_hint = int(entry.get("depth", depth_hint))
        max_nodes = max(max_nodes, int(entry.get("nodes", 0)))
    return {
        "fen": fen,
        "depth": depth_hint,
        "knodes": max_nodes // 1000,
        "pvs": payload_pvs,
    }


def _assert_payloads_match(expected: dict, actual: dict) -> None:
    exp_pvs = expected.get("pvs", [])
    act_pvs = actual.get("pvs", [])
    if len(exp_pvs) != len(act_pvs):
        raise AssertionError(
            f"Mismatch PV count (python={len(exp_pvs)} rust={len(act_pvs)})"
        )
    for idx, (exp, act) in enumerate(zip(exp_pvs, act_pvs), start=1):
        if exp.get("moves") != act.get("moves"):
            raise AssertionError(
                f"PV {idx} move mismatch: {exp.get('moves')} != {act.get('moves')}"
            )
        if exp.get("cp") != act.get("cp"):
            raise AssertionError(
                f"PV {idx} cp mismatch: {exp.get('cp')} != {act.get('cp')}"
            )
        if exp.get("mate") != act.get("mate"):
            raise AssertionError(
                f"PV {idx} mate mismatch: {exp.get('mate')} != {act.get('mate')}"
            )


def _time_call(func):
    start = time.perf_counter()
    payload = func()
    return time.perf_counter() - start, payload


def run_trials(config: RegressionConfig) -> list[TrialResult]:
    if not config.positions:
        raise ValueError("At least one FEN is required")
    warmup_fen = config.positions[0]
    _core.stockfish_evaluate(
        warmup_fen,
        str(config.engine_path),
        config.depth,
        config.multi_pv,
        config.think_time,
        config.pool_size,
    )
    results: list[TrialResult] = []
    tolerance_factor = 1.0 + config.tolerance
    for fen in config.positions:
        python_time, python_payload = _time_call(
            lambda fen=fen: _python_stockfish_evaluate(
                fen,
                config.engine_path,
                depth=config.depth,
                multi_pv=config.multi_pv,
                think_time=config.think_time,
            )
        )
        rust_time, rust_payload = _time_call(
            lambda fen=fen: _core.stockfish_evaluate(
                fen,
                str(config.engine_path),
                config.depth,
                config.multi_pv,
                config.think_time,
                config.pool_size,
            )
        )
        _assert_payloads_match(python_payload, rust_payload)
        best_move = ""
        pvs = rust_payload.get("pvs", [])
        if pvs:
            best_move = pvs[0].get("moves", "").split(" ", 1)[0]
        results.append(
            TrialResult(
                fen=fen,
                python_seconds=python_time,
                rust_seconds=rust_time,
                best_move=best_move,
                rust_not_slower=rust_time <= python_time * tolerance_factor,
            )
        )
    return results


def ensure_success(results: Sequence[TrialResult], config: RegressionConfig) -> None:
    required = max(1, math.ceil(len(results) * config.min_success_ratio))
    passing = sum(1 for result in results if result.rust_not_slower)
    if passing < required:
        raise AssertionError(
            f"Rust evaluator needs to be no slower in at least {required} cases; only {passing} succeeded"
        )


def _load_positions(path: Path) -> list[str]:
    fens: list[str] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        fens.append(line)
    return fens


def _parse_args(argv: Iterable[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--engine-path",
        default=os.getenv("STOCKFISH_PATH", "/opt/homebrew/bin/stockfish"),
        help="Path to the Stockfish binary.",
    )
    parser.add_argument(
        "--depth", type=int, default=10, help="Search depth to request."
    )
    parser.add_argument(
        "--multi-pv", type=int, default=3, help="MultiPV setting to compare."
    )
    parser.add_argument(
        "--think-time",
        type=float,
        default=None,
        help="Optional think time in seconds (takes precedence over depth when set).",
    )
    parser.add_argument(
        "--pool-size",
        type=int,
        default=1,
        help="Number of persistent Stockfish workers for the Rust path.",
    )
    parser.add_argument(
        "--tolerance",
        type=float,
        default=0.05,
        help="Allowed slowdown ratio when comparing timings (default 5%%).",
    )
    parser.add_argument(
        "--min-success-ratio",
        type=float,
        default=0.8,
        help="Share of test cases where Rust must be no slower.",
    )
    parser.add_argument(
        "--positions-file",
        type=Path,
        help="Optional file containing one FEN per line to override the default set.",
    )
    return parser.parse_args(list(argv) if argv is not None else None)


def _build_config(
    args: argparse.Namespace, positions: Sequence[str]
) -> RegressionConfig:
    engine_path = Path(args.engine_path).expanduser()
    if not engine_path.exists():
        raise FileNotFoundError(engine_path)
    if args.depth < 0:
        raise ValueError("--depth must be non-negative")
    if args.multi_pv < 1:
        raise ValueError("--multi-pv must be at least 1")
    if args.pool_size < 1:
        raise ValueError("--pool-size must be at least 1")
    if args.think_time is not None and args.think_time <= 0:
        raise ValueError("--think-time must be positive when provided")
    return RegressionConfig(
        engine_path=engine_path,
        depth=args.depth,
        multi_pv=args.multi_pv,
        think_time=args.think_time,
        pool_size=args.pool_size,
        tolerance=max(0.0, args.tolerance),
        positions=positions,
        min_success_ratio=min(1.0, max(0.0, args.min_success_ratio)),
    )


def _print_results(results: Sequence[TrialResult]) -> None:
    print("\nTrial Summary:")
    print("fen_index  python(s)  rust(s)  delta(s)  best_move  status")
    for idx, result in enumerate(results, start=1):
        delta = result.python_seconds - result.rust_seconds
        status = "PASS" if result.rust_not_slower else "FAIL"
        print(
            f"{idx:02d}         {result.python_seconds:7.3f}   {result.rust_seconds:7.3f}   "
            f"{delta:7.3f}   {result.best_move or '-':8}  {status}"
        )


def main(argv: Iterable[str] | None = None) -> int:
    args = _parse_args(argv)
    positions = (
        _load_positions(args.positions_file)
        if args.positions_file
        else default_positions()
    )
    if len(positions) < 1:
        print("No positions available for regression test.", file=sys.stderr)
        return 2
    config = _build_config(args, positions)
    try:
        results = run_trials(config)
        ensure_success(results, config)
    except Exception as exc:  # pragma: no cover - surfaced through CLI use
        print(f"Stockfish regression failed: {exc}", file=sys.stderr)
        return 1
    _print_results(results)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
