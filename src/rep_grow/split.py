from __future__ import annotations

import chess
import click

from .cli_options import SplitOptions
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
    options = SplitOptions(
        pgn_file=pgn_file,
        side=side,
        output_path=output_path,
        max_moves=max_moves,
        trim_event_prefix=trim_event_prefix,
    )
    _run_split(options)


def main() -> None:
    click_main(standalone_mode=True)


if __name__ == "__main__":
    main()


def _run_split(options: SplitOptions) -> None:
    side_color = chess.WHITE if options.side.lower() == "white" else chess.BLACK
    repertoire = Repertoire.from_pgn_file(side=side_color, pgn_path=options.pgn_file)
    splitter = RepertoireSplitter(repertoire)
    events = splitter.split_events(max_moves=options.max_moves)
    event_names = (
        splitter.compact_event_names(events) if options.trim_event_prefix else None
    )
    splitter.write_events(
        events, output_path=options.output_path, event_names=event_names
    )

    click.echo(
        f"Created {len(events)} games at {options.output_path} (max {options.max_moves} moves each)"
    )
