"""lessons CLI — the four decoupled passes plus serve.

Phase 0 scaffold: commands exist and are wired, but each pass is a stub that
reports "not implemented yet" and points at the phase that builds it. This keeps
`lessons --help` honest from day one (requirements §7a.3, design §2) without
pretending work is done.
"""

from __future__ import annotations

import typer

from lessons import __version__

app = typer.Typer(
    name="lessons",
    help="Local-first knowledge graph of learnings from business/history video podcasts.",
    no_args_is_help=True,
    add_completion=False,
)


def _todo(command: str, phase: str) -> None:
    """Stub reporter — honest placeholder until the phase lands."""
    typer.echo(f"`lessons {command}` is not implemented yet (scaffold). Built in {phase}.")
    raise typer.Exit(code=0)


@app.command()
def version() -> None:
    """Print the installed version."""
    typer.echo(f"lessons {__version__}")


@app.command()
def ingest(
    probe: int = typer.Option(
        0,
        "--probe",
        help="Dry-run: sample N videos/channel and report caption coverage + latency (Phase 2.7).",
    ),
) -> None:
    """Pass A — download transcripts + metadata from the configured channels."""
    _todo("ingest", "Phase 2")


@app.command()
def extract() -> None:
    """Pass B — turn transcripts into a structured graph with a local LLM."""
    _todo("extract", "Phase 3")


@app.command()
def enrich() -> None:
    """Pass B2 (optional) — attach external biographies to person nodes."""
    _todo("enrich", "Phase 7")


@app.command()
def serve(
    port: int = typer.Option(8787, "--port", help="Port to bind on 127.0.0.1."),
) -> None:
    """Pass C — serve the splash, situation-first search, and graph explorer on 127.0.0.1."""
    _todo("serve", "Phase 5")


if __name__ == "__main__":
    app()
