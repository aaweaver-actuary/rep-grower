from __future__ import annotations

import asyncio
import httpx
from pathlib import Path
from typing import Iterable

import click

from .lichess_explorer_api import LichessExplorerApi
from .pgn_metadata import upsert_reach_count_tag
from .repertoire import Repertoire, RepertoireNode


async def _fetch_total_games(
    fen: str,
    *,
    retries: int,
    backoff: float,
    jitter: float,
) -> int | None:
    api = LichessExplorerApi(fen=fen)
    try:
        await api.raw_explorer(retries=retries, backoff=backoff, jitter=jitter)
    except TypeError:
        # Fallback for test doubles that do not accept retry args.
        try:
            await api.raw_explorer()
        except Exception:
            return None
    except httpx.HTTPError:
        return None
    except Exception:
        return None
    response = getattr(api, "response", None)
    if response is None or not hasattr(response, "totalGames"):
        return None
    return response.totalGames


async def _annotate_nodes(
    nodes: Iterable[RepertoireNode],
    *,
    repertoire: Repertoire,
    max_concurrency: int,
    force: bool,
    retries: int,
    backoff: float,
    jitter: float,
    progress_every: int | None,
    progress_bar: bool,
    checkpoint_every: int | None,
    checkpoint_base: Path | None,
    total_nodes: int,
    dry_run: bool,
) -> int:
    semaphore = asyncio.Semaphore(max(1, max_concurrency))
    updated = 0
    lock = asyncio.Lock()
    progress_step: int | None = (
        progress_every if progress_every is not None and progress_every > 0 else None
    )
    use_progress_bar = progress_bar and progress_step is not None and total_nodes > 0
    progress_printed = False

    async def annotate(node: RepertoireNode) -> None:
        nonlocal updated
        if node.games_reached is not None and not force:
            return
        async with semaphore:
            try:
                total_games = await _fetch_total_games(
                    node.fen, retries=retries, backoff=backoff, jitter=jitter
                )
            except Exception:
                total_games = None
        if total_games is None:
            return
        node.games_reached = total_games
        for pgn_node in node.pgn_nodes:
            pgn_node.comment = upsert_reach_count_tag(pgn_node.comment, total_games)

        async with lock:
            updated += 1
            if (
                use_progress_bar
                and progress_step is not None
                and updated % progress_step == 0
            ):
                pct = (updated / total_nodes) * 100 if total_nodes else 0.0
                click.echo(
                    f"\rProgress: {updated}/{total_nodes} ({pct:.1f}%)",
                    nl=False,
                )
                progress_printed = True
            if (
                checkpoint_every
                and checkpoint_base is not None
                and not dry_run
                and updated % checkpoint_every == 0
            ):
                if use_progress_bar and progress_printed:
                    click.echo()  # finish progress line before checkpoint message
                    progress_printed = False
                checkpoint_path = checkpoint_base.with_name(
                    f"{checkpoint_base.stem}_checkpoint_{updated}{checkpoint_base.suffix}"
                )
                checkpoint_path.write_text(repertoire.pgn, encoding="utf-8")
                click.echo(f"Checkpoint written to {checkpoint_path}")

    await asyncio.gather(*(annotate(node) for node in nodes))
    if use_progress_bar and progress_printed:
        click.echo()
    return updated


@click.command()
@click.option(
    "--pgn-file",
    type=click.Path(exists=True, dir_okay=False, path_type=str),
    required=True,
    help="Input PGN to annotate in place by default.",
)
@click.option(
    "--side",
    type=click.Choice(["white", "black"], case_sensitive=False),
    required=True,
    help="Repertoire side (used for PGN parsing context).",
)
@click.option(
    "--output",
    type=click.Path(dir_okay=False, path_type=str),
    default=None,
    show_default=True,
    help="Destination path; defaults to overwriting the input file.",
)
@click.option(
    "--max-concurrency",
    type=int,
    default=1,
    show_default=True,
    help="Maximum concurrent Explorer requests.",
)
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Overwrite existing reach-count tags instead of skipping them.",
)
@click.option(
    "--include-opponent",
    is_flag=True,
    default=False,
    help="Annotate all nodes, not just the repertoire side's move.",
)
@click.option(
    "--progress-every",
    type=int,
    default=25,
    show_default=True,
    help="Print progress every N successful annotations (0 to disable).",
)
@click.option(
    "--progress-bar/--no-progress-bar",
    default=True,
    show_default=True,
    help="Render progress updates on a single line instead of new messages.",
)
@click.option(
    "--checkpoint-every",
    type=int,
    default=None,
    show_default=True,
    help="Write intermediate PGN checkpoints every N annotations (disabled when unset).",
)
@click.option(
    "--retries",
    type=int,
    default=6,
    show_default=True,
    help="Explorer retries per node (transient errors are retried).",
)
@click.option(
    "--backoff",
    type=float,
    default=1.0,
    show_default=True,
    help="Initial backoff (seconds) for Explorer retries.",
)
@click.option(
    "--jitter",
    type=float,
    default=0.3,
    show_default=True,
    help="Jitter (seconds) added to backoff for Explorer retries.",
)
@click.option(
    "--dry-run",
    is_flag=True,
    default=False,
    help="Compute and report counts without writing the PGN.",
)
def click_main(
    pgn_file: str,
    side: str,
    output: str | None,
    max_concurrency: int,
    force: bool,
    include_opponent: bool,
    progress_every: int,
    progress_bar: bool,
    checkpoint_every: int | None,
    retries: int,
    backoff: float,
    jitter: float,
    dry_run: bool,
) -> None:
    """Annotate PGN nodes with reach-count tags fetched from Lichess Explorer."""

    side_color = side.lower()
    import chess

    repertoire = Repertoire.from_pgn_file(
        side=chess.WHITE if side_color == "white" else chess.BLACK,
        pgn_path=pgn_file,
    )

    if include_opponent:
        target_nodes = list(repertoire.nodes_by_fen.values())
    else:
        target_nodes = [
            node
            for node in repertoire.nodes_by_fen.values()
            if repertoire.side == chess.Board(node.fen).turn
        ]

    progress_value = progress_every if progress_every > 0 else None

    checkpoint_base = None
    if checkpoint_every and not dry_run:
        checkpoint_base = Path(output) if output else Path(pgn_file)

    updated = asyncio.run(
        _annotate_nodes(
            target_nodes,
            repertoire=repertoire,
            max_concurrency=max_concurrency,
            force=force,
            retries=retries,
            backoff=backoff,
            jitter=jitter,
            progress_every=progress_value,
            progress_bar=progress_bar,
            checkpoint_every=checkpoint_every,
            checkpoint_base=checkpoint_base,
            total_nodes=len(target_nodes),
            dry_run=dry_run,
        )
    )

    click.echo(f"Annotated {updated} nodes with reach counts")

    if dry_run:
        return

    output_path = Path(output) if output else Path(pgn_file)
    output_path.write_text(repertoire.pgn, encoding="utf-8")
    click.echo(f"Wrote annotated PGN to {output_path}")


def main() -> None:
    click_main(standalone_mode=True)


if __name__ == "__main__":
    main()
