use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3::{Bound, FromPyObject};

use shakmaty::Position;
use shakmaty::fen::Fen;
use shakmaty::san::SanPlus;
use shakmaty::uci::UciMove;
use shakmaty::{CastlingMode, Chess, Color, EnPassantMode, Move, Role, Square};

use std::collections::{HashMap, HashSet};
use std::str::FromStr;

mod stockfish;
use stockfish::stockfish_evaluate;

/// A Python module implemented in Rust.
#[pymodule]
fn _core(_py: Python<'_>, m: Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(player_move_analysis, &m)?)?;
    m.add_function(wrap_pyfunction!(player_turn_mask, &m)?)?;
    m.add_function(wrap_pyfunction!(split_repertoire_nodes, &m)?)?;
    m.add_function(wrap_pyfunction!(canonicalize_fen, &m)?)?;
    m.add_function(wrap_pyfunction!(stockfish_evaluate, &m)?)?;
    Ok(())
}

#[pyfunction]
fn canonicalize_fen(fen_text: String) -> PyResult<String> {
    let fen = Fen::from_str(&fen_text).map_err(|err| {
        PyValueError::new_err(format!(
            "Invalid FEN '{}' while canonicalizing: {err}",
            fen_text
        ))
    })?;
    let position: Chess = fen.into_position(CastlingMode::Standard).map_err(|err| {
        PyValueError::new_err(format!(
            "Unable to construct position from '{}' while canonicalizing: {err}",
            fen_text
        ))
    })?;
    let normalized = Fen::from_position(position, EnPassantMode::Legal).to_string();
    Ok(reset_move_counters(&normalized))
}

fn reset_move_counters(fen_text: &str) -> String {
    let mut parts: Vec<&str> = fen_text.split_whitespace().collect();
    if parts.len() == 6 {
        parts[4] = "0";
        parts[5] = "1";
        return parts.join(" ");
    }
    fen_text.to_string()
}

#[pyfunction]
fn player_move_analysis(
    py: Python<'_>,
    nodes: Vec<PyNodeInput>,
) -> PyResult<(Py<PyAny>, Py<PyAny>)> {
    let mut analyzed_nodes: Vec<NodeAnalysis> = Vec::with_capacity(nodes.len());
    let mut frequencies: HashMap<Fingerprint, u32> = HashMap::new();

    for node in nodes {
        let fen = Fen::from_str(&node.fen)
            .map_err(|err| PyValueError::new_err(format!("Invalid FEN '{}': {err}", node.fen)))?;
        let position: Chess = fen.into_position(CastlingMode::Standard).map_err(|err| {
            PyValueError::new_err(format!(
                "Unable to construct position from '{}': {err}",
                node.fen
            ))
        })?;

        let mut entries: Vec<MoveEntry> = Vec::with_capacity(node.moves.len());
        for move_text in node.moves {
            let uci = UciMove::from_str(&move_text).map_err(|err| {
                PyValueError::new_err(format!("Invalid UCI '{move_text}' for {}: {err}", node.fen))
            })?;

            let mv: Move = uci.to_move(&position).map_err(|_| {
                PyValueError::new_err(format!(
                    "Move '{move_text}' is illegal in position {}",
                    node.fen
                ))
            })?;

            let fingerprint = Fingerprint::from_move(&mv)?;
            let san = SanPlus::from_move(position.clone(), &mv).to_string();

            *frequencies.entry(fingerprint.clone()).or_insert(0) += 1;
            entries.push(MoveEntry {
                uci: move_text,
                san,
                fingerprint,
                frequency: 0,
            });
        }

        analyzed_nodes.push(NodeAnalysis {
            fen: node.fen,
            entries,
        });
    }

    for node in &mut analyzed_nodes {
        for entry in &mut node.entries {
            if let Some(count) = frequencies.get(&entry.fingerprint) {
                entry.frequency = *count;
            }
        }
        node.entries
            .sort_by(|a, b| b.frequency.cmp(&a.frequency).then(a.san.cmp(&b.san)));
    }

    let freq_payload = build_frequency_payload(py, &frequencies)?;
    let rankings_payload = build_rankings_payload(py, analyzed_nodes)?;
    Ok((freq_payload, rankings_payload))
}

#[pyfunction]
fn player_turn_mask(side_is_white: bool, fens: Vec<String>) -> PyResult<Vec<bool>> {
    let target_color = if side_is_white {
        Color::White
    } else {
        Color::Black
    };
    let mut mask: Vec<bool> = Vec::with_capacity(fens.len());
    for fen_text in fens {
        let fen = Fen::from_str(&fen_text).map_err(|err| {
            PyValueError::new_err(format!(
                "Invalid FEN '{}' while checking turn: {err}",
                fen_text
            ))
        })?;
        let position: Chess = fen.into_position(CastlingMode::Standard).map_err(|err| {
            PyValueError::new_err(format!(
                "Unable to construct position from '{}' while checking turn: {err}",
                fen_text
            ))
        })?;
        mask.push(position.turn() == target_color);
    }
    Ok(mask)
}

fn build_frequency_payload(
    py: Python<'_>,
    frequencies: &HashMap<Fingerprint, u32>,
) -> PyResult<Py<PyAny>> {
    let items = PyList::empty(py);
    for (fingerprint, count) in frequencies {
        let entry = PyDict::new(py);
        entry.set_item("piece", role_symbol(fingerprint.role))?;
        entry.set_item("from_square", square_name(fingerprint.from))?;
        entry.set_item("to_square", square_name(fingerprint.to))?;
        entry.set_item("frequency", count)?;
        items.append(entry)?;
    }
    Ok(items.into())
}

fn build_rankings_payload(
    py: Python<'_>,
    analyzed_nodes: Vec<NodeAnalysis>,
) -> PyResult<Py<PyAny>> {
    let rankings = PyDict::new(py);
    for node in analyzed_nodes {
        let moves = PyList::empty(py);
        for entry in node.entries {
            let payload = PyDict::new(py);
            payload.set_item("uci", entry.uci)?;
            payload.set_item("san", entry.san)?;
            payload.set_item("frequency", entry.frequency)?;
            moves.append(payload)?;
        }
        rankings.set_item(node.fen, moves)?;
    }
    Ok(rankings.into())
}

#[derive(FromPyObject)]
struct PyNodeInput {
    fen: String,
    moves: Vec<String>,
}

#[derive(Clone, Debug, Eq, Hash, PartialEq)]
struct Fingerprint {
    role: Role,
    from: Square,
    to: Square,
}

impl Fingerprint {
    fn from_move(mv: &Move) -> PyResult<Self> {
        let role = mv.role();
        let from = mv
            .from()
            .ok_or_else(|| PyValueError::new_err("Move lacks origin square"))?;
        let to = mv.to();
        Ok(Self { role, from, to })
    }
}

struct MoveEntry {
    uci: String,
    san: String,
    fingerprint: Fingerprint,
    frequency: u32,
}

struct NodeAnalysis {
    fen: String,
    entries: Vec<MoveEntry>,
}

fn role_symbol(role: Role) -> &'static str {
    match role {
        Role::Pawn => "P",
        Role::Knight => "N",
        Role::Bishop => "B",
        Role::Rook => "R",
        Role::Queen => "Q",
        Role::King => "K",
    }
}

fn square_name(square: Square) -> String {
    square.to_string()
}

#[derive(Clone, FromPyObject)]
struct SplitChildInput {
    uci: String,
    fen: String,
}

#[derive(Clone, FromPyObject)]
struct SplitNodeInput {
    fen: String,
    children: Vec<SplitChildInput>,
}

struct SplitEventPayload {
    fen: String,
    prefix: Vec<String>,
    move_count: u64,
}

#[pyfunction]
fn split_repertoire_nodes(
    root_fen: String,
    nodes: Vec<SplitNodeInput>,
    max_moves: u64,
) -> PyResult<Vec<(String, Vec<String>, u64)>> {
    let mut node_map: HashMap<String, SplitNodeInput> = HashMap::new();
    for node in nodes {
        Fen::from_str(&node.fen).map_err(|err| {
            PyValueError::new_err(format!("Invalid FEN '{}' in node list: {err}", node.fen))
        })?;
        node_map.insert(node.fen.clone(), node);
    }
    let max_moves = max_moves.max(1);
    let move_counts = compute_move_counts(&node_map)?;
    let mut prefix_moves: Vec<String> = Vec::new();
    let mut prefix_fens: HashSet<String> = HashSet::new();
    prefix_fens.insert(root_fen.clone());
    let mut events: Vec<SplitEventPayload> = Vec::new();
    split_node(
        &root_fen,
        &node_map,
        &move_counts,
        max_moves,
        &mut prefix_moves,
        &mut prefix_fens,
        &mut events,
    )?;
    Ok(events
        .into_iter()
        .map(|event| (event.fen, event.prefix, event.move_count))
        .collect())
}

fn split_node(
    fen: &str,
    nodes: &HashMap<String, SplitNodeInput>,
    move_counts: &HashMap<String, u64>,
    max_moves: u64,
    prefix_moves: &mut Vec<String>,
    prefix_fens: &mut HashSet<String>,
    events: &mut Vec<SplitEventPayload>,
) -> PyResult<()> {
    let node_children = nodes.get(fen);
    let mut sorted_children: Vec<&SplitChildInput> = Vec::new();
    if let Some(node) = node_children {
        sorted_children = sort_children(node)?;
    }
    let count = *move_counts.get(fen).unwrap_or(&0);
    if count <= max_moves || sorted_children.is_empty() {
        events.push(SplitEventPayload {
            fen: fen.to_string(),
            prefix: prefix_moves.clone(),
            move_count: count,
        });
        return Ok(());
    }

    for child in sorted_children {
        if prefix_fens.contains(&child.fen) {
            continue;
        }
        prefix_moves.push(child.uci.clone());
        prefix_fens.insert(child.fen.clone());
        split_node(
            &child.fen,
            nodes,
            move_counts,
            max_moves,
            prefix_moves,
            prefix_fens,
            events,
        )?;
        prefix_fens.remove(&child.fen);
        prefix_moves.pop();
    }
    Ok(())
}

fn sort_children(node: &SplitNodeInput) -> PyResult<Vec<&SplitChildInput>> {
    let fen = Fen::from_str(&node.fen).map_err(|err| {
        PyValueError::new_err(format!(
            "Invalid FEN '{}' while sorting children: {err}",
            node.fen
        ))
    })?;
    let position: Chess = fen.into_position(CastlingMode::Standard).map_err(|err| {
        PyValueError::new_err(format!(
            "Unable to construct position from '{}' while sorting children: {err}",
            node.fen
        ))
    })?;
    let mut decorated: Vec<(String, &SplitChildInput)> = Vec::with_capacity(node.children.len());
    for child in &node.children {
        let uci = UciMove::from_str(&child.uci).map_err(|err| {
            PyValueError::new_err(format!(
                "Invalid UCI '{}' for node {} while sorting children: {err}",
                child.uci, node.fen
            ))
        })?;
        let mv = uci.to_move(&position).map_err(|_| {
            PyValueError::new_err(format!(
                "Move '{}' is illegal in position {}",
                child.uci, node.fen
            ))
        })?;
        let san = SanPlus::from_move(position.clone(), &mv).to_string();
        decorated.push((san, child));
    }
    decorated.sort_by(|a, b| a.0.cmp(&b.0));
    Ok(decorated.into_iter().map(|(_, child)| child).collect())
}

fn compute_move_counts(nodes: &HashMap<String, SplitNodeInput>) -> PyResult<HashMap<String, u64>> {
    let mut memo: HashMap<String, u64> = HashMap::new();
    let mut visiting: HashSet<String> = HashSet::new();
    for fen in nodes.keys() {
        dfs_move_count(fen, nodes, &mut memo, &mut visiting)?;
    }
    Ok(memo)
}

fn dfs_move_count(
    fen: &str,
    nodes: &HashMap<String, SplitNodeInput>,
    memo: &mut HashMap<String, u64>,
    visiting: &mut HashSet<String>,
) -> PyResult<u64> {
    if let Some(value) = memo.get(fen) {
        return Ok(*value);
    }
    if !visiting.insert(fen.to_string()) {
        return Ok(0);
    }
    let mut total = 0u64;
    if let Some(node) = nodes.get(fen) {
        total += node.children.len() as u64;
        for child in &node.children {
            total += dfs_move_count(&child.fen, nodes, memo, visiting)?;
        }
    }
    visiting.remove(fen);
    memo.insert(fen.to_string(), total);
    Ok(total)
}

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::types::{PyDict, PyList};
    use shakmaty::{EnPassantMode, Position};
    use std::collections::HashMap;
    use std::sync::Once;

    const START_FEN: &str = "rnbqkbnr/pppppppp/8/8/8/8/PPPPPPPP/RNBQKBNR w KQkq - 0 1";

    fn initialize_python() {
        static INIT: Once = Once::new();
        INIT.call_once(|| {
            Python::initialize();
        });
    }

    fn node(fen: &str, moves: &[&str]) -> PyNodeInput {
        PyNodeInput {
            fen: fen.to_string(),
            moves: moves.iter().map(|m| m.to_string()).collect(),
        }
    }

    #[test]
    fn player_move_analysis_counts_frequencies() {
        initialize_python();
        Python::attach(|py| {
            let nodes = vec![
                node(START_FEN, &["g1f3", "e2e4"]),
                node(START_FEN, &["g1f3"]),
            ];

            let (freq_obj, rankings_obj) = player_move_analysis(py, nodes).unwrap();

            let freq_list = freq_obj
                .into_bound(py)
                .cast_into::<PyList>()
                .expect("frequency list");
            let mut freq_map = HashMap::new();
            for entry in freq_list.iter() {
                let entry = entry.cast::<PyDict>().expect("freq entry dict");
                let piece: String = entry
                    .get_item("piece")
                    .expect("piece lookup")
                    .expect("piece value")
                    .extract()
                    .expect("piece str");
                let from_sq: String = entry
                    .get_item("from_square")
                    .expect("from square lookup")
                    .expect("from square value")
                    .extract()
                    .expect("from str");
                let to_sq: String = entry
                    .get_item("to_square")
                    .expect("to square lookup")
                    .expect("to square value")
                    .extract()
                    .expect("to str");
                let key = format!("{}{}{}", piece, from_sq, to_sq);
                let count: u32 = entry
                    .get_item("frequency")
                    .expect("frequency lookup")
                    .expect("frequency value")
                    .extract()
                    .expect("count");
                freq_map.insert(key, count);
            }
            assert_eq!(freq_map.get("Ng1f3"), Some(&2));
            assert_eq!(freq_map.get("Pe2e4"), Some(&1));

            let rankings = rankings_obj
                .into_bound(py)
                .cast_into::<PyDict>()
                .expect("rankings dict");
            let moves = rankings
                .get_item(START_FEN)
                .expect("start fen lookup")
                .expect("start fen value")
                .cast_into::<PyList>()
                .expect("moves list");
            let first = moves
                .get_item(0)
                .expect("first move lookup")
                .cast_into::<PyDict>()
                .expect("first dict");
            assert_eq!(
                first
                    .get_item("uci")
                    .expect("uci lookup")
                    .expect("uci value")
                    .extract::<String>()
                    .expect("uci str"),
                "g1f3"
            );
            assert_eq!(
                first
                    .get_item("frequency")
                    .expect("freq lookup")
                    .expect("freq value")
                    .extract::<u32>()
                    .expect("freq int"),
                2
            );
        });
    }

    #[test]
    fn player_move_analysis_rejects_invalid_fen() {
        initialize_python();
        Python::attach(|py| {
            let nodes = vec![node("not a fen", &["e2e4"])];
            let err = player_move_analysis(py, nodes).unwrap_err();
            assert!(err.is_instance_of::<PyValueError>(py));
        });
    }

    #[test]
    fn player_move_analysis_rejects_invalid_uci() {
        initialize_python();
        Python::attach(|py| {
            let nodes = vec![node(START_FEN, &["badmove"])];
            let err = player_move_analysis(py, nodes).unwrap_err();
            assert!(err.is_instance_of::<PyValueError>(py));
        });
    }

    #[test]
    fn player_move_analysis_rejects_illegal_move() {
        initialize_python();
        Python::attach(|py| {
            let nodes = vec![node(START_FEN, &["e2e5"])];
            let err = player_move_analysis(py, nodes).unwrap_err();
            assert!(err.is_instance_of::<PyValueError>(py));
        });
    }

    #[test]
    fn player_turn_mask_identifies_player_nodes() {
        let post_white = next_fen(START_FEN, &["e2e4"]);
        let post_black = next_fen(&post_white, &["e7e5"]);
        let fens = vec![
            START_FEN.to_string(),
            post_white.clone(),
            post_black.clone(),
        ];
        let white_mask = player_turn_mask(true, fens.clone()).unwrap();
        assert_eq!(white_mask, vec![true, false, true]);
        let black_mask = player_turn_mask(false, fens).unwrap();
        assert_eq!(black_mask, vec![false, true, false]);
    }

    #[test]
    fn player_turn_mask_rejects_invalid_fen() {
        let err = player_turn_mask(true, vec!["bad fen".to_string()]).unwrap_err();
        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
        });
    }

    fn ensure_edge(
        map: &mut HashMap<String, SplitNodeInput>,
        from_fen: &str,
        uci: &str,
        to_fen: &str,
    ) {
        let entry = map
            .entry(from_fen.to_string())
            .or_insert_with(|| SplitNodeInput {
                fen: from_fen.to_string(),
                children: Vec::new(),
            });
        if entry
            .children
            .iter()
            .any(|child| child.uci == uci && child.fen == to_fen)
        {
            return;
        }
        entry.children.push(SplitChildInput {
            uci: uci.to_string(),
            fen: to_fen.to_string(),
        });
    }

    fn next_fen(start_fen: &str, moves: &[&str]) -> String {
        let fen = Fen::from_str(start_fen).unwrap();
        let mut position: Chess = fen.into_position(CastlingMode::Standard).unwrap();
        for mv in moves {
            let uci = UciMove::from_str(mv).unwrap();
            let chess_move = uci.to_move(&position).unwrap_or_else(|_| {
                let current = Fen::from_position(position.clone(), EnPassantMode::Legal);
                panic!("Illegal move {mv} from position {current}");
            });
            position.play_unchecked(&chess_move);
        }
        Fen::from_position(position, EnPassantMode::Legal).to_string()
    }

    fn build_shared_prefix_nodes() -> Vec<SplitNodeInput> {
        let common = ["e2e4", "e7e5", "g1f3", "b8c6", "b1c3", "g8f6"];
        let suffixes = ["f1b5", "f1c4", "f1e2", "d2d4", "f1d3", "g2g3", "h2h3"];
        let mut map: HashMap<String, SplitNodeInput> = HashMap::new();
        for suffix in suffixes {
            let mut path: Vec<&str> = Vec::new();
            path.extend_from_slice(&common);
            path.push(suffix);
            let mut current_fen = START_FEN.to_string();
            for &mv in &path {
                let next = next_fen(&current_fen, std::slice::from_ref(&mv));
                ensure_edge(&mut map, &current_fen, mv, &next);
                current_fen = next;
            }
        }
        map.into_values().collect()
    }

    #[test]
    fn split_repertoire_nodes_generates_expected_prefixes() {
        let nodes = build_shared_prefix_nodes();
        let events = split_repertoire_nodes(START_FEN.to_string(), nodes, 3).unwrap();
        assert_eq!(events.len(), 7);
        let mut seen_suffixes = std::collections::HashSet::new();
        for (_, prefix, _) in events {
            assert!(prefix.len() >= 6);
            let last = prefix.last().cloned().unwrap();
            seen_suffixes.insert(last);
        }
        let expected: std::collections::HashSet<String> =
            vec!["f1b5", "f1c4", "f1e2", "d2d4", "f1d3", "g2g3", "h2h3"]
                .into_iter()
                .map(String::from)
                .collect();
        assert_eq!(seen_suffixes, expected);
    }

    #[test]
    fn split_repertoire_nodes_rejects_invalid_fen() {
        let nodes = vec![SplitNodeInput {
            fen: "not a fen".to_string(),
            children: vec![SplitChildInput {
                uci: "e2e4".to_string(),
                fen: START_FEN.to_string(),
            }],
        }];
        let err = split_repertoire_nodes(START_FEN.to_string(), nodes, 5).unwrap_err();
        Python::attach(|py| {
            assert!(err.is_instance_of::<PyValueError>(py));
        });
    }

    #[test]
    fn split_repertoire_nodes_handles_cycles() {
        let mut map: HashMap<String, SplitNodeInput> = HashMap::new();
        let second_fen = next_fen(START_FEN, &["e2e4"]);
        ensure_edge(&mut map, START_FEN, "e2e4", &second_fen);
        ensure_edge(&mut map, &second_fen, "e7e5", START_FEN);
        let nodes: Vec<SplitNodeInput> = map.into_values().collect();
        let events = split_repertoire_nodes(START_FEN.to_string(), nodes, 1).unwrap();
        assert!(!events.is_empty());
    }
}
