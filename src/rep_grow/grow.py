import asyncio
import chess
import click

from . import _core
from .repertoire import Repertoire, RepertoireConfig


@click.command()
@click.option(
    "--side",
    type=click.Choice(["white", "black"], case_sensitive=False),
    default="white",
    help="Side to play in the repertoire.",
)
@click.option(
    "--initial-san",
    type=str,
    required=False,
    help="Initial moves in SAN notation.",
)
@click.option(
    "--pgn-file",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    required=False,
    help="Path to the PGN file containing the repertoire.",
)
@click.option(
    "--iterations",
    type=int,
    default=10,
    help="Number of expansion iterations to perform.",
)
@click.option(
    "--max-player-moves",
    type=int,
    default=None,
    help=(
        "Stop expanding a line once the player side has this many moves."
        " Useful for targeting a specific repertoire depth."
    ),
    show_default=False,
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=str),
    default=".",
    help="Directory to save the exported PGN files.",
)
@click.option(
    "--engine-path",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    default="/opt/homebrew/bin/stockfish",
    help="Path to the Stockfish engine executable.",
)
@click.option(
    "--engine-depth",
    type=int,
    default=20,
    help="Depth for Stockfish analysis.",
)
@click.option(
    "--engine-pool-size",
    type=int,
    default=None,
    help="Number of persistent Stockfish worker processes to keep alive.",
    show_default=False,
)
@click.option(
    "--engine-multi-pv",
    type=int,
    default=10,
    help="Number of principal variations for Stockfish analysis.",
)
@click.option(
    "--best-score-threshold",
    type=int,
    default=20,
    help="Moves within this centipawn threshold of the best score will be added.",
)
@click.option(
    "--explorer-pct",
    type=float,
    default=95.0,
    help="Top-p percentage for explorer move selection. Moves covering this percentage of games will be added.",
)
@click.option(
    "--explorer-min-game-share",
    type=float,
    default=0.05,
    help="Minimum game share for explorer move selection. Overrides top-p if a move's share is below this threshold.",
)
def click_main(
    initial_san: str,
    pgn_file: str,
    iterations: int,
    output_dir: str,
    side: str,
    engine_path: str,
    engine_depth: int,
    engine_pool_size: int | None,
    engine_multi_pv: int,
    best_score_threshold: int,
    explorer_pct: float,
    explorer_min_game_share: float,
    max_player_moves: int | None,
):
    if bool(initial_san) == bool(pgn_file):
        raise click.UsageError(
            "You must provide either --initial-san or --pgn-file, but not both."
        )

    if max_player_moves is not None and max_player_moves < 1:
        raise click.BadParameter(
            "--max-player-moves must be a positive integer.",
            param_hint="--max-player-moves",
        )
    if engine_pool_size is not None and engine_pool_size < 1:
        raise click.BadParameter(
            "--engine-pool-size must be a positive integer.",
            param_hint="--engine-pool-size",
        )

    side_color = chess.WHITE if side.lower() == "white" else chess.BLACK

    config = RepertoireConfig(
        stockfish_multi_pv=engine_multi_pv,
        stockfish_depth=engine_depth,
        stockfish_engine_path=engine_path,
        stockfish_best_score_threshold=best_score_threshold,
        stockfish_pool_size=engine_pool_size,
        explorer_pct=explorer_pct,
        explorer_min_game_share=explorer_min_game_share,
    )

    if pgn_file:
        rep = Repertoire.from_pgn_file(
            side=side_color,
            pgn_path=pgn_file,
            config=config,
        )
    else:
        rep = Repertoire.from_str(
            side,
            initial_san,
            config=config,
        )
        rep.play_initial_moves()

    click.echo("PGN:")
    click.echo(rep.pgn)

    initial_moves = _initial_moves_slug(rep.initial_san)
    if iterations > 0:
        with click.progressbar(length=iterations, label="Growing repertoire") as bar:
            for iteration in range(1, iterations + 1):
                player_nodes, opponent_nodes = _leaf_turn_counts(rep, max_player_moves)
                click.echo(
                    f"Iteration {iteration}: expanding {player_nodes} player-turn and {opponent_nodes} opponent-turn leaf nodes..."
                )
                before_moves = _repertoire_move_count(rep)
                asyncio.run(
                    rep.expand_leaves_by_turn(max_player_moves=max_player_moves)
                )
                after_moves = _repertoire_move_count(rep)
                added_moves = max(0, after_moves - before_moves)
                click.echo(
                    f"    Added {added_moves} SAN moves this pass (total {after_moves})."
                )

                filename = f"{output_dir}/{initial_moves}__iteration_{iteration}.pgn"
                rep.export_pgn(filename)
                click.echo(f"    Exported repertoire to {filename}")
                bar.update(1)

    click.echo("\nFinal PGN with all engine variations:")
    final_filename = f"{output_dir}/{initial_moves}.pgn"
    rep.export_pgn(final_filename)
    click.echo(f"Exported repertoire to {final_filename}")


def main():
    click_main()


if __name__ == "__main__":
    main()


def _initial_moves_slug(initial_san: str) -> str:
    slug = ""
    for index, move in enumerate(initial_san.split()):
        move_number = (index // 2) + 1
        if index == 0:
            slug += f"{move_number}_{move}"
        elif index % 2 == 0:
            slug += f"_{move_number}_{move}"
        else:
            slug += f"_{move}"
    return slug


def _leaf_turn_counts(
    rep: Repertoire, max_player_moves: int | None = None
) -> tuple[int, int]:
    leaves = rep.leaf_nodes
    if not leaves:
        return 0, 0
    mask = _core.player_turn_mask(
        rep.side == chess.WHITE,
        [node.fen for node in leaves],
    )
    move_counts = [rep.player_move_count(node) for node in leaves]
    player_nodes = 0
    opponent_nodes = 0
    for is_player, move_count in zip(mask, move_counts):
        if max_player_moves is not None and move_count >= max_player_moves:
            continue
        if is_player:
            player_nodes += 1
        else:
            opponent_nodes += 1
    return player_nodes, opponent_nodes


def _repertoire_move_count(rep: Repertoire) -> int:
    return sum(len(node.children) for node in rep.nodes_by_fen.values())
