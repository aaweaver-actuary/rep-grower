import asyncio
import chess
import click

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
    engine_multi_pv: int,
    best_score_threshold: int,
    explorer_pct: float,
    explorer_min_game_share: float,
):
    if bool(initial_san) == bool(pgn_file):
        raise click.UsageError(
            "You must provide either --initial-san or --pgn-file, but not both."
        )

    side_color = chess.WHITE if side.lower() == "white" else chess.BLACK

    config = RepertoireConfig(
        stockfish_multi_pv=engine_multi_pv,
        stockfish_depth=engine_depth,
        stockfish_engine_path=engine_path,
        stockfish_best_score_threshold=best_score_threshold,
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

    print("PGN:")
    print(rep.pgn)

    async def expand_by_turn(iteration: int):
        player_nodes = 0
        opponent_nodes = 0
        for node in rep.leaf_nodes:
            board = chess.Board(node.fen)
            if board.turn == rep.side:
                player_nodes += 1
            else:
                opponent_nodes += 1
        print(
            "Iteration {iter}: expanding {player} player-turn and {opp} opponent-turn leaf nodes...".format(
                iter=iteration, player=player_nodes, opp=opponent_nodes
            )
        )
        await rep.expand_leaves_by_turn()

    for i in range(iterations):
        asyncio.run(expand_by_turn(i + 1))
        initial_moves = ""
        for j, move in enumerate(rep.initial_san.split()):
            move_number = (j // 2) + 1
            if j == 0:
                initial_moves += f"{move_number}_{move}"
            elif j % 2 == 0:
                initial_moves += f"_{move_number}_{move}"
            else:
                initial_moves += f"_{move}"
        filename = f"{output_dir}/{initial_moves}__iteration_{i + 1}.pgn"
        rep.export_pgn(filename)
        print(f"Exported repertoire to {filename}")

    print("\nFinal PGN with all engine variations:")
    filename = f"{output_dir}/{initial_moves}.pgn"
    rep.export_pgn(filename)
    print(f"Exported repertoire to {filename}")


def main():
    click_main()


if __name__ == "__main__":
    main()
