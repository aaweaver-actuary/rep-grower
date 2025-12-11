from __future__ import annotations


import chess
from click.testing import CliRunner

from rep_grow.annotate_reach_counts import click_main
from rep_grow.pgn_metadata import extract_reach_count


class FakeExplorer:
    totals: dict[str, int] = {}

    def __init__(self, fen, **kwargs):  # noqa: D401, ARG002
        self.fen = fen
        self._response = self

    async def raw_explorer(self):  # noqa: D401
        return self

    @property
    def response(self):  # noqa: D401
        return self

    @property
    def totalGames(self) -> int:  # noqa: D401
        from rep_grow.fen import canonical_fen

        candidates = [self.fen, canonical_fen(self.fen), " ".join(self.fen.split()[:4])]
        for key in candidates:
            if key in self.totals:
                return self.totals[key]
        return 1


def _sample_pgn() -> str:
    return """[Event "?"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]

1. e4 e5 2. Nf3 Nc6 3. Bb5 a6 *
"""


def test_cli_annotates_reach_counts(monkeypatch, tmp_path):
    # Patch explorer to return deterministic totals per FEN
    from rep_grow import annotate_reach_counts as arc

    start = chess.Board()
    after_e4 = start.copy(stack=False)
    after_e4.push_san("e4")
    after_e5 = after_e4.copy(stack=False)
    after_e5.push_san("e5")

    from rep_grow.fen import canonical_fen

    FakeExplorer.totals = {
        canonical_fen(start.fen()): 100,
        canonical_fen(after_e4.fen()): 80,
        canonical_fen(after_e5.fen()): 60,
    }

    monkeypatch.setattr(arc, "LichessExplorerApi", FakeExplorer)

    pgn_path = tmp_path / "in.pgn"
    pgn_path.write_text(_sample_pgn(), encoding="utf-8")

    runner = CliRunner()
    out_path = tmp_path / "out.pgn"
    result = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(pgn_path),
            "--side",
            "white",
            "--output",
            str(out_path),
            "--max-concurrency",
            "2",
            "--include-opponent",
        ],
    )

    assert result.exit_code == 0, result.output
    annotated = out_path.read_text(encoding="utf-8")

    # Verify tags are present and parsed
    counts = []
    for line in annotated.splitlines():
        if "[rg:games=" in line:
            count, _ = extract_reach_count(line)
            counts.append(count)
    assert len(counts) >= 1
    assert all(count > 0 for count in counts)


def test_cli_respects_existing_tags(monkeypatch, tmp_path):
    from rep_grow import annotate_reach_counts as arc

    FakeExplorer.totals = {chess.STARTING_FEN: 5}
    monkeypatch.setattr(arc, "LichessExplorerApi", FakeExplorer)

    pgn_with_tag = """[Event "?"]
[Site "?"]
[Date "????.??.??"]
[Round "?"]
[White "?"]
[Black "?"]
[Result "*"]

1. e4 { [rg:games=42] } e5 *
"""
    pgn_path = tmp_path / "tagged.pgn"
    pgn_path.write_text(pgn_with_tag, encoding="utf-8")

    runner = CliRunner()
    result = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(pgn_path),
            "--side",
            "white",
            "--dry-run",
        ],
    )

    assert result.exit_code == 0, result.output
    updated = pgn_path.read_text(encoding="utf-8")
    assert "[rg:games=42]" in updated  # untouched because dry-run

    # Force overwrite (player nodes only by default)
    result_force = runner.invoke(
        click_main,
        [
            "--pgn-file",
            str(pgn_path),
            "--side",
            "white",
            "--force",
        ],
    )
    assert result_force.exit_code == 0, result_force.output
    updated_force = pgn_path.read_text(encoding="utf-8")
    assert "[rg:games=5]" in updated_force
    assert "[rg:games=42]" in updated_force  # opponent node remains
