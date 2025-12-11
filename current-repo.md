# Current repo snapshot

## Shape of the system (today)
- Python package (`src/rep_grow`) holds the orchestration and most logic: repertoire graph (`repertoire.py`), PGN ingest/export, Stockfish adapter + DuckDB cache (`stockfish_analysis_api.py`), Explorer client with retries/backoff (`lichess_explorer_api.py`), pruning (`repertoire_pruner.py`), CLI entrypoints (`grow`, `prune`, `split`, `export-anki`, `freq`, `annotate-reach-counts`).
- Rust crate (`src/lib.rs` + bins) builds a cdylib for Python and rlib for Rust tooling; provides fast helpers (Stockfish process glue, FEN utilities, CLI bins like `freq`), tested via cargo. Pyo3 bridges to Python.
- Tests: pytest suite exercises the Python graph/CLI/API layers; cargo tests cover the Rust helpers and CLI bin behavior. Quality gate is `make check` (ruff + format + ty + pytest + cargo fmt/clippy/test).
- Assets: `lib/` holds front-end deps for possible visualizations (tom-select, vis.js). `scripts/` has auxiliary exporters/visualizers.

## What feels SOLID/DRY
- Repertoire graph dedupes transpositions by canonical FEN; node model threads through PGN nodes consistently.
- API layers are separated: Stockfish vs Lichess Explorer clients with retry/backoff knobs; DuckDB cache encapsulated behind the Stockfish adapter.
- CLI flows are composable and mostly side-effect free beyond explicit file writes; tests cover the happy paths and error cases.
- PGN tagging helpers (reach counts) are centralized and reused across annotator/exports.

## Where coupling is high (single-machine assumptions)
- Persistence: PGN is the single source of truth, written to local disk; no abstraction for alternate stores (e.g., Lichess Study, S3, DB).
- Engine: Stockfish path is assumed local; cache path is a local DuckDB file (`REP_GROW_STOCKFISH_DB`). No remote/external engine provider abstraction.
- Explorer: Direct HTTP calls with retries; no pluggable fetcher or offline cache beyond current process memory.
- Work coordination: `grow`/`annotate` iterate synchronously within one process. There is no task queue, locking, or lease concept per FEN, so multiple workers would duplicate work.
- Progress/checkpointing: checkpointers write to local files; no shared checkpoint sink.
- Configuration: Flags/env vars; no layered config file describing credentials, study IDs, or backend choices.

## Abstractions to introduce (Python)
- `RepertoireStore` Protocol: `load() -> Repertoire`, `save(repertoire, meta)` with implementations `FilePGNStore`, `LichessStudyStore` (per-chapter naming, rate-limit handling), `MemoryStore` (tests).
- `PositionWorkQueue` Protocol: lease/release FENs to annotate/analyze; implementations could back onto DuckDB/SQLite, Redis, or study chapter tags.
- `EngineClient` Protocol: `analyze(fen, settings) -> Eval` with `LocalStockfishClient` (today) and future `RemoteEngineClient`.
- `ExplorerClient` Protocol: wrap current `LichessExplorerApi`; allow swap for cached/offline sources.
- `CheckpointWriter` Protocol: handle periodic snapshots; default to local file, option for study chapter or object storage.
- Factor CLI orchestration to accept these interfaces (constructor injection or small factory map driven by config). Use `typing.Protocol`/`ABC` and thin adapters.

## Abstractions to introduce (Rust)
- Traits mirroring the Python protocols where Rust helpers are used directly: `RepertoireSink`/`Source` for PGN or study I/O; `WorkQueue` for FEN leases; `EngineProvider` for Stockfish vs remote service.
- Keep the cdylib boundary narrow: expose trait objects or function entrypoints that operate on plain data (FEN strings, SAN lists) so Python can pick the implementation and hand work into Rust for speed-critical bits.

## Path to a Lichess Study as source of truth
- Ingest: fetch study metadata and chapters via Lichess API; pull chapter PGNs; build `Repertoire` from concatenated chapters; record mapping of chapter id -> root FEN/prefix.
- Persist: after mutations, push PGN chunks back to chapters (respect chapter size limits); include reach-count tags and metadata so work can resume idempotently.
- Identity: use canonical FEN for node identity; store a per-node marker (e.g., comment tag) to note evaluation status, last updated time, and worker id.

## Multi-machine, no-duplicate-work strategy
- Shared work queue: store leases in the study (tags) or an external store (Redis/SQLite in cloud). Lease by FEN with TTL to avoid abandonment; include worker id and start time.
- Idempotent updates: workers write results atomically—either via optimistic concurrency (e.g., chapter etag) or via append-only checkpoints in a side channel, then a reconciler folds them into the study.
- Sharding: partition by chapter or by FEN hash buckets so machines can focus on disjoint sets; balanced by work queue fairness.
- Caching: share Explorer responses and engine evals via a small DB/file cache synced through object storage or a lightweight API to reduce duplicate HTTP/engine effort.
- Failure recovery: periodic checkpoints to a durable store; on startup, requeue expired leases and reconcile partial chapter writes.

## Config file sketch (toml or yaml)
- `lichess.api_token`, `study.id`, `chapter_prefix`, `rate_limits.{sleep,burst}`
- `engine.{kind=local|remote, path, depth, multipv, cache_path}`
- `explorer.{base_url, retries, backoff, jitter, cache_path}`
- `work_queue.{backend=file|sqlite|redis|study-tags, uri, lease_seconds}`
- `checkpoint.{backend=file|study, path_or_prefix, every_n}`
- `logging.{level, progress_every}`

## Next steps (concrete)
1. Define Python Protocols (`RepertoireStore`, `PositionWorkQueue`, `EngineClient`, `ExplorerClient`, `CheckpointWriter`) and refactor CLIs to accept them via factories driven by config.
2. Add a config loader (toml/yaml) layered over env/CLI flags; thread config into CLI main functions.
3. Implement `FilePGNStore` (current behavior) and `LichessStudyStore` (get/push chapters; handle rate limits and chapter size constraints).
4. Add a simple SQLite-backed `PositionWorkQueue` with leases; wire `grow`/`annotate` to pull next FENs from the queue instead of in-memory traversal only.
5. Introduce per-node status tags (comment markers) to encode pending/in-progress/done and last-updated worker id, so study sync stays idempotent.
6. Add shared caches: optional Explorer cache table (DuckDB/SQLite) keyed by FEN; allow pointing at a shared path/object storage.
7. Build a reconciliation command to merge partial results into the study and clean up expired leases.
8. Extend Rust helpers with traits mirroring the new interfaces where performance matters; keep Python owning the choice of backend.
