# Repertoire → Anki CSV Export: Requirements & Next Steps

## Objective
Generate a CSV for Anki bulk import with columns: `PuzzleID, Description, FEN, Moves`. The CSV feeds an existing JS chess-board/Anki note type that starts from a single FEN and plays a SAN move list.

## Repository Pieces to Use
- `src/rep_grow/repertoire.py`: load PGNs (`Repertoire.from_pgn_file/from_str`), access graph (`nodes_by_fen`, `leaf_nodes`, `RepertoireNode.children`), canonicalize start with `canonical_fen` (Rust `_core`).
- `src/rep_grow/repertoire_splitter.py`: optional splitting/capping by move count.
- `src/rep_grow/repertoire_analysis.py`: player-node helpers (optional labeling/filtering).
- Rust `_core` (`src/lib.rs`): `canonicalize_fen` for stable root FEN.
- Patterns: `scripts/benchmark_grow.py` (bootstrapping), `grow.py` (export patterns, not needed for final export).

## Data Model
- **Root FEN**: one canonical FEN for all rows (repertoire start).
- **Line**: SAN sequence from root to a leaf (or depth-capped node).
- **Description**: short label (default: first N plies of SAN; optionally ECO/variation tag).
- **PuzzleID**: sequential 1-up or hash of SAN string.

## Functional Requirements
1. Ingest repertoire PGN via `Repertoire.from_pgn_file(side, pgn_path)`.
2. Compute `root_fen = canonical_fen(rep.root_node.fen)`.
3. Traverse graph root→leaf (DFS), converting UCI children to SAN with a live `chess.Board`.
4. Optional filters: `max_plies`, `min_plies`, `dedupe` identical SAN lines, chunking for very large sets.
5. Build description from first `DESCRIPTION_PLIES` SAN tokens or from PGN tags (ECO/Variation) when available.
6. Emit CSV rows `[id, description, root_fen, "SAN1 SAN2 ..."]` using `csv.writer(..., quoting=csv.QUOTE_ALL)`.
7. Determinism: sort children by SAN for stable order.
8. Validation: ensure 4 columns, >0 rows, no empty Moves for non-root lines; spot-check sample in Anki.

## Proposed CLI (`scripts/export_repertoire_to_anki_csv.py`)
Flags (suggested):
- `--pgn-file PATH` (required)
- `--side {white,black}` (required)
- `--output PATH` (default `anki_repertoire.csv`)
- `--max-plies INT` / `--min-plies INT` (optional)
- `--description-plies INT` (default 8)
- `--dedupe` (flag)
- `--chunk-size INT` (optional chunking)
- `--prefix-filter SAN...` (optional, future)

Flow:
1. Load repertoire from PGN; canonicalize root FEN.
2. DFS traverse; stop at leaves or `max_plies`; skip < `min_plies`.
3. Convert UCI→SAN on a board copy; build moves string and description.
4. Dedupe if requested; write CSV (and chunk if requested).

## Edge Cases
- Empty repertoire: emit 0–1 rows; warn.
- Non-standard start FEN: supported; canonicalization still applies.
- Deep/long lines: use `--max-plies` to keep cards reasonable.
- Overlapping PGNs: use `--dedupe` to drop duplicate SAN strings.

## Acceptance Criteria
- Stable root FEN across all rows.
- Valid SAN sequences playable from the root.
- CSV imports cleanly into Anki (tested with a 5-row sample).

## Scaffolding & Testing Plan
- Add the CLI in `scripts/export_repertoire_to_anki_csv.py` using `click` (mirrors existing style).
- Add a small fixture PGN under `src/rep_grow/tests/fixtures/` and a pytest that:
  - Runs the exporter in-memory to produce rows.
  - Asserts column count, non-empty moves, stable FEN, and expected row count given the fixture.
  - Exercises `--max-plies` and `--dedupe` flags.
- Add `ruff`/`ty` coverage by placing the script under `scripts/` (already linted by existing make targets).
- Optional: chunking test to ensure file rotation logic works.

## Implementation Steps (actionable)
1. Implement `scripts/export_repertoire_to_anki_csv.py` with the flags above; reuse `canonical_fen` and `Repertoire.from_pgn_file`.
2. Ensure deterministic traversal: sort child moves by SAN before DFS push.
3. Add pytest `test_export_repertoire_to_anki_csv.py` with a tiny PGN fixture (2–3 branches) to validate output shape and options.
4. Document usage in `README.md` (short section) and link from the script docstring.
5. Manual sanity check: run the exporter on a sample PGN, import 5 rows into Anki, verify the JS board plays the line from the common FEN.
