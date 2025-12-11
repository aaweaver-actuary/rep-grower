use std::fs;
use std::path::Path;

use assert_cmd::Command;
use serde_json::Value;
use tempfile::tempdir;

fn write_sample_pgn(path: &Path) {
    let pgn = r#"[Event "?"]
[Site "?"]
[Date "2024.01.01"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"#;
    fs::write(path, pgn).expect("write pgn");
}

#[test]
fn freq_cli_outputs_expected_json() {
    let tmp = tempdir().expect("tempdir");
    let pgn_path = tmp.path().join("freq_input.pgn");
    write_sample_pgn(&pgn_path);

    #[allow(deprecated)]
    let output = Command::cargo_bin("freq")
        .expect("freq bin")
        .args([
            pgn_path.to_str().unwrap(),
            "--side",
            "white",
            "--indent",
            "0",
        ])
        .output()
        .expect("run freq");

    assert!(
        output.status.success(),
        "freq exited with failure. stdout: {} stderr: {}",
        String::from_utf8_lossy(&output.stdout),
        String::from_utf8_lossy(&output.stderr)
    );
    let stdout = String::from_utf8_lossy(&output.stdout);
    let payload: Value = serde_json::from_str(&stdout).expect("json output");

    assert_eq!(payload["side"], "white");
    let rankings = payload["rankings"].as_object().expect("rankings map");
    assert!(!rankings.is_empty());
    let has_e4 = rankings
        .values()
        .filter_map(|v| v.as_array())
        .flat_map(|arr| arr.iter())
        .any(|m| m["san"] == "e4");
    assert!(has_e4, "expected to see e4 in any ranking entry");
}
