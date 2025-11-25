from __future__ import annotations

import chess
import click

from .repertoire import Repertoire
from .repertoire_splitter import RepertoireSplitter


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
    help="Player side for repertoire context",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=str),
    required=True,
    help="Destination PGN file containing the split games",
)
@click.option(
    "--max-moves",
    type=int,
    default=1000,
    show_default=True,
    help="Maximum number of moves per split game",
)
@click.option(
    "--trim-event-prefix/--no-trim-event-prefix",
    default=False,
    help="Shorten event headers by removing the shared opening prefix",
)
def click_main(
    pgn_file: str,
    side: str,
    output_path: str,
    max_moves: int,
    trim_event_prefix: bool,
) -> None:
    """Split a repertoire PGN into multiple games with bounded move counts."""

    side_color = chess.WHITE if side.lower() == "white" else chess.BLACK
    repertoire = Repertoire.from_pgn_file(side=side_color, pgn_path=pgn_file)
    splitter = RepertoireSplitter(repertoire)
    events = splitter.split_events(max_moves=max_moves)
    event_names = splitter.compact_event_names(events) if trim_event_prefix else None
    splitter.write_events(events, output_path=output_path, event_names=event_names)

    click.echo(
        f"Created {len(events)} games at {output_path} (max {max_moves} moves each)"
    )


def main() -> None:
    click_main(standalone_mode=True)


if __name__ == "__main__":
    main()
