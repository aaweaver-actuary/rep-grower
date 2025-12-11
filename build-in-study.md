# Syncing Repertoires to Lichess Studies

This note outlines how we could export the existing repertoire graph into Lichess
Studies (instead of plain PGNs) and keep them in sync over time.

## Feasibility
- Lichess exposes authenticated endpoints to create/update study chapters from PGN
  (`POST /api/study/{studyId}/import-pgn`) and to update tags (`POST .../{chapterId}/tags`).
- We can export the repertoire in PGN chunks today (see `export-anki`/`split` flows);
  pushing those chunks as study chapters is a straightforward extension.
- Unauthenticated calls only see public chapters; private/unlisted writes require a
  bearer token. The API allows up to 64 chapters per study, so large repertoires may
  need chunking and multiple studies.

## Proposed workflow
1. **Auth/config**: Accept a `LICHESS_TOKEN` (env or config file). Allow `--study-id`
   and optional `--study-name` for creation vs. update modes.
2. **Export to PGN**: Reuse current exporters:
   - Whole repertoire → single PGN, or
   - Chunked PGNs via `split`/`export-anki` depth caps for early seeding.
3. **Chapter mapping**:
   - One chapter per split event or per top-level line prefix (e.g., first N plies).
   - Name chapters with the shared prefix (`1.e4 e5 2.Nf3 Nc6`) for readability.
   - Include `[Orientation "..."]` if we want forced board sides.
4. **Push updates**:
   - For each chapter: `POST /api/study/{studyId}/import-pgn` with the chunk PGN
     and `name` set to the intended chapter title. This creates or appends a chapter.
   - Optional: immediately `POST .../{chapterId}/tags` to set ECO/Variation/SetUp/FEN
     tags if we need specific metadata.
5. **Sync strategy**:
   - Track a local manifest mapping `chapter_slug -> {chapterId, last_hash}`.
   - On each run, recompute the PGN chunk; if the hash differs, re-import to update
     that chapter (Lichess creates a new chapter; we can delete the old one to avoid
     duplicates via `DELETE /api/study/{studyId}/{chapterId}`).
   - For first few moves seeding: run with a low `--max-plies` to publish short lines,
     then rerun with larger depth as explorer/engine moves are added.
6. **Deletion/rotation**:
   - When a chunk disappears (e.g., pruning), delete its chapter to avoid stale lines.
   - Ensure the study always has ≥1 chapter; if deleting the last, push a small stub.

## API considerations
- **Rate/limits**: Respect any request limits; batch updates, and sleep between calls.
- **Visibility**: Use unlisted/private studies for drafts; token scope must permit
  study edits.
- **Clocks/comments/variations**: Default to `variations=true`, `comments=false`,
  `clocks=false` to keep study chapters clean unless we want annotations.
- **Orientation**: If the repertoire side is Black, set orientation to `black` so
  boards face the learner correctly.

## CLI sketch
```
export-study \
  --pgn-file repertoire.pgn \
  --side white \
  --study-id <id> \
  --token $LICHESS_TOKEN \
  --max-plies 10 \
  --chunk-size 1 \
  --delete-missing
```
- Internals: split/cap repertoire → generate PGN chunks → compute hashes →
  import/update/delete chapters accordingly → write/update a manifest JSON.

## Open questions
- Should chapter names be stable hashes vs. human-readable prefixes?
- Do we want comments (engine evals, explorer stats) embedded as PGN comments?
- How to handle >64 chapters (multiple studies vs. deeper chunking)?
- Do we mirror repertoire tags (ECO/Variation) onto chapters or leave clean?

## Implementation steps (minimal viable sync tool)
1. **Config plumbing** – Define a TOML config (e.g., `~/.config/rep-grow/study.toml`) holding `token`, `study_id`, `base_url` (default `https://lichess.org`), and optional defaults like `orientation` or `clocks/comments/variations` flags.
2. **Rust client types** – Create a `StudyConfig` loader and a `StudyMovePayload` struct capturing the study ID, chapter ID (optional), move SAN/uci context, chapter name, PGN text, and flags.
3. **HTTP scaffolding** – Add a small `LichessStudyClient` with methods:
   - `import_pgn(payload)` → `POST /api/study/{studyId}/import-pgn`
   - `update_tags(payload)` → `POST /api/study/{studyId}/{chapterId}/tags`
   Include bearer auth and sensible headers.
4. **Unit tests (red/green)** – Stub HTTP with a mock server (e.g., `httpmock`) to verify request shapes, headers, and payloads; test config parsing and error cases.
5. **Integration hook** – Provide a CLI shim or Python-callable binding later to trigger sync after export (not in MVP).
6. **Refine/DRY** – Factor shared request building (base URL, auth header), guardrails (max chapters), and reuse chunked export data to construct per-chapter payloads.
