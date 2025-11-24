import chess
import click

from .repertoire import Repertoire
from .repertoire_pruner import RepertoirePruner


@click.command()
@click.argument("pgn_file", type=click.Path(exists=True, dir_okay=False, path_type=str))
@click.option(
    "--side",
    type=click.Choice(["white", "black"], case_sensitive=False),
    default="white",
    show_default=True,
    help="Player side to prune (determines which move choices are trimmed)",
)
@click.option(
    "--output",
    "output_path",
    type=click.Path(dir_okay=False, path_type=str),
    required=True,
    help="Destination PGN file for the pruned repertoire",
)
@click.option(
    "--preferred-move",
    "preferred_moves",
    multiple=True,
    help=(
        "SAN or UCI moves to prioritize when available; can be provided multiple times"
    ),
)
def click_main(
    pgn_file: str,
    side: str,
    output_path: str,
    preferred_moves: tuple[str, ...],
) -> None:
    """Prune a repertoire PGN down to the most frequent player moves."""

    side_color = chess.WHITE if side.lower() == "white" else chess.BLACK
    repertoire = Repertoire.from_pgn_file(side=side_color, pgn_path=pgn_file)
    pruner = RepertoirePruner(repertoire, preferred_moves=preferred_moves)
    pruned_game = pruner.pruned_game()

    output = str(pruned_game).strip() + "\n"
    with open(output_path, "w", encoding="utf-8") as handle:
        handle.write(output)
    click.echo(f"Pruned PGN written to {output_path}")


def main() -> None:
    click_main(standalone_mode=True)


if __name__ == "__main__":
    main()
