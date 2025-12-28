use anyhow::{Context, Result};
use serde_json::Value;
use std::fs::File;
use std::io::{BufRead, BufReader};
use std::path::Path;

use crate::canonicalize_fen_str;

/// Look up a Lichess cloud evaluation by FEN in a zstd-compressed JSONL file.
/// Returns `Ok(Some(value))` when the FEN is present, `Ok(None)` when absent.
/// The input FEN is canonicalized so differences in move counters do not block a match.
pub fn lookup_lichess_eval<P: AsRef<Path>>(dataset_path: P, fen: &str) -> Result<Option<Value>> {
    let normalized_fen = canonicalize_fen_str(fen).map_err(anyhow::Error::msg)?;

    let path_ref = dataset_path.as_ref();
    let file = File::open(path_ref)
        .with_context(|| format!("opening lichess eval file at {}", path_ref.display()))?;
    let decoder = zstd::Decoder::new(file)
        .with_context(|| format!("initializing zstd decoder for {}", path_ref.display()))?;
    let reader = BufReader::new(decoder);

    for line_result in reader.lines() {
        let line =
            line_result.with_context(|| format!("reading line from {}", path_ref.display()))?;
        if line.trim().is_empty() {
            continue;
        }

        let value: Value = serde_json::from_str(&line)
            .with_context(|| format!("parsing JSON line in {}: {}", path_ref.display(), line))?;
        let record_fen = match value.get("fen").and_then(|v| v.as_str()) {
            Some(f) => f,
            None => continue,
        };

        if record_fen == fen || record_fen == normalized_fen {
            return Ok(Some(value));
        }

        if let Ok(canonical_record) = canonicalize_fen_str(record_fen)
            && canonical_record == normalized_fen
        {
            return Ok(Some(value));
        }
    }

    Ok(None)
}

/// Look up many Lichess cloud evaluations by FEN in a single pass over the dataset.
/// Returns one entry per input FEN, preserving order: `Some(Value)` when found, `None` otherwise.
/// Matching is performed against canonicalized FEN strings to tolerate move-counter differences.
pub fn lookup_lichess_evals<P: AsRef<Path>>(
    dataset_path: P,
    fens: &[String],
) -> Result<Vec<Option<Value>>> {
    let mut canonical_inputs: Vec<String> = Vec::with_capacity(fens.len());
    let mut fen_to_indices: std::collections::HashMap<String, Vec<usize>> =
        std::collections::HashMap::new();

    for (idx, fen) in fens.iter().enumerate() {
        let canonical = canonicalize_fen_str(fen).map_err(anyhow::Error::msg)?;
        canonical_inputs.push(canonical.clone());
        fen_to_indices.entry(canonical).or_default().push(idx);
    }

    let mut results: Vec<Option<Value>> = vec![None; fens.len()];
    let mut remaining = fens.len();

    let path_ref = dataset_path.as_ref();
    let file = File::open(path_ref)
        .with_context(|| format!("opening lichess eval file at {}", path_ref.display()))?;
    let decoder = zstd::Decoder::new(file)
        .with_context(|| format!("initializing zstd decoder for {}", path_ref.display()))?;
    let reader = BufReader::new(decoder);

    for line_result in reader.lines() {
        let line =
            line_result.with_context(|| format!("reading line from {}", path_ref.display()))?;
        if line.trim().is_empty() {
            continue;
        }

        let value: Value = serde_json::from_str(&line)
            .with_context(|| format!("parsing JSON line in {}: {}", path_ref.display(), line))?;
        let record_fen = match value.get("fen").and_then(|v| v.as_str()) {
            Some(f) => f,
            None => continue,
        };

        let canonical_record = match canonicalize_fen_str(record_fen) {
            Ok(c) => c,
            Err(_) => continue,
        };

        if let Some(indices) = fen_to_indices.get(&canonical_record) {
            for &idx in indices {
                if results[idx].is_none() {
                    results[idx] = Some(value.clone());
                    remaining -= 1;
                }
            }
            if remaining == 0 {
                break;
            }
        }
    }

    // Preserve order and length with canonicalization already applied.
    Ok(results)
}
