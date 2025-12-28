from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path

import chess
import click

from . import _core
from .cli_options import BuildOptions
from .grow import _initial_moves_slug, _leaf_turn_counts, _repertoire_move_count
from .repertoire import Repertoire, RepertoireConfig
from .repertoire_pruner import RepertoirePruner


@dataclass(frozen=True)
class BuildRunConfig:
    side_color: chess.Color
    initial_san: str
    pgn_file: str | None
    output_dir: Path
    prune_output_dir: Path
    target_player_moves: int
    min_games_stop: int
    max_player_moves: int | None
    max_total_iterations: int
    iterations_first: int
    iterations_per_cycle: int
    max_cycles: int
    repertoire_config: RepertoireConfig
    preferred_moves: tuple[str, ...]
    ramp_iterations: int | None
    explorer_pct_start: float
    explorer_pct_final: float
    explorer_min_game_share_start: float
    explorer_min_game_share_final: float
    best_score_threshold_start: int
    best_score_threshold_final: int


@dataclass(frozen=True)
class PlayerProgress:
    remaining_targets: int
    min_depth: int | None
    max_depth: int | None


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
    "--initial-iterations",
    type=int,
    default=4,
    help="Grow iterations to run before the first prune cycle.",
)
@click.option(
    "--iterations-per-cycle",
    type=int,
    default=2,
    help="Grow iterations to run in each cycle after the first prune.",
)
@click.option(
    "--target-hero-moves",
    "target_player_moves",
    type=int,
    default=10,
    help="Target number of hero (player) moves to reach before stopping.",
)
@click.option(
    "--min-games-stop",
    type=int,
    default=1000,
    help=(
        "Stop expanding when remaining player-turn leaves each have fewer games_reached "
        "than this threshold."
    ),
)
@click.option(
    "--min-games-reached",
    type=int,
    default=None,
    show_default=False,
    help=(
        "Skip expanding leaves whose games_reached is below this threshold. Defaults to "
        "--min-games-stop when omitted."
    ),
)
@click.option(
    "--max-cycles",
    type=int,
    default=8,
    help="Maximum build cycles (grow + prune) to run.",
)
@click.option(
    "--max-total-iterations",
    type=int,
    default=40,
    help="Safety cap for the total number of grow iterations across all cycles.",
)
@click.option(
    "--output-dir",
    type=click.Path(file_okay=False, path_type=str),
    default=".",
    help="Directory to save the exported PGN files from grow passes.",
)
@click.option(
    "--prune-output-dir",
    type=click.Path(file_okay=False, path_type=str),
    default=None,
    show_default=False,
    help="Directory to save pruned PGNs (defaults to --output-dir).",
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
    show_default=False,
    help="Number of persistent Stockfish worker processes to keep alive.",
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
    show_default=False,
    help="Path to a lichess_db_eval.jsonl.zst file for offline/static evaluations.",
)
@click.option(
    "--explorer-pct",
    type=float,
    default=95.0,
    help="Top-p percentage for explorer move selection.",
)
@click.option(
    "--explorer-pct-final",
    type=float,
    default=None,
    show_default=False,
    help="Optional final top-p percentage; ramps linearly from --explorer-pct when --ramp-iterations is set.",
)
@click.option(
    "--explorer-min-game-share",
    type=float,
    default=5.0,
    help="Minimum game share for explorer move selection.",
)
@click.option(
    "--explorer-min-game-share-final",
    type=float,
    default=None,
    show_default=False,
    help="Optional final minimum game share; ramps linearly from --explorer-min-game-share when --ramp-iterations is set.",
)
@click.option(
    "--disable-explorer",
    is_flag=True,
    default=False,
    help="Disable Lichess Explorer expansion; use engine for both sides.",
)
@click.option(
    "--max-player-moves",
    type=int,
    default=None,
    show_default=False,
    help="Stop expanding a line once the player side has this many moves.",
)
@click.option(
    "--preferred-move",
    "preferred_moves",
    multiple=True,
    help="SAN or UCI moves to prioritize in pruning when available; can be provided multiple times.",
)
@click.option(
    "--best-score-threshold-final",
    type=int,
    default=None,
    show_default=False,
    help="Optional final best-score-threshold; ramps from --best-score-threshold when --ramp-iterations is set.",
)
@click.option(
    "--ramp-iterations",
    type=int,
    default=None,
    show_default=False,
    help="If set, linearly ramp explorer pct/game share and best-score-threshold from initial to final over this many grow iterations.",
)
def click_main(
    initial_san: str,
    pgn_file: str,
    side: str,
    initial_iterations: int,
    iterations_per_cycle: int,
    target_player_moves: int,
    min_games_stop: int,
    min_games_reached: int | None,
    max_cycles: int,
    max_total_iterations: int,
    output_dir: str,
    prune_output_dir: str | None,
    engine_path: str,
    engine_depth: int,
    engine_pool_size: int | None,
    engine_multi_pv: int,
    best_score_threshold: int,
    analysis_provider: str,
    lichess_eval_path: str | None,
    explorer_pct: float,
    explorer_pct_final: float | None,
    explorer_min_game_share: float,
    explorer_min_game_share_final: float | None,
    disable_explorer: bool,
    max_player_moves: int | None,
    preferred_moves: tuple[str, ...],
    best_score_threshold_final: int | None,
    ramp_iterations: int | None,
) -> None:
    options = BuildOptions(
        initial_san=initial_san,
        pgn_file=pgn_file,
        side=side,
        output_dir=output_dir,
        prune_output_dir=prune_output_dir,
        initial_iterations=initial_iterations,
        iterations_per_cycle=iterations_per_cycle,
        target_player_moves=target_player_moves,
        min_games_stop=min_games_stop,
        max_cycles=max_cycles,
        max_total_iterations=max_total_iterations,
        engine_path=engine_path,
        engine_depth=engine_depth,
        engine_pool_size=engine_pool_size,
        engine_multi_pv=engine_multi_pv,
        best_score_threshold=best_score_threshold,
        best_score_threshold_final=best_score_threshold_final,
        analysis_provider=analysis_provider,
        explorer_enabled=not disable_explorer,
        explorer_pct=explorer_pct,
        explorer_pct_final=explorer_pct_final,
        explorer_min_game_share=explorer_min_game_share,
        explorer_min_game_share_final=explorer_min_game_share_final,
        ramp_iterations=ramp_iterations,
        max_player_moves=max_player_moves,
        min_games_reached=min_games_reached,
        lichess_eval_path=lichess_eval_path,
        preferred_moves=preferred_moves,
        analysis_providers=None,
    )
    _validate_build_options(options)

    run_config = _build_run_config(options)
    _run_build(run_config)


def _build_run_config(options: BuildOptions) -> BuildRunConfig:
    side_color = chess.WHITE if options.side.lower() == "white" else chess.BLACK
    max_player_moves = options.max_player_moves or options.target_player_moves
    min_reach_threshold = options.min_games_reached or options.min_games_stop
    initial_san = options.initial_san or ""

    explorer_pct_final = (
        options.explorer_pct_final
        if options.explorer_pct_final is not None
        else options.explorer_pct
    )

    def _pct_to_fraction(value: float) -> float:
        return value / 100.0

    def _pct_to_fraction_optional(value: float | None) -> float | None:
        if value is None:
            return None
        return value / 100.0

    explorer_min_game_share = _pct_to_fraction(options.explorer_min_game_share)
    explorer_min_game_share_final = _pct_to_fraction_optional(
        options.explorer_min_game_share_final
    )
    explorer_min_game_share_final = (
        explorer_min_game_share_final
        if explorer_min_game_share_final is not None
        else explorer_min_game_share
    )
    best_score_threshold_final = (
        options.best_score_threshold_final
        if options.best_score_threshold_final is not None
        else options.best_score_threshold
    )
    ramp_needed = any(
        [
            explorer_pct_final != options.explorer_pct,
            explorer_min_game_share_final != options.explorer_min_game_share,
            best_score_threshold_final != options.best_score_threshold,
        ]
    )
    ramp_iterations = options.ramp_iterations or (
        options.initial_iterations if ramp_needed else None
    )

    rep_config = RepertoireConfig(
        stockfish_multi_pv=options.engine_multi_pv,
        stockfish_depth=options.engine_depth,
        stockfish_engine_path=options.engine_path,
        stockfish_best_score_threshold=options.best_score_threshold,
        stockfish_pool_size=options.engine_pool_size,
        explorer_pct=options.explorer_pct,
        explorer_enabled=options.explorer_enabled,
        explorer_min_game_share=explorer_min_game_share,
        min_games_reached=min_reach_threshold,
        analysis_provider=options.analysis_provider,
        lichess_eval_path=options.lichess_eval_path,
    )

    output_dir = Path(options.output_dir)
    prune_dir = Path(options.prune_output_dir or options.output_dir)

    return BuildRunConfig(
        side_color=side_color,
        initial_san=initial_san,
        pgn_file=options.pgn_file or None,
        output_dir=output_dir,
        prune_output_dir=prune_dir,
        target_player_moves=options.target_player_moves,
        min_games_stop=options.min_games_stop,
        max_player_moves=max_player_moves,
        max_total_iterations=options.max_total_iterations,
        iterations_first=options.initial_iterations,
        iterations_per_cycle=options.iterations_per_cycle,
        max_cycles=options.max_cycles,
        repertoire_config=rep_config,
        preferred_moves=tuple(options.preferred_moves),
        ramp_iterations=ramp_iterations,
        explorer_pct_start=options.explorer_pct,
        explorer_pct_final=explorer_pct_final,
        explorer_min_game_share_start=explorer_min_game_share,
        explorer_min_game_share_final=explorer_min_game_share_final,
        best_score_threshold_start=options.best_score_threshold,
        best_score_threshold_final=best_score_threshold_final,
    )


def _validate_build_options(options: BuildOptions) -> None:
    if not (bool(options.initial_san) ^ bool(options.pgn_file)):
        raise click.UsageError(
            "You must provide either --initial-san or --pgn-file, but not both."
        )
    if options.initial_iterations < 1:
        raise click.BadParameter(
            "--initial-iterations must be a positive integer.",
            param_hint="--initial-iterations",
        )
    if options.iterations_per_cycle < 1:
        raise click.BadParameter(
            "--iterations-per-cycle must be a positive integer.",
            param_hint="--iterations-per-cycle",
        )
    if options.target_player_moves < 1:
        raise click.BadParameter(
            "--target-hero-moves must be a positive integer.",
            param_hint="--target-hero-moves",
        )
    if options.min_games_stop < 1:
        raise click.BadParameter(
            "--min-games-stop must be a positive integer.",
            param_hint="--min-games-stop",
        )
    if options.max_cycles < 1:
        raise click.BadParameter(
            "--max-cycles must be a positive integer.",
            param_hint="--max-cycles",
        )
    if options.max_total_iterations < 1:
        raise click.BadParameter(
            "--max-total-iterations must be a positive integer.",
            param_hint="--max-total-iterations",
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
    if options.explorer_pct_final is not None:
        if options.explorer_pct_final <= 0 or options.explorer_pct_final > 100:
            raise click.BadParameter(
                "--explorer-pct-final must be between 0 and 100 (percent).",
                param_hint="--explorer-pct-final",
            )
    if options.explorer_min_game_share <= 0 or options.explorer_min_game_share > 100:
        raise click.BadParameter(
            "--explorer-min-game-share must be between 0 and 100 (percent).",
            param_hint="--explorer-min-game-share",
        )
    if options.explorer_min_game_share_final is not None:
        if (
            options.explorer_min_game_share_final <= 0
            or options.explorer_min_game_share_final > 100
        ):
            raise click.BadParameter(
                "--explorer-min-game-share-final must be between 0 and 100 (percent).",
                param_hint="--explorer-min-game-share-final",
            )
    if options.max_player_moves is not None and options.max_player_moves < 1:
        raise click.BadParameter(
            "--max-player-moves must be a positive integer when provided.",
            param_hint="--max-player-moves",
        )
    if options.min_games_reached is not None and options.min_games_reached < 1:
        raise click.BadParameter(
            "--min-games-reached must be a positive integer when provided.",
            param_hint="--min-games-reached",
        )
    if options.ramp_iterations is not None and options.ramp_iterations < 1:
        raise click.BadParameter(
            "--ramp-iterations must be a positive integer when provided.",
            param_hint="--ramp-iterations",
        )
    if (
        options.max_player_moves is not None
        and options.max_player_moves < options.target_player_moves
    ):
        raise click.BadParameter(
            "--max-player-moves cannot be less than --target-hero-moves.",
            param_hint="--max-player-moves",
        )


def main() -> None:
    click_main(standalone_mode=True)


def _load_repertoire(run_config: BuildRunConfig) -> Repertoire:
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


def _iteration_label(
    iteration_index: int, *, total_targets: int, iterations: int
) -> str:
    if iterations > 1:
        return f"Build iteration {iteration_index} progress ({total_targets} nodes)"
    return f"Build iteration {iteration_index}"


def _expand_repertoire_pass(
    rep: Repertoire,
    *,
    run_config: BuildRunConfig,
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
    run_config: BuildRunConfig,
    initial_moves_slug: str,
    iteration_index: int,
) -> Path:
    filename = (
        run_config.output_dir / f"{initial_moves_slug}__iteration_{iteration_index}.pgn"
    )
    rep.export_pgn(str(filename))
    click.echo(f"    Exported repertoire to {filename}")
    return filename


def _run_single_iteration(
    rep: Repertoire,
    *,
    iteration_index: int,
    iterations: int,
    run_config: BuildRunConfig,
    initial_moves_slug: str,
) -> tuple[int, bool]:
    current_iteration = iteration_index + 1
    _apply_ramp_settings(rep, run_config, current_iteration)
    player_nodes, opponent_nodes = _leaf_turn_counts(
        rep,
        run_config.max_player_moves,
        run_config.repertoire_config.min_games_reached,
    )
    total_targets = player_nodes + opponent_nodes
    click.echo(
        f"Iteration {current_iteration}: expanding {player_nodes} player-turn and {opponent_nodes} opponent-turn leaf nodes..."
    )
    before_moves = _repertoire_move_count(rep)
    label = _iteration_label(
        current_iteration, total_targets=total_targets, iterations=iterations
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
        iteration_index=current_iteration,
    )

    reached_total_cap = current_iteration >= run_config.max_total_iterations
    if reached_total_cap:
        click.echo("Reached the maximum total iterations; further growth will stop.")

    return current_iteration, reached_total_cap


def _player_progress(
    rep: Repertoire, *, target_player_moves: int, min_games_stop: int
) -> PlayerProgress:
    leaves = rep.leaf_nodes
    if not leaves:
        return PlayerProgress(remaining_targets=0, min_depth=None, max_depth=None)

    mask = _core.player_turn_mask(
        rep.side == chess.WHITE, [node.fen for node in leaves]
    )
    player_depths: list[int] = []
    remaining_depths: list[int] = []

    for node, is_player in zip(leaves, mask):
        if not is_player:
            continue
        depth = rep.player_move_count(node)
        player_depths.append(depth)
        games = node.games_reached
        if games is not None and games < min_games_stop:
            continue
        if depth < target_player_moves:
            remaining_depths.append(depth)

    return PlayerProgress(
        remaining_targets=len(remaining_depths),
        min_depth=min(player_depths) if player_depths else None,
        max_depth=max(player_depths) if player_depths else None,
    )


def _ramped_value(
    start: float | int,
    final: float | int,
    ramp_iterations: int | None,
    iteration_index: int,
) -> float:
    if ramp_iterations is None:
        return float(start)
    step = (final - start) / ramp_iterations
    if iteration_index >= ramp_iterations:
        return float(final)
    return float(start + step * iteration_index)


def _apply_ramp_settings(
    rep: Repertoire, run_config: BuildRunConfig, iteration_index: int
) -> None:
    rep.config.explorer_pct = _ramped_value(
        run_config.explorer_pct_start,
        run_config.explorer_pct_final,
        run_config.ramp_iterations,
        iteration_index,
    )
    rep.config.explorer_min_game_share = _ramped_value(
        run_config.explorer_min_game_share_start,
        run_config.explorer_min_game_share_final,
        run_config.ramp_iterations,
        iteration_index,
    )
    rep.config.stockfish_best_score_threshold = int(
        round(
            _ramped_value(
                run_config.best_score_threshold_start,
                run_config.best_score_threshold_final,
                run_config.ramp_iterations,
                iteration_index,
            )
        )
    )


def _grow_iterations(
    rep: Repertoire,
    *,
    iterations: int,
    iteration_offset: int,
    run_config: BuildRunConfig,
    initial_moves_slug: str,
) -> int:
    iteration_index = iteration_offset
    for _ in range(iterations):
        iteration_index, reached_total_cap = _run_single_iteration(
            rep,
            iteration_index=iteration_index,
            iterations=iterations,
            run_config=run_config,
            initial_moves_slug=initial_moves_slug,
        )
        if reached_total_cap:
            break

    return iteration_index


def _prune_and_reload(
    rep: Repertoire, *, preferred_moves: tuple[str, ...], prune_path: Path
) -> Repertoire:
    pruner = RepertoirePruner(rep, preferred_moves=preferred_moves)
    pruned_game = pruner.pruned_game()
    prune_path.parent.mkdir(parents=True, exist_ok=True)
    with open(prune_path, "w", encoding="utf-8") as handle:
        handle.write(str(pruned_game).strip() + "\n")
    click.echo(f"    Pruned PGN written to {prune_path}")
    return Repertoire.from_pgn_file(
        side=rep.side, pgn_path=str(prune_path), config=rep.config
    )


def _run_build(run_config: BuildRunConfig) -> None:
    run_config.output_dir.mkdir(parents=True, exist_ok=True)
    run_config.prune_output_dir.mkdir(parents=True, exist_ok=True)

    rep = _load_repertoire(run_config)
    initial_moves_slug = _initial_moves_slug(rep.initial_san)

    click.echo("Starting build from initial PGN:")
    click.echo(rep.pgn)

    iteration_index = 0
    for cycle in range(1, run_config.max_cycles + 1):
        iteration_budget = (
            run_config.iterations_first
            if cycle == 1
            else run_config.iterations_per_cycle
        )
        click.echo(f"\nCycle {cycle}: grow {iteration_budget} iterations, then prune.")

        iteration_index = _grow_iterations(
            rep,
            iterations=iteration_budget,
            iteration_offset=iteration_index,
            run_config=run_config,
            initial_moves_slug=initial_moves_slug,
        )

        progress = _player_progress(
            rep,
            target_player_moves=run_config.target_player_moves,
            min_games_stop=run_config.min_games_stop,
        )
        click.echo(
            f"    Hero move depth: min={progress.min_depth} max={progress.max_depth}; remaining player targets={progress.remaining_targets}"
        )

        if progress.remaining_targets == 0:
            break
        if iteration_index >= run_config.max_total_iterations:
            click.echo("Stopping build: reached max-total-iterations limit.")
            break

        prune_path = (
            run_config.prune_output_dir
            / f"{initial_moves_slug}__pruned_cycle_{cycle}.pgn"
        )
        rep = _prune_and_reload(
            rep,
            preferred_moves=run_config.preferred_moves,
            prune_path=prune_path,
        )

    final_filename = run_config.output_dir / f"{initial_moves_slug}__build_final.pgn"
    rep.export_pgn(str(final_filename))
    click.echo(f"\nExported final repertoire to {final_filename}")


if __name__ == "__main__":
    main()
