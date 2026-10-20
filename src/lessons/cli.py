"""lessons CLI — the four decoupled passes plus serve.

Phase 0 scaffold: commands exist and are wired, but each pass is a stub that
reports "not implemented yet" and points at the phase that builds it. This keeps
`lessons --help` honest from day one (requirements §7a.3, design §2) without
pretending work is done.
"""

from __future__ import annotations

import os
from pathlib import Path

import typer

from lessons import __version__

app = typer.Typer(
    name="lessons",
    help="Local-first knowledge graph of learnings from business/history video podcasts.",
    no_args_is_help=True,
    add_completion=False,
)


def _data_dir() -> Path:
    """Per-OS application-data dir for the user's DB/transcripts (design §9a).

    Honors LESSONS_DATA_DIR override; otherwise uses the platform convention.
    """
    override = os.environ.get("LESSONS_DATA_DIR")
    if override:
        return Path(override)
    home = Path.home()
    if os.name == "nt":
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
    elif os.sys.platform == "darwin":  # type: ignore[attr-defined]
        base = home / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME", home / ".local" / "share"))
    return base / "lessons"


def _db_path() -> Path:
    d = _data_dir()
    d.mkdir(parents=True, exist_ok=True)
    return d / "lessons.db"


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
    from lessons.ingest import Ingester
    from lessons.providers import RealYouTubeProvider
    from lessons.store import Store

    store = Store(_db_path())
    try:
        ingester = Ingester(store, RealYouTubeProvider())
        try:
            outcome = ingester.ingest(probe=probe)
        except RuntimeError as e:
            # Honest, tidy message when the heavy ingest extra isn't installed.
            typer.secho(str(e), fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1) from None
        if probe > 0:
            typer.echo(outcome.render())  # type: ignore[union-attr]
        else:
            r = outcome  # type: ignore[assignment]
            typer.echo(
                f"ingested {r.videos_ingested} new video(s) across {r.channels} channel(s); "
                f"skipped {r.videos_skipped_existing} existing, {r.videos_deduped} duplicate(s); "
                f"whisper used {r.whisper_used} time(s)."
            )
    finally:
        store.close()


@app.command()
def extract(
    model: str = typer.Option(
        "llama3.1", "--model", help="Ollama model to use (default local Ollama)."
    ),
) -> None:
    """Pass B — turn transcripts into a structured graph with a local LLM."""
    from lessons.extract import Extractor
    from lessons.providers import OllamaClient
    from lessons.store import Store

    store = Store(_db_path())
    try:
        try:
            llm = OllamaClient(model=model)
        except RuntimeError as e:
            typer.secho(str(e), fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1) from None
        r = Extractor(store, llm).extract()
        typer.echo(
            f"extracted {r.nodes_added} node(s), {r.edges_added} edge(s) "
            f"({r.tension_edges} tension) from {r.videos_processed} video(s); "
            f"skipped {r.videos_skipped_same_version} already at this prompt version, "
            f"{r.chunks_invalid_skipped} invalid chunk(s)."
        )
    finally:
        store.close()


@app.command()
def enrich() -> None:
    """Pass B2 (optional) — attach external biographies to person nodes."""
    _todo("enrich", "Phase 7")


@app.command()
def serve(
    port: int = typer.Option(8787, "--port", help="Port to bind on 127.0.0.1."),
) -> None:
    """Pass C — serve the splash, situation-first search, and graph explorer on 127.0.0.1."""
    try:
        import uvicorn
    except ImportError:
        typer.secho(
            "uvicorn not installed; pip install -e '.[serve]'",
            fg=typer.colors.YELLOW,
            err=True,
        )
        raise typer.Exit(code=1) from None

    from lessons.store import Store
    from lessons.web import build_app

    store = Store(_db_path())
    app_ = build_app(store)
    typer.echo(f"serving lessons on http://127.0.0.1:{port}  (Ctrl-C to stop)")
    # Always bind loopback explicitly — never 0.0.0.0 (R§5.1).
    uvicorn.run(app_, host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    app()
