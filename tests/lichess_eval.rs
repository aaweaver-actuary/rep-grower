use std::fs::File;
use std::io::{BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Command, Stdio};

use serde_json::json;
use tempfile::tempdir;

use _core::{lookup_lichess_eval, lookup_lichess_evals};

fn write_sample_zst(path: &std::path::Path) {
    let file = File::create(path).expect("create zst file");
    let mut encoder = zstd::stream::write::Encoder::new(file, 0).expect("encoder");

    let entries = vec![
        json!({
            "fen": "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 0 1",
            "depth": 16,
            "pvs": [{"cp": 20, "moves": ["c7c5"]}],
        }),
        json!({
            "fen": "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 2",
            "depth": 12,
            "pvs": [{"cp": -15, "moves": ["d2d4"]}],
        }),
    ];

    for entry in entries {
        let line = entry.to_string();
        encoder.write_all(line.as_bytes()).expect("write line");
        encoder.write_all(b"\n").expect("write newline");
    }

    encoder.finish().expect("finish encoder");
}

#[test]
fn lookup_returns_matching_eval() {
    let tmp = tempdir().expect("tempdir");
    let path = tmp.path().join("lichess_eval.jsonl.zst");
    write_sample_zst(&path);

    // Use a non-canonical FEN to ensure normalization still matches the stored record.
    let query_fen = "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 8 3";

    let result = lookup_lichess_eval(&path, query_fen).expect("lookup success");
    let value = result.expect("expected a hit");
    assert_eq!(value["depth"], 16);
    assert_eq!(value["pvs"][0]["cp"], 20);
}

#[test]
fn lookup_returns_none_when_absent() {
    let tmp = tempdir().expect("tempdir");
    let path = tmp.path().join("lichess_eval.jsonl.zst");
    write_sample_zst(&path);

    let missing = "rnbqkbnr/pppppppp/8/8/3P4/8/PPP1PPPP/RNBQKBNR b KQkq - 0 1";
    let result = lookup_lichess_eval(&path, missing).expect("lookup success");
    assert!(result.is_none());
}

#[test]
fn lookup_many_returns_ordered_matches_and_nones() {
    let tmp = tempdir().expect("tempdir");
    let path = tmp.path().join("lichess_eval.jsonl.zst");
    write_sample_zst(&path);

    // Include duplicate-in-position-but-different-counters and a missing FEN.
    let queries = vec![
        // Legal positions matching the sample entries (with different move counters).
        "rnbqkbnr/pppppppp/8/8/4P3/8/PPPP1PPP/RNBQKBNR b KQkq - 8 3".to_string(),
        "rnbqkbnr/pppp1ppp/8/4p3/4P3/8/PPPP1PPP/RNBQKBNR w KQkq - 0 8".to_string(),
        // Valid but absent to ensure None is returned.
        "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1".to_string(),
    ];

    for fen in &queries {
        _core::canonicalize_fen_str(fen)
            .unwrap_or_else(|err| panic!("failed to canonicalize test fen '{fen}': {err}"));
    }

    let results = lookup_lichess_evals(&path, &queries).expect("lookup many success");
    assert_eq!(results.len(), queries.len());

    assert!(results[0].is_some());
    assert!(results[1].is_some());
    assert!(results[2].is_none());

    let first_depth = results[0].as_ref().unwrap()["depth"].as_i64().unwrap();
    let second_cp = results[1].as_ref().unwrap()["pvs"][0]["cp"]
        .as_i64()
        .unwrap();
    assert_eq!(first_depth, 16);
    assert_eq!(second_cp, -15);
}

#[test]
fn lookup_finds_start_position_when_real_dataset_present() {
    let path = Path::new("/Users/andy/Downloads/lichess_db_eval.jsonl.zst");
    if !path.exists() {
        eprintln!(
            "skipping real dataset lookup test; file not found at {}",
            path.display()
        );
        return;
    }

    let start_fen = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";
    let result = lookup_lichess_eval(path, start_fen).expect("lookup success");
    assert!(
        result.is_some(),
        "expected to find starting position in dataset at {}",
        path.display()
    );
}

fn stockfish_cp_for_fen(engine_path: &Path, fen: &str, depth: u32) -> Option<i32> {
    let mut child = Command::new(engine_path)
        .stdin(Stdio::piped())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;

    {
        let mut stdin = child.stdin.take()?;
        let _ = writeln!(stdin, "uci");
        let _ = writeln!(stdin, "setoption name MultiPV value 1");
        let _ = writeln!(stdin, "isready");
        let _ = writeln!(stdin, "ucinewgame");
        let _ = writeln!(stdin, "position fen {}", fen);
        let _ = writeln!(stdin, "go depth {}", depth);
        let _ = stdin.flush();
    }

    let stdout = child.stdout.take()?;
    let reader = BufReader::new(stdout);
    let mut last_cp: Option<i32> = None;

    for line in reader.lines().map_while(Result::ok) {
        if line.starts_with("info ") {
            let mut tokens = line.split_whitespace().peekable();
            while let Some(tok) = tokens.next() {
                if tok == "score" {
                    if let Some(kind) = tokens.next()
                        && kind == "cp"
                        && let Some(val) = tokens.next().and_then(|s| s.parse::<i32>().ok())
                    {
                        last_cp = Some(val);
                    }
                } else if tok == "bestmove" {
                    break;
                }
            }
        }
        if line.starts_with("bestmove") {
            break;
        }
    }

    let _ = child.kill();
    let _ = child.wait();
    last_cp
}

#[test]
fn stockfish_and_lichess_eval_are_comparable_for_philidor() {
    let dataset_path = Path::new("/Users/andy/Downloads/lichess_db_eval.jsonl.zst");
    if !dataset_path.exists() {
        eprintln!(
            "skipping comparison test; dataset missing at {}",
            dataset_path.display()
        );
        return;
    }

    let engine_path = Path::new("/opt/homebrew/bin/stockfish");
    if !engine_path.exists() {
        eprintln!(
            "skipping comparison test; stockfish missing at {}",
            engine_path.display()
        );
        return;
    }

    let fen = "rnbqkbnr/ppp2ppp/3p4/4p3/4P3/5N2/PPPP1PPP/RNBQKB1R w KQkq - 0 3";

    let lichess = match lookup_lichess_eval(dataset_path, fen).expect("lookup") {
        Some(v) => v,
        None => {
            eprintln!(
                "skipping comparison test; fen missing from dataset {}",
                dataset_path.display()
            );
            return;
        }
    };

    let lichess_cp = lichess
        .get("pvs")
        .and_then(|p| p.get(0))
        .and_then(|pv| pv.get("cp"))
        .and_then(|cp| cp.as_i64())
        .map(|cp| cp as i32);

    if lichess_cp.is_none() {
        eprintln!("skipping comparison test; cp missing in lichess entry");
        return;
    }

    let stockfish_cp = match stockfish_cp_for_fen(engine_path, fen, 12) {
        Some(cp) => cp,
        None => {
            eprintln!("skipping comparison test; could not obtain stockfish cp");
            return;
        }
    };

    let delta = (stockfish_cp - lichess_cp.unwrap()).abs();
    assert!(
        delta <= 100,
        "expected evaluations to be within 100cp; stockfish={stockfish_cp}, lichess={}",
        lichess_cp.unwrap()
    );
}
