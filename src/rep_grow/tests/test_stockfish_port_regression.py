from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

regression = importlib.import_module("scripts.stockfish_port_regression")


def _require_engine() -> Path:
    path = Path(os.getenv("STOCKFISH_PATH", "/opt/homebrew/bin/stockfish")).expanduser()
    if not path.exists():
        pytest.skip(f"Stockfish binary not found at {path}")
    return path


def test_rust_stockfish_matches_python_speed():
    if not os.getenv("REP_GROW_RUN_ENGINE_TESTS"):
        pytest.skip("Set REP_GROW_RUN_ENGINE_TESTS=1 to run engine parity test")
    engine_path = _require_engine()
    config = regression.RegressionConfig(
        engine_path=engine_path,
        depth=8,
        multi_pv=3,
        think_time=None,
        pool_size=1,
        tolerance=0.05,
        positions=regression.default_positions(),
        min_success_ratio=0.8,
    )
    results = regression.run_trials(config)
    regression.ensure_success(results, config)
