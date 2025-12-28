import asyncio
import chess
import click
from dataclasses import dataclass

from . import _core
from .cli_options import GrowOptions
from .repertoire import Repertoire, RepertoireConfig


@dataclass(frozen=True)
class GrowRunConfig:
    side_color: chess.Color
    initial_san: str
    pgn_file: str | None
    iterations: int
    output_dir: str
    max_player_moves: int | None
    repertoire_config: RepertoireConfig


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
    "--analysis-provider",
    type=click.Choice(["stockfish", "lichess", "hybrid"], case_sensitive=False),
    default="stockfish",
    help="Select analysis backend: local Stockfish, Lichess cloud, or hybrid (cloud with local fallback).",
)
@click.option(
    "--lichess-eval-path",
    type=click.Path(exists=False, dir_okay=False, path_type=str),
    default=None,
    help="Path to a lichess_db_eval.jsonl.zst file for offline/static evaluations.",
    show_default=False,
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
    default=5.0,
    help="Minimum game share (percent) for explorer move selection. Overrides top-p if a move's share is below this threshold.",
)
@click.option(
    "--min-games-reached",
    type=int,
    default=None,
    help=(
        "Skip expanding leaf nodes whose recorded games_reached count is below this"
        " threshold. Useful for pruning low-popularity branches."
    ),
    show_default=False,
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
    analysis_provider: str,
    explorer_pct: float,
    explorer_min_game_share: float,
    max_player_moves: int | None,
    min_games_reached: int | None,
    lichess_eval_path: str | None,
):
    options = GrowOptions(
        initial_san=initial_san,
        pgn_file=pgn_file,
        iterations=iterations,
        output_dir=output_dir,
        side=side,
        engine_path=engine_path,
        engine_depth=engine_depth,
        engine_pool_size=engine_pool_size,
        engine_multi_pv=engine_multi_pv,
        best_score_threshold=best_score_threshold,
        analysis_provider=analysis_provider,
        explorer_pct=explorer_pct,
        explorer_min_game_share=explorer_min_game_share,
        max_player_moves=max_player_moves,
        min_games_reached=min_games_reached,
        lichess_eval_path=lichess_eval_path,
    )
    _validate_grow_options(options)
    run_config = _build_run_config(options)
    _run_grow(run_config)


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


def _build_run_config(options: GrowOptions) -> GrowRunConfig:
    side_color = chess.WHITE if options.side.lower() == "white" else chess.BLACK

    def _pct_to_fraction(value: float) -> float:
        return value / 100.0

    rep_config = RepertoireConfig(
        stockfish_multi_pv=options.engine_multi_pv,
        stockfish_depth=options.engine_depth,
        stockfish_engine_path=options.engine_path,
        stockfish_best_score_threshold=options.best_score_threshold,
        stockfish_pool_size=options.engine_pool_size,
        explorer_pct=options.explorer_pct,
        explorer_min_game_share=_pct_to_fraction(options.explorer_min_game_share),
        min_games_reached=options.min_games_reached,
        analysis_provider=options.analysis_provider,
        lichess_eval_path=options.lichess_eval_path,
    )

    return GrowRunConfig(
        side_color=side_color,
        initial_san=options.initial_san,
        pgn_file=options.pgn_file or None,
        iterations=options.iterations,
        output_dir=options.output_dir,
        max_player_moves=options.max_player_moves,
        repertoire_config=rep_config,
    )


def _leaf_turn_counts(
    rep: Repertoire,
    max_player_moves: int | None = None,
    min_games_reached: int | None = None,
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
    for node, is_player, move_count in zip(leaves, mask, move_counts):
        if (
            min_games_reached is not None
            and node.games_reached is not None
            and node.games_reached < min_games_reached
        ):
            continue
        if max_player_moves is not None and move_count >= max_player_moves:
            continue
        if is_player:
            player_nodes += 1
        else:
            opponent_nodes += 1
    return player_nodes, opponent_nodes


def _repertoire_move_count(rep: Repertoire) -> int:
    return sum(len(node.children) for node in rep.nodes_by_fen.values())


def _load_grow_repertoire(run_config: GrowRunConfig) -> Repertoire:
    if run_config.pgn_file:
        rep = Repertoire.from_pgn_file(
            side=run_config.side_color,
            pgn_path=run_config.pgn_file,
            config=run_config.repertoire_config,
        )
    else:
        rep = Repertoire.from_str(
            "white" if run_config.side_color == chess.WHITE else "black",
            run_config.initial_san,
            config=run_config.repertoire_config,
        )
        rep.play_initial_moves()
    return rep


def _iteration_label(iteration: int, *, total_targets: int, iterations: int) -> str:
    if iterations > 1:
        return f"Iteration {iteration} progress ({total_targets} nodes)"
    return f"Expanding repertoire ({total_targets} nodes)"


def _expand_repertoire_pass(
    rep: Repertoire,
    *,
    run_config: GrowRunConfig,
    total_targets: int,
    label: str,
) -> None:
    if total_targets > 0:
        with click.progressbar(length=total_targets, label=label) as node_bar:

            def _progress_callback(_node):
                node_bar.update(1)

            asyncio.run(
                rep.expand_leaves_by_turn(
                    max_player_moves=run_config.max_player_moves,
                    min_games_reached=run_config.repertoire_config.min_games_reached,
                    progress_callback=_progress_callback,
                )
            )
    else:
        asyncio.run(
            rep.expand_leaves_by_turn(
                max_player_moves=run_config.max_player_moves,
                min_games_reached=run_config.repertoire_config.min_games_reached,
            )
        )


def _export_iteration_snapshot(
    rep: Repertoire,
    *,
    run_config: GrowRunConfig,
    initial_moves_slug: str,
    iteration: int,
) -> str:
    filename = (
        f"{run_config.output_dir}/{initial_moves_slug}__iteration_{iteration}.pgn"
    )
    rep.export_pgn(filename)
    click.echo(f"    Exported repertoire to {filename}")
    return filename


def _run_single_iteration(
    rep: Repertoire,
    *,
    iteration: int,
    iterations: int,
    run_config: GrowRunConfig,
    initial_moves_slug: str,
) -> None:
    player_nodes, opponent_nodes = _leaf_turn_counts(
        rep,
        run_config.max_player_moves,
        run_config.repertoire_config.min_games_reached,
    )
    total_targets = player_nodes + opponent_nodes
    click.echo(
        f"Iteration {iteration}: expanding {player_nodes} player-turn and {opponent_nodes} opponent-turn leaf nodes..."
    )
    before_moves = _repertoire_move_count(rep)

    label = _iteration_label(
        iteration, total_targets=total_targets, iterations=iterations
    )
    _expand_repertoire_pass(
        rep,
        run_config=run_config,
        total_targets=total_targets,
        label=label,
    )

    after_moves = _repertoire_move_count(rep)
    added_moves = max(0, after_moves - before_moves)
    click.echo(f"    Added {added_moves} SAN moves this pass (total {after_moves}).")

    _export_iteration_snapshot(
        rep,
        run_config=run_config,
        initial_moves_slug=initial_moves_slug,
        iteration=iteration,
    )


def _export_final_repertoire(
    rep: Repertoire, *, run_config: GrowRunConfig, initial_moves_slug: str
) -> None:
    click.echo("\nFinal PGN with all engine variations:")
    final_filename = f"{run_config.output_dir}/{initial_moves_slug}.pgn"
    rep.export_pgn(final_filename)
    click.echo(f"Exported repertoire to {final_filename}")


def _run_grow(run_config: GrowRunConfig) -> None:
    rep = _load_grow_repertoire(run_config)

    click.echo("PGN:")
    click.echo(rep.pgn)

    initial_moves = _initial_moves_slug(rep.initial_san)
    if run_config.iterations > 0:
        for iteration in range(1, run_config.iterations + 1):
            _run_single_iteration(
                rep,
                iteration=iteration,
                iterations=run_config.iterations,
                run_config=run_config,
                initial_moves_slug=initial_moves,
            )

    _export_final_repertoire(
        rep, run_config=run_config, initial_moves_slug=initial_moves
    )


def _validate_grow_options(options: GrowOptions) -> None:
    if not options.has_exactly_one_source():
        raise click.UsageError(
            "You must provide either --initial-san or --pgn-file, but not both."
        )

    if options.max_player_moves is not None and options.max_player_moves < 1:
        raise click.BadParameter(
            "--max-player-moves must be a positive integer.",
            param_hint="--max-player-moves",
        )
    if options.min_games_reached is not None and options.min_games_reached < 1:
        raise click.BadParameter(
            "--min-games-reached must be a positive integer.",
            param_hint="--min-games-reached",
        )
    if options.engine_pool_size is not None and options.engine_pool_size < 1:
        raise click.BadParameter(
            "--engine-pool-size must be a positive integer.",
            param_hint="--engine-pool-size",
        )
    if options.explorer_pct <= 0 or options.explorer_pct > 100:
        raise click.BadParameter(
            "--explorer-pct must be between 0 and 100 (percent).",
            param_hint="--explorer-pct",
        )
    if options.explorer_min_game_share <= 0 or options.explorer_min_game_share > 100:
        raise click.BadParameter(
            "--explorer-min-game-share must be between 0 and 100 (percent).",
            param_hint="--explorer-min-game-share",
        )
