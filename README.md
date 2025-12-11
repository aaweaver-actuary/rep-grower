# rep-grow

[![CI](https://github.com/aaweaver-actuary/rep-grower/actions/workflows/ci.yml/badge.svg)](https://github.com/aaweaver-actuary/rep-grower/actions/workflows/ci.yml)

Tooling for automatically growing, pruning, and visualizing chess repertoires that are
engine-verified for the player side and grounded in human game data for the opponent.

The repository centers on a Python package plus several Click CLIs:

- `grow` – iteratively expand a PGN repertoire by alternating between Stockfish
	analysis for the player and Lichess Explorer statistics for the opponent.
- `prune` – collapse an existing repertoire down to the single most frequent move
	for each player decision to yield a practical training line.
- `split` – divide a large repertoire into smaller PGNs capped by move count and
	labeled by their shared prefix.
- `freq` – print the move-frequency ordering the pruner uses at each player node.
- `export-anki` – emit an Anki-ready CSV of repertoire lines for bulk import.

Supporting scripts export PyVis graphs from pruning reports and bundle optional JS
assets that can front a richer UI later.

The ultimate goal: build a system that can generate trusted repertoires for either
side of the board, relying on engine lines for our play while understanding the
historically common replies (and mistakes) the opponent is likely to make.

## How the system works

```
┌────────────┐      ┌──────────────────────┐      ┌─────────────────────┐
│  grow CLI  │──▶──▶│  Repertoire graph    │──▶──▶│  PGN + reports      │
└────────────┘      │  (nodes deduped by   │      │  (export per pass)  │
										│   canonical FEN)     │      └─────────────────────┘
										│        ▲             │
										│        │             │
				┌───────────┴────────┴────────┐ ┌──┴─────────────────────────┐
				│ StockfishAnalysisApi (player)│ │ LichessExplorerApi        │
				│  • best-move search          │ │  • historic move stats    │
				│  • DuckDB eval cache         │ │  • probabilistic filter   │
				└──────────────────────────────┘ └───────────────────────────┘
```

Key pieces:

- `Repertoire` (`src/rep_grow/repertoire.py`) owns the PGN tree, deduplicates
	transpositions via canonical FENs, and tracks every PGN node reachable from the
	root position. Helper methods expand leaves, add Stockfish variations, and export
	fully annotated PGNs.
- `StockfishAnalysisApi` wraps `python-chess`'s engine API, persisting evaluations
	in DuckDB (configurable via `REP_GROW_STOCKFISH_DB`) so repeated calls to the same
	position are instant.
- `LichessExplorerApi` fetches aggregated human games with retry/backoff logic, then
	filters moves to the top `p%` coverage or until their per-move share falls below a
	configurable floor.
- `RepertoirePruner` walks the graph and selects the most frequent player move at
	every decision, producing a "principled line" PGN plus JSON selection data (when
	exported via other tooling).
- `scripts/visualize_pruner.py` converts those selection reports into a PyVis HTML
	graph so you can quickly spot bottlenecks or unexplored branches.

## User-facing entry points

### Installation prerequisites

- Python 3.12+
- Stockfish binary (defaults to `/opt/homebrew/bin/stockfish`; override with
	`--engine-path` or `REP_GROW_STOCKFISH_DB` for cache file location).
- `uv` + `make` are convenient for running the quality gate (`make check`).

Install editable dependencies:

```bash
uv sync           # or pip install -e .[dev]
```

### `grow` CLI

Expands a repertoire for a specific side.

```bash
grow \
	--side white \
	--initial-san "e4 e5 Nf3 Nc6 Bc4" \
	--iterations 4 \
	--engine-path /usr/local/bin/stockfish \
	--engine-depth 18 \
	--engine-multi-pv 12 \
	--explorer-pct 95 \
	--best-score-threshold 25 \
	--output-dir target/pruner_reports
```

Highlights:

- Accepts either an initial SAN string (`--initial-san`) or an existing PGN
	(`--pgn-file`), but never both.
- Iteration loop alternates between exploring our move (Stockfish best-move set)
	and opponent replies (Explorer top-p%) for every leaf, updating a progress bar
	with counts and SAN deltas.
- Every pass exports `INITIAL_SAN__iteration_N.pgn` plus a final consolidated PGN.

### `prune` CLI

Reduce a PGN to the single highest-frequency player move per node:

```bash
prune repertoire.pgn --side white --output repertoire_pruned.pgn
```

Useful after large `grow` sessions to distill study lines or to feed the visualizer.

### `split` CLI

Break a large repertoire PGN into several games, each capped at a configurable
number of moves and labeled by the shared prefix they contain:

```bash
split repertoire.pgn --side white --max-moves 1000 --output repertoire_split.pgn
```

Each resulting game uses an `Event` header such as `1.e4 e5 2.Nf3 Nc6` to show
the common moves for that chunk and sets `[SetUp "1"]` + `[FEN ...]` so play
begins directly from the split position. This is handy when viewers cap PGN
size (e.g., 1000 moves) or when you want to organize chapters by early
divergences.

### `export-anki` CLI

Generate an Anki-compatible CSV (columns: `PuzzleID, Description, FEN, Moves`) from
a repertoire PGN:

```bash
export-anki \
	--pgn-file repertoire.pgn \
	--side white \
	--output anki_repertoire.csv \
	--max-plies 12 \
	--description-plies 8 \
	--dedupe
```

- Root FEN is canonicalized so every row starts from the same position.
- Traversal is depth-first with children sorted by SAN for determinism; `--max-plies`
	truncates move lists while `--min-plies` drops shorter lines.
- `--dedupe` collapses identical SAN strings after capping; `--chunk-size` writes
	numbered `_partN` CSV files for large exports.
- Descriptions default to the first N SAN tokens, falling back to PGN `Variation`/`ECO`
	tags when present.
- Output uses `csv.QUOTE_ALL` to match Anki's bulk importer expectations.

### `freq` CLI

Inspect the global move-frequency ordering that the pruner uses when choosing
lines:

```bash
freq repertoire.pgn --side white --indent 2 > frequencies.json
```

The JSON groups every player-to-move node by FEN and lists each legal move in
descending order of how often that move occurs across the repertoire graph. Use
this to understand why `prune` picked a specific line or to locate candidate
moves whose frequency you’d like to boost.

### `scripts/visualize_pruner.py`

```bash
python scripts/visualize_pruner.py target/pruner_reports/iteration6_selection.json \
	--output iteration6.html --max-depth 12 --include-alternatives
```

Produces an interactive HTML graph (PyVis) colored by whether an edge was the
selected move or an alternative, with edge widths proportional to frequency counts.

## Repository map

- `src/rep_grow/repertoire.py` – Core PGN graph, transposition handling, expanders.
- `src/rep_grow/stockfish_analysis_api.py` – Engine adapter + DuckDB cache.
- `src/rep_grow/lichess_explorer_api.py` – Explorer client with retry/backoff and
	top-p filtering.
- `src/rep_grow/repertoire_pruner.py` – Move frequency analysis + pruning export.
- `src/rep_grow/grow.py` – Click CLI orchestrating expansion/export loop.
- `src/rep_grow/prune.py` – Click CLI for pruning existing PGNs.
- `src/rep_grow/export_repertoire_to_anki_csv.py` – Click CLI for CSV exports to
	Anki.
- `scripts/visualize_pruner.py` – PyVis renderer for pruning reports.
- `lib/` – Front-end dependencies (tom-select, vis.js) for future UI work.
- `tests/` – Rich pytest suite exercising the CLI flows, graph logic, pruner, and
	API clients (including async Stockfish/Lichess stubs).

## Project goals

1. **Automatic repertoire generation** – Given any seed (short line or existing
	 PGN), produce a breadth-balanced tree of variations validated by Stockfish for
	 our moves and grounded in real-world opponent tendencies.
2. **Opponent modeling** – Track historic move frequencies to anticipate common
	 sidesteps and punishable errors, making the repertoire resilient in practical
	 games.
3. **Actionable exports** – Provide PGNs, pruning reports, and HTML graphs suitable
	 for drilling, sharing, or further toolchain ingestion.

## Next steps toward a helpful tool

1. **Automated evaluation queue** – Persist pending FENs and process them with a
	 background worker so long searches or multiple openings can run unattended.
2. **Explorer provenance + weighting** – Store the opponent statistics used for each
	 node so future analysis (e.g., blunder likelihood) can be surfaced in the PGN or
	 visualizer tooltips.
3. **Human-friendly pruning profiles** – Allow `prune` to select the top *k* moves
	 per depth or by EV gap, enabling study sets with both primary and backup plans.
4. **Web UI / Study sync** – Reuse the assets under `lib/` plus the PyVis output to
	 host a browser viewer, optionally syncing chapters to Lichess studies.
5. **Automated testing against real engines** – Extend the DuckDB-backed cache to
	 schedule fresh Stockfish runs for stale nodes and compare evaluations against a
	 baseline, ensuring repertoire accuracy over time.

With these pieces in place, the project becomes a repeatable pipeline for creating
engine-verified repertoires for either color, complete with opponent-facing
contingency prep. Contributions and issue reports are welcome!
