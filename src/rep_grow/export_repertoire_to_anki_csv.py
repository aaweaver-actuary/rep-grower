from __future__ import annotations

import csv
from pathlib import Path
from typing import Iterable, Sequence

import chess
import click

from .fen import canonical_fen
from .repertoire import Repertoire, RepertoireNode


def _sorted_children(
    node: RepertoireNode, board: chess.Board
) -> list[tuple[str, chess.Move, RepertoireNode]]:
    decorated: list[tuple[str, chess.Move, RepertoireNode]] = []
    for move_uci, child in node.children.items():
        move = chess.Move.from_uci(move_uci)
        try:
            san = board.san(move)
        except ValueError:
            san = move_uci
        decorated.append((san, move, child))
    decorated.sort(key=lambda item: item[0])
    return decorated


def _collect_san_lines(
    repertoire: Repertoire,
) -> list[tuple[list[str], RepertoireNode]]:
    """Return SAN move lists from root to every leaf, paired with the leaf node."""

    root_fen = canonical_fen(repertoire.root_node.fen)
    root_board = chess.Board(root_fen)
    lines: list[tuple[list[str], RepertoireNode]] = []

    def dfs(
        node: RepertoireNode,
        board: chess.Board,
        san_moves: list[str],
        visited: set[str],
    ) -> None:
        children = _sorted_children(node, board)
        if not children:
            lines.append((list(san_moves), node))
            return
        for san, move, child in children:
            if child.fen in visited:
                continue
            next_board = board.copy(stack=False)
            if move not in next_board.legal_moves:
                continue
            next_board.push(move)
            dfs(child, next_board, san_moves + [san], visited | {child.fen})

    dfs(repertoire.root_node, root_board, [], {root_fen})
    return lines


def _format_description(
    moves: Sequence[str], headers: dict[str, str], description_plies: int
) -> str:
    variation = headers.get("Variation")
    if variation and variation != "?":
        return variation
    eco = headers.get("ECO")
    if eco and eco != "?":
        return eco
    if description_plies < 1:
        return ""
    return " ".join(moves[:description_plies])


def _build_rows(
    repertoire: Repertoire,
    *,
    max_plies: int | None,
    min_plies: int | None,
    description_plies: int,
    dedupe: bool,
    include_games_reached: bool,
    sort_by_games_reached: bool,
) -> list[list[str]]:
    root_fen = canonical_fen(repertoire.root_node.fen)
    san_lines = _collect_san_lines(repertoire)
    headers = {str(key): str(value) for key, value in repertoire.game.headers.items()}

    seen_moves: set[str] = set()
    rows: list[tuple[str, str, int]] = []
    min_required = max(min_plies or 0, 0)

    for san_moves, leaf_node in san_lines:
        limited_moves = san_moves[:max_plies] if max_plies else san_moves
        if len(limited_moves) < min_required:
            continue
        moves_str = " ".join(limited_moves)
        if not moves_str:
            continue
        if dedupe:
            if moves_str in seen_moves:
                continue
            seen_moves.add(moves_str)
        description = _format_description(
            limited_moves,
            headers=headers,
            description_plies=description_plies,
        )
        games_reached = leaf_node.games_reached or 0
        rows.append((description, moves_str, games_reached))

    if sort_by_games_reached:
        rows.sort(key=lambda item: item[2], reverse=True)

    final_rows: list[list[str]] = []
    for idx, (description, moves_str, games_reached) in enumerate(rows, start=1):
        row = [str(idx), description, root_fen, moves_str]
        if include_games_reached:
            row.append(str(games_reached))
        final_rows.append(row)

    return final_rows


def _write_rows(rows: Iterable[list[str]], output_path: Path) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle, quoting=csv.QUOTE_ALL)
        writer.writerows(rows)


def _chunk_paths(base_path: Path, chunks: int) -> list[Path]:
    stem = base_path.stem or "anki_repertoire"
    suffix = base_path.suffix or ".csv"
    return [
        base_path.with_name(f"{stem}_part{index}{suffix}")
        for index in range(1, chunks + 1)
    ]


@click.command()
@click.option(
    "--pgn-file",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    required=True,
    help="PGN file containing the repertoire to export.",
)
@click.option(
    "--side",
    type=click.Choice(["white", "black"], case_sensitive=False),
    required=True,
    help="Player side for repertoire context.",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=str),
    default="anki_repertoire.csv",
    show_default=True,
    help="Destination CSV path (QUOTE_ALL).",
)
@click.option(
    "--max-plies",
    type=int,
    default=None,
    help="Cap move sequences to this many plies (half-moves).",
    show_default=False,
)
@click.option(
    "--min-plies",
    type=int,
    default=None,
    help="Skip lines shorter than this many plies after capping.",
    show_default=False,
)
@click.option(
    "--description-plies",
    type=int,
    default=8,
    show_default=True,
    help="Number of SAN tokens to include in the Description column.",
)
@click.option(
    "--dedupe",
    is_flag=True,
    default=False,
    help="Drop duplicate SAN lines (after applying max/min plies).",
)
@click.option(
    "--include-games-reached",
    is_flag=True,
    default=False,
    help="Append a GamesReached column sourced from PGN reach-count tags.",
)
@click.option(
    "--sort-by-games-reached",
    is_flag=True,
    default=False,
    help="Sort rows descending by GamesReached (defaults to 0 when absent).",
)
@click.option(
    "--chunk-size",
    type=int,
    default=None,
    help="Write multiple CSV files with this many rows per chunk.",
    show_default=False,
)
def click_main(
    pgn_file: str,
    side: str,
    output_path: str,
    max_plies: int | None,
    min_plies: int | None,
    description_plies: int,
    dedupe: bool,
    include_games_reached: bool,
    sort_by_games_reached: bool,
    chunk_size: int | None,
) -> None:
    """Export a repertoire PGN to an Anki-ready CSV: PuzzleID, Description, FEN, Moves."""

    if max_plies is not None and max_plies < 1:
        raise click.BadParameter(
            "--max-plies must be positive", param_hint="--max-plies"
        )
    if min_plies is not None and min_plies < 0:
        raise click.BadParameter(
            "--min-plies cannot be negative", param_hint="--min-plies"
        )
    if max_plies is not None and min_plies is not None and min_plies > max_plies:
        raise click.BadParameter(
            "--min-plies cannot exceed --max-plies",
            param_hint="--min-plies",
        )
    if description_plies < 0:
        raise click.BadParameter(
            "--description-plies cannot be negative",
            param_hint="--description-plies",
        )
    if chunk_size is not None and chunk_size < 1:
        raise click.BadParameter(
            "--chunk-size must be positive", param_hint="--chunk-size"
        )

    side_color = chess.WHITE if side.lower() == "white" else chess.BLACK
    repertoire = Repertoire.from_pgn_file(side=side_color, pgn_path=pgn_file)

    rows = _build_rows(
        repertoire,
        max_plies=max_plies,
        min_plies=min_plies,
        description_plies=description_plies,
        dedupe=dedupe,
        include_games_reached=include_games_reached,
        sort_by_games_reached=sort_by_games_reached,
    )

    if not rows:
        click.echo("No lines to export; repertoire may be empty.", err=True)

    base_path = Path(output_path)
    if chunk_size is None or len(rows) <= chunk_size:
        _write_rows(rows, base_path)
        click.echo(f"Wrote {len(rows)} rows to {base_path}")
        return

    chunk_paths = _chunk_paths(base_path, (len(rows) + chunk_size - 1) // chunk_size)
    for index, start in enumerate(range(0, len(rows), chunk_size)):
        end = start + chunk_size
        chunk = rows[start:end]
        path = chunk_paths[index]
        _write_rows(chunk, path)
    written = ", ".join(str(path) for path in chunk_paths)
    click.echo(f"Wrote {len(rows)} rows across {len(chunk_paths)} files: {written}")


def main() -> None:
    click_main(standalone_mode=True)


if __name__ == "__main__":
    main()
