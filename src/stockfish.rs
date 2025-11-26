use once_cell::sync::Lazy;
use pyo3::exceptions::PyRuntimeError;
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyList};
use std::collections::HashMap;
use std::io::{BufRead, BufReader, BufWriter, Write};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};

static STOCKFISH_POOLS: Lazy<Mutex<HashMap<PoolKey, Arc<StockfishPool>>>> =
    Lazy::new(|| Mutex::new(HashMap::new()));

#[derive(Hash, Eq, PartialEq, Clone)]
struct PoolKey {
    engine_path: String,
    depth: u32,
    multi_pv: u32,
    think_time_ms: Option<u64>,
    pool_size: usize,
}

#[pyfunction]
pub fn stockfish_evaluate(
    py: Python<'_>,
    fen: String,
    engine_path: String,
    depth: u32,
    multi_pv: u32,
    think_time: Option<f64>,
    pool_size: usize,
) -> PyResult<Py<PyAny>> {
    let think_time_ms = think_time.and_then(|secs| {
        if secs <= 0.0 {
            None
        } else {
            Some((secs * 1000.0).round().clamp(1.0, f64::MAX) as u64)
        }
    });
    let key = PoolKey {
        engine_path: engine_path.clone(),
        depth,
        multi_pv,
        think_time_ms,
        pool_size: pool_size.max(1),
    };
    let pool = get_or_create_pool(&key)?;
    let payload = pool.evaluate(&fen)?;
    payload.to_pydict(py)
}

fn get_or_create_pool(key: &PoolKey) -> PyResult<Arc<StockfishPool>> {
    let mut registry = STOCKFISH_POOLS.lock().unwrap();
    if let Some(pool) = registry.get(key) {
        return Ok(pool.clone());
    }
    let pool = Arc::new(StockfishPool::new(key)?);
    registry.insert(key.clone(), pool.clone());
    Ok(pool)
}

struct StockfishPool {
    workers: Vec<Arc<Mutex<StockfishWorker>>>,
    next: AtomicUsize,
    key: PoolKey,
}

impl StockfishPool {
    fn new(key: &PoolKey) -> PyResult<Self> {
        let worker_count = key.pool_size.max(1);
        let mut workers = Vec::with_capacity(worker_count);
        for _ in 0..worker_count {
            workers.push(Arc::new(Mutex::new(StockfishWorker::spawn(
                &key.engine_path,
                key.multi_pv,
            )?)));
        }
        Ok(Self {
            workers,
            next: AtomicUsize::new(0),
            key: key.clone(),
        })
    }

    fn evaluate(&self, fen: &str) -> PyResult<EvalPayload> {
        let idx = self.next.fetch_add(1, Ordering::SeqCst) % self.workers.len().max(1);
        let worker_arc = self.workers[idx].clone();
        let mut worker = worker_arc.lock().unwrap();
        worker.evaluate(fen, &self.key)
    }
}

struct StockfishWorker {
    io: Box<dyn EngineIo + Send>,
}

impl StockfishWorker {
    fn spawn(engine_path: &str, multi_pv: u32) -> PyResult<Self> {
        let io = ProcessIo::spawn(engine_path).map_err(|err| {
            PyRuntimeError::new_err(format!(
                "Unable to launch Stockfish at '{}': {err}",
                engine_path
            ))
        })?;
        let mut worker = Self { io: Box::new(io) };
        worker.initialize(multi_pv)?;
        Ok(worker)
    }

    fn initialize(&mut self, multi_pv: u32) -> PyResult<()> {
        self.send_line("uci")?;
        self.wait_for("uciok")?;
        self.send_line(&format!("setoption name MultiPV value {}", multi_pv))?;
        self.send_line("isready")?;
        self.wait_for("readyok")
    }

    fn evaluate(&mut self, fen: &str, key: &PoolKey) -> PyResult<EvalPayload> {
        self.send_line("ucinewgame")?;
        self.send_line(&format!("position fen {}", fen))?;
        self.send_line(&self.go_command(key))?;
        let mut parser = InfoParser::new();
        loop {
            let line = self.read_line().map_err(|err| {
                PyRuntimeError::new_err(format!("Stockfish terminated unexpectedly: {err}"))
            })?;
            if line.starts_with("info ") {
                parser.consume(&line);
            } else if line.starts_with("bestmove") {
                break;
            }
        }
        parser.into_payload(fen)
    }

    fn go_command(&self, key: &PoolKey) -> String {
        if let Some(ms) = key.think_time_ms {
            format!("go movetime {}", ms)
        } else {
            format!("go depth {}", key.depth)
        }
    }

    fn send_line(&mut self, line: &str) -> PyResult<()> {
        self.io.write_line(line).map_err(|err| {
            PyRuntimeError::new_err(format!("Failed to communicate with Stockfish: {err}"))
        })
    }

    fn read_line(&mut self) -> std::io::Result<String> {
        self.io.read_line()
    }

    fn wait_for(&mut self, needle: &str) -> PyResult<()> {
        loop {
            let line = self.read_line().map_err(|err| {
                PyRuntimeError::new_err(format!("Error waiting for '{}': {err}", needle))
            })?;
            if line.contains(needle) {
                return Ok(());
            }
        }
    }
}

impl Drop for StockfishWorker {
    fn drop(&mut self) {
        self.io.shutdown();
    }
}

trait EngineIo {
    fn write_line(&mut self, line: &str) -> std::io::Result<()>;
    fn read_line(&mut self) -> std::io::Result<String>;
    fn shutdown(&mut self);
}

struct ProcessIo {
    child: Child,
    stdin: BufWriter<ChildStdin>,
    stdout: BufReader<ChildStdout>,
}

impl ProcessIo {
    fn spawn(engine_path: &str) -> std::io::Result<Self> {
        let mut child = Command::new(engine_path)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| std::io::Error::other("missing stdin"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| std::io::Error::other("missing stdout"))?;
        Ok(Self {
            child,
            stdin: BufWriter::new(stdin),
            stdout: BufReader::new(stdout),
        })
    }
}

impl EngineIo for ProcessIo {
    fn write_line(&mut self, line: &str) -> std::io::Result<()> {
        self.stdin.write_all(line.as_bytes())?;
        self.stdin.write_all(b"\n")?;
        self.stdin.flush()
    }

    fn read_line(&mut self) -> std::io::Result<String> {
        let mut buf = String::new();
        let bytes = self.stdout.read_line(&mut buf)?;
        if bytes == 0 {
            return Err(std::io::Error::new(
                std::io::ErrorKind::UnexpectedEof,
                "Stockfish closed pipe",
            ));
        }
        Ok(buf)
    }

    fn shutdown(&mut self) {
        let _ = self.write_line("quit");
        let _ = self.child.wait();
    }
}

struct InfoParser {
    depth: u32,
    nodes: u64,
    entries: HashMap<u32, PvEntry>,
}

impl InfoParser {
    fn new() -> Self {
        Self {
            depth: 0,
            nodes: 0,
            entries: HashMap::new(),
        }
    }

    fn consume(&mut self, line: &str) {
        let mut tokens = line.split_whitespace();
        let mut current_multipv = 1;
        let mut cp: Option<i32> = None;
        let mut mate: Option<i32> = None;
        while let Some(token) = tokens.next() {
            match token {
                "depth" => {
                    if let Some(parsed) = tokens.next().and_then(|value| value.parse::<u32>().ok())
                    {
                        self.depth = parsed;
                    }
                }
                "nodes" => {
                    if let Some(parsed) = tokens.next().and_then(|value| value.parse::<u64>().ok())
                    {
                        self.nodes = parsed;
                    }
                }
                "multipv" => {
                    if let Some(parsed) = tokens.next().and_then(|value| value.parse::<u32>().ok())
                    {
                        current_multipv = parsed.max(1);
                    }
                }
                "score" => {
                    if let Some(kind) = tokens.next() {
                        match kind {
                            "cp" => {
                                cp = tokens.next().and_then(|value| value.parse::<i32>().ok());
                                mate = None;
                            }
                            "mate" => {
                                mate = tokens.next().and_then(|value| value.parse::<i32>().ok());
                                cp = None;
                            }
                            _ => {}
                        }
                    }
                }
                "pv" => {
                    let moves: Vec<String> = tokens.map(|mv| mv.to_string()).collect();
                    if !moves.is_empty() {
                        self.entries
                            .insert(current_multipv, PvEntry { cp, mate, moves });
                    }
                    break;
                }
                _ => {}
            }
        }
    }

    fn into_payload(self, fen: &str) -> PyResult<EvalPayload> {
        let mut entries: Vec<(u32, PvEntry)> = self.entries.into_iter().collect();
        entries.sort_by_key(|(multipv, _)| *multipv);
        Ok(EvalPayload {
            fen: fen.to_string(),
            depth: self.depth,
            knodes: self.nodes / 1000,
            pvs: entries.into_iter().map(|(_, entry)| entry).collect(),
        })
    }
}

struct PvEntry {
    cp: Option<i32>,
    mate: Option<i32>,
    moves: Vec<String>,
}

struct EvalPayload {
    fen: String,
    depth: u32,
    knodes: u64,
    pvs: Vec<PvEntry>,
}

impl EvalPayload {
    fn to_pydict(&self, py: Python<'_>) -> PyResult<Py<PyAny>> {
        let dict = PyDict::new(py);
        dict.set_item("fen", &self.fen)?;
        dict.set_item("depth", self.depth)?;
        dict.set_item("knodes", self.knodes)?;
        let pv_list = PyList::empty(py);
        for entry in &self.pvs {
            let pv_dict = PyDict::new(py);
            if let Some(cp) = entry.cp {
                pv_dict.set_item("cp", cp)?;
                pv_dict.set_item("score", cp)?;
            }
            if let Some(mate) = entry.mate {
                pv_dict.set_item("mate", mate)?;
                if entry.cp.is_none() {
                    pv_dict.set_item("score", mate)?;
                }
            }
            pv_dict.set_item("moves", entry.moves.join(" "))?;
            pv_list.append(pv_dict)?;
        }
        dict.set_item("pvs", pv_list)?;
        Ok(dict.into())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::Mutex as StdMutex;

    struct MockIo {
        writes: Arc<StdMutex<Vec<String>>>,
        reads: Vec<String>,
    }

    impl MockIo {
        fn new(responses: Vec<&str>) -> Self {
            Self {
                writes: Arc::new(StdMutex::new(Vec::new())),
                reads: responses.into_iter().map(|s| format!("{s}\n")).collect(),
            }
        }

        fn writes(&self) -> Arc<StdMutex<Vec<String>>> {
            self.writes.clone()
        }
    }

    impl EngineIo for MockIo {
        fn write_line(&mut self, line: &str) -> std::io::Result<()> {
            self.writes.lock().unwrap().push(line.to_string());
            Ok(())
        }

        fn read_line(&mut self) -> std::io::Result<String> {
            if self.reads.is_empty() {
                return Err(std::io::Error::new(
                    std::io::ErrorKind::UnexpectedEof,
                    "no more lines",
                ));
            }
            Ok(self.reads.remove(0))
        }

        fn shutdown(&mut self) {}
    }

    impl StockfishWorker {
        fn with_io(io: Box<dyn EngineIo + Send>) -> Self {
            Self { io }
        }
    }

    #[test]
    fn parser_collects_multiple_pvs() {
        let mut parser = InfoParser::new();
        parser.consume("info depth 10 nodes 100000 multipv 1 score cp 50 pv e2e4 e7e5");
        parser.consume("info depth 10 nodes 100000 multipv 2 score cp 30 pv d2d4 d7d5");
        let payload = parser.into_payload("fen").unwrap();
        assert_eq!(payload.pvs.len(), 2);
        assert_eq!(payload.depth, 10);
        assert_eq!(payload.knodes, 100);
    }

    #[test]
    fn worker_emits_expected_commands() {
        let mock = MockIo::new(vec![
            "uciok",
            "readyok",
            "info depth 8 nodes 50000 multipv 1 score cp 15 pv e2e4 e7e5",
            "bestmove e2e4",
        ]);
        let writes_handle = mock.writes();
        let mut worker = StockfishWorker::with_io(Box::new(mock));
        worker.initialize(2).unwrap();
        let key = PoolKey {
            engine_path: "engine".into(),
            depth: 12,
            multi_pv: 2,
            think_time_ms: None,
            pool_size: 1,
        };
        let payload = worker.evaluate("fen", &key).unwrap();
        assert_eq!(payload.pvs.len(), 1);
        let writes = writes_handle.lock().unwrap();
        assert_eq!(writes[0], "uci");
        assert!(writes.iter().any(|cmd| cmd.starts_with("position fen")));
        assert!(writes.iter().any(|cmd| cmd.starts_with("go depth")));
    }
}
