use std::collections::HashMap;
use std::fs;

use anyhow::{Context, anyhow};
use chrono::Utc;
use clap::{Parser, ValueEnum};
use serde::Serialize;
use shakmaty::fen::Fen;
use shakmaty::san::SanPlus;
use shakmaty::uci::UciMove;
use shakmaty::{CastlingMode, Chess, Color, EnPassantMode, Move, Position};

use _core::canonicalize_fen_str;

#[derive(Parser, Debug)]
#[command(name = "freq", about = "Compute move frequencies for a repertoire PGN")]
struct Args {
    /// PGN file containing the repertoire
    pgn_file: String,

    /// Player side whose move frequencies should be analyzed
    #[arg(long, value_enum, default_value_t = Side::White)]
    side: Side,

    /// Destination JSON file (use '-' for stdout)
    #[arg(long, default_value = "-")]
    output: String,

    /// Number of spaces to indent JSON (0 for compact)
    #[arg(long, default_value_t = 2)]
    indent: u16,
}

#[derive(Copy, Clone, Debug, ValueEnum)]
enum Side {
    White,
    Black,
}

#[derive(Clone, Debug, Eq, PartialEq, Hash)]
struct Fingerprint {
    piece: char,
    from: String,
    to: String,
}

#[derive(Serialize)]
struct RankedMove {
    uci: String,
    san: String,
    frequency: u32,
}

#[derive(Serialize)]
struct Payload {
    generated_at: String,
    side: String,
    total_nodes: usize,
    rankings: HashMap<String, Vec<RankedMove>>,
}

fn main() -> anyhow::Result<()> {
    let args = Args::parse();

    let pgn_text = fs::read_to_string(&args.pgn_file)
        .with_context(|| format!("Failed to read PGN file: {}", args.pgn_file))?;
    let mainline = parse_mainline_san(&pgn_text)?;

    let side_color = match args.side {
        Side::White => Color::White,
        Side::Black => Color::Black,
    };

    let (rankings, total_nodes) = build_rankings(&mainline, side_color)?;

    let payload = Payload {
        generated_at: Utc::now().to_rfc3339(),
        side: match args.side {
            Side::White => "white".to_string(),
            Side::Black => "black".to_string(),
        },
        total_nodes,
        rankings,
    };

    let indent = args.indent as usize;
    let json = if indent == 0 {
        serde_json::to_string(&payload)?
    } else {
        serde_json::to_string_pretty(&payload)?
    };

    if args.output == "-" {
        println!("{}", json);
    } else {
        std::fs::write(&args.output, json + "\n")?;
        println!("Wrote frequency map to {}", args.output);
    }

    Ok(())
}

fn parse_mainline_san(text: &str) -> anyhow::Result<Vec<SanPlus>> {
    let mut sans: Vec<SanPlus> = Vec::new();
    let mut variation_depth: i32 = 0;
    for raw in text.split_whitespace() {
        if raw.starts_with('[')
            || raw.starts_with('{')
            || raw.ends_with(']')
            || raw.starts_with('"')
        {
            continue;
        }

        let open = raw.matches('(').count() as i32;
        let close = raw.matches(')').count() as i32;
        variation_depth += open;

        if variation_depth > 0 {
            variation_depth -= close;
            continue;
        }

        variation_depth = (variation_depth - close).max(0);

        let token = raw.trim_matches(|c| c == '(' || c == ')');
        if token.is_empty() {
            continue;
        }
        if token.contains('.') {
            continue;
        }
        if matches!(token, "*" | "1-0" | "0-1" | "1/2-1/2") {
            break;
        }
        if token.starts_with('$') {
            continue;
        }
        let san = SanPlus::from_ascii(token.as_bytes())
            .with_context(|| format!("Invalid SAN token in PGN: {token}"))?;
        sans.push(san);
    }
    Ok(sans)
}

fn build_rankings(
    mainline: &[SanPlus],
    player_side: Color,
) -> anyhow::Result<(HashMap<String, Vec<RankedMove>>, usize)> {
    let mut position = Chess::new();
    let mut nodes: HashMap<String, Vec<(Move, String, String)>> = HashMap::new();
    let mut frequencies: HashMap<Fingerprint, u32> = HashMap::new();

    let root_fen = canonicalize_current_fen(&position)?;
    nodes.entry(root_fen.clone()).or_default();

    for san in mainline {
        let mv = san.san.to_move(&position)?;
        let parent_fen = canonicalize_current_fen(&position)?;
        let san_str = san.to_string();
        let uci = UciMove::from_move(&mv, CastlingMode::Standard).to_string();

        if position.turn() == player_side {
            let fp = Fingerprint::from_move(&mv)?;
            *frequencies.entry(fp).or_insert(0) += 1;
        }

        position = position.play(&mv)?;
        let child_fen = canonicalize_current_fen(&position)?;
        nodes
            .entry(parent_fen)
            .or_default()
            .push((mv.clone(), uci, san_str));
        nodes.entry(child_fen).or_default();
    }

    let mut rankings: HashMap<String, Vec<RankedMove>> = HashMap::new();
    let mut total_nodes = 0usize;
    for (fen, moves) in nodes {
        let board: Chess =
            Fen::from_ascii(fen.as_bytes())?.into_position(CastlingMode::Standard)?;
        if board.turn() != player_side {
            continue;
        }
        total_nodes += 1;
        let mut ranked: Vec<RankedMove> = Vec::new();
        for (mv, uci, san) in moves {
            let fp = Fingerprint::from_move(&mv)?;
            let freq = *frequencies.get(&fp).unwrap_or(&0);
            ranked.push(RankedMove {
                uci,
                san,
                frequency: freq,
            });
        }
        ranked.sort_by(|a, b| b.frequency.cmp(&a.frequency).then(a.san.cmp(&b.san)));
        rankings.insert(fen, ranked);
    }

    Ok((rankings, total_nodes))
}

impl Fingerprint {
    fn from_move(mv: &Move) -> anyhow::Result<Self> {
        let role = mv.role();
        let from_sq = mv
            .from()
            .ok_or_else(|| anyhow::anyhow!("Move lacks origin square"))?;
        let to_sq = mv.to();
        Ok(Fingerprint {
            piece: role.char().to_ascii_uppercase(),
            from: from_sq.to_string(),
            to: to_sq.to_string(),
        })
    }
}

fn canonicalize_current_fen(board: &Chess) -> anyhow::Result<String> {
    let fen = Fen::from_position(board.clone(), EnPassantMode::Legal).to_string();
    canonicalize_fen_str(&fen).map_err(|err| anyhow!(err))
}
