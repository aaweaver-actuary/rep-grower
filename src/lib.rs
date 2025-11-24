use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use pyo3::{Bound, FromPyObject};

use shakmaty::fen::Fen;
use shakmaty::san::SanPlus;
use shakmaty::uci::UciMove;
use shakmaty::{CastlingMode, Chess, Move, Role, Square};

use std::collections::HashMap;
use std::str::FromStr;

/// A Python module implemented in Rust.
#[pymodule]
fn _core(_py: Python<'_>, m: Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(player_move_analysis, &m)?)?;
    Ok(())
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

#[cfg(test)]
mod tests {
    use super::*;
    use pyo3::types::{PyDict, PyList};
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
}
