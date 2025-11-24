from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import chess
import click

from .repertoire import Repertoire


@click.command()
@click.argument(
    "pgn_file",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
)
@click.option(
    "--side",
    type=click.Choice(["white", "black"], case_sensitive=False),
    default="white",
    show_default=True,
    help="Player side whose move frequencies should be analyzed",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=str),
    default="-",
    show_default=True,
    help="Destination file for JSON output (default: stdout)",
)
@click.option(
    "--indent",
    type=int,
    default=2,
    show_default=True,
    help="Number of spaces for JSON indentation (use 0 for compact)",
)
def click_main(pgn_file: str, side: str, output: str, indent: int) -> None:
    """Print the per-node move frequency map for a repertoire PGN."""

    side_color = chess.WHITE if side.lower() == "white" else chess.BLACK
    repertoire = Repertoire.from_pgn_file(side=side_color, pgn_path=pgn_file)
    rankings = repertoire.player_move_rankings()
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "side": side.lower(),
        "total_nodes": len(rankings),
        "rankings": rankings,
    }
    indent_value: int | None
    if indent <= 0:
        indent_value = None
    else:
        indent_value = indent
    data = json.dumps(payload, indent=indent_value)

    if output == "-":
        click.echo(data)
    else:
        path = Path(output).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data + "\n", encoding="utf-8")
        click.echo(f"Wrote frequency map to {path}")


def main() -> None:
    click_main(standalone_mode=True)


if __name__ == "__main__":
    main()
