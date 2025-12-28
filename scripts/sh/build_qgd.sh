#! /usr/bin/env zsh

PGN_PATH="${PGN_PATH:-$(pwd)/queens-gambit-declined1.pgn}"

uv run build \
	--side black \
	--pgn-file "$PGN_PATH" \
	--initial-iterations 1 \
	--iterations-per-cycle 2 \
	--target-hero-moves 15 \
	--min-games-stop 1000 \
	--engine-depth 20 \
	--engine-pool-size 4 \
	--engine-multi-pv 5 \
	--best-score-threshold 30 \
	--best-score-threshold-final 50 \
	--explorer-pct 95 \
	--explorer-pct-final 80 \
	--explorer-min-game-share 5 \
	--explorer-min-game-share-final 10 \
	--max-player-moves 15 \
	--preferred-move 0-0 \
	--preferred-move Nc6 \
	--preferred-move Nf6 \
	--analysis-provider hybrid \
	--lichess-eval-path ~/Downloads/lichess_db_eval.jsonl.zst