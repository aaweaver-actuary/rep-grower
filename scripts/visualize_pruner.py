from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Iterable

import chess
from pyvis.network import Network

from rep_grow.repertoire import canonical_fen


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Visualize a pruner report as an interactive PyVis graph with edge"
            " weights based on move frequencies."
        )
    )
    parser.add_argument(
        "report",
        type=Path,
        help="Path to the JSON report (e.g. target/pruner_reports/...json)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("pruner_report.html"),
        help="HTML file to write (default: pruner_report.html)",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=8,
        help="Only draw nodes up to this ply depth (default: 8)",
    )
    parser.add_argument(
        "--min-frequency",
        type=int,
        default=1,
        help="Skip moves with frequency below this threshold (default: 1)",
    )
    parser.add_argument(
        "--frequency-scale",
        type=float,
        default=75.0,
        help="Divisor used to convert frequency into edge width (default: 75)",
    )
    parser.add_argument(
        "--include-alternatives",
        action="store_true",
        help="Include non-selected candidate moves as gray edges",
    )
    parser.add_argument(
        "--max-alternatives",
        type=int,
        default=3,
        help="Maximum alternative edges per node when included (default: 3)",
    )
    parser.add_argument(
        "--height",
        default="900px",
        help="Viewport height for the output graph (default: 900px)",
    )
    return parser.parse_args()


def readable_path(path: Iterable[str]) -> str:
    moves = list(path)
    return " ".join(moves) if moves else "<root>"


def node_label(path: list[str]) -> str:
    if not path:
        return "Start"
    if len(path) == 1:
        return path[0]
    return f"{path[-2]}→{path[-1]}"


def board_for_path(path: list[str]) -> chess.Board:
    board = chess.Board()
    for san in path:
        move = board.parse_san(san)
        board.push(move)
    return board


def add_node_if_missing(
    net: Network,
    added: set[str],
    node_id: str,
    label: str,
    title: str,
    degree: int,
) -> None:
    if node_id in added:
        return
    net.add_node(
        node_id,
        label=label,
        title=title,
        size=12 + min(degree, 6),
    )
    added.add(node_id)


def build_graph(data: dict, args: argparse.Namespace) -> Network:
    net = Network(height=args.height, width="100%", directed=True)
    net.toggle_physics(True)
    net.barnes_hut(gravity=-3000, central_gravity=0.3, spring_length=120)
    added_nodes: set[str] = set()
    node_titles: dict[str, str] = {}
    node_degrees: defaultdict[str, int] = defaultdict(int)

    for entry in data.get("selections", []):
        path = entry["path"]
        depth = len(path)
        if depth >= args.max_depth:
            continue

        candidates = [(entry["selected_move"], True)]
        candidates.extend((alt, False) for alt in entry.get("alternatives", []))

        filtered: list[tuple[dict, bool]] = []
        alternative_count = 0
        for move_data, is_selected in candidates:
            if not is_selected and not args.include_alternatives:
                continue
            if move_data["frequency"] < args.min_frequency:
                continue
            filtered.append((move_data, is_selected))
            if not is_selected:
                alternative_count += 1
                if alternative_count >= args.max_alternatives:
                    break

        if not filtered:
            continue

        parent_board = board_for_path(path)
        parent_fen = canonical_fen(parent_board.fen())
        parent_label = node_label(path)
        parent_title = readable_path(path)
        node_titles.setdefault(parent_fen, parent_title)
        node_degrees[parent_fen] += len(filtered)
        add_node_if_missing(
            net,
            added_nodes,
            parent_fen,
            parent_label,
            node_titles[parent_fen],
            degree=node_degrees[parent_fen],
        )

        for move_data, is_selected in filtered:
            child_path = [*path, move_data["san"]]
            if len(child_path) > args.max_depth:
                continue
            try:
                move = parent_board.parse_san(move_data["san"])
            except ValueError:
                move = chess.Move.from_uci(move_data["uci"])
            child_board = parent_board.copy(stack=False)
            child_board.push(move)
            child_fen = canonical_fen(child_board.fen())

            node_titles.setdefault(child_fen, readable_path(child_path))
            node_degrees[child_fen] += 1
            add_node_if_missing(
                net,
                added_nodes,
                child_fen,
                node_label(child_path),
                node_titles[child_fen],
                degree=node_degrees[child_fen],
            )

            edge_color = "#2ecc71" if is_selected else "#b0b0b0"
            width = max(1.0, move_data["frequency"] / args.frequency_scale)
            title = (
                f"{move_data['san']} ({move_data['frequency']} positions)\n"
                f"From: {readable_path(path)}"
            )
            net.add_edge(
                parent_fen,
                child_fen,
                title=title,
                color=edge_color,
                width=width,
                value=move_data["frequency"],
            )

    highlight_options = {
        "nodes": {"font": {"color": "#f1f1f1"}},
        "edges": {"smooth": {"enabled": False}},
        "physics": {
            "barnesHut": {
                "gravitationalConstant": -3000,
                "springLength": 120,
            }
        },
    }
    net.set_options(json.dumps(highlight_options))
    return net


def main() -> None:
    args = parse_args()
    data = json.loads(args.report.read_text())
    net = build_graph(data, args)
    net.show(str(args.output), notebook=False)
    print(f"Wrote visualization to {args.output}")


if __name__ == "__main__":
    main()
