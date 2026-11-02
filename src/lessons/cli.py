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
    source: str = typer.Option(
        "youtube", "--source", help="Ingest source: 'youtube' or 'rss'."
    ),
    feed: str = typer.Option(
        "", "--feed", help="RSS: a known slug (founders / david-senra / "
        "how-to-take-over-the-world) or a feed URL. Required when --source rss."
    ),
    limit: int = typer.Option(
        0, "--limit", help="Cap episodes ingested (0 = all). Use for a thin-slice run."
    ),
) -> None:
    """Pass A — download transcripts + metadata from the configured channels."""
    from lessons.ingest import Ingester
    from lessons.store import Store

    store = Store(_db_path())
    try:
        if source == "rss":
            from lessons.rss import KNOWN_FEEDS, RssProvider

            feed_url = KNOWN_FEEDS.get(feed, feed)
            if not feed_url:
                typer.secho(
                    "--source rss requires --feed (a known slug or a URL)",
                    fg=typer.colors.YELLOW, err=True,
                )
                raise typer.Exit(code=1) from None
            provider = RssProvider(feed_url)
            channels = [{"handle": feed or feed_url, "url": feed_url}]
        else:
            from lessons.providers import RealYouTubeProvider

            provider = RealYouTubeProvider()
            channels = None  # default three channels

        ingester = Ingester(store, provider)
        try:
            outcome = ingester.ingest(channels=channels, probe=probe, limit=limit)
        except RuntimeError as e:
            # Honest, tidy message when the heavy ingest extra isn't installed.
            typer.secho(str(e), fg=typer.colors.YELLOW, err=True)
            raise typer.Exit(code=1) from None
        if probe > 0:
            typer.echo(outcome.render())  # type: ignore[union-attr]
        else:
            r = outcome  # type: ignore[assignment]
            typer.echo(
                f"ingested {r.videos_ingested} new episode(s); "
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
def enrich(
    refresh: bool = typer.Option(
        False, "--refresh", help="Re-fetch bios even if a person already has one."
    ),
) -> None:
    """Pass B2 (optional) — attach external biographies to person nodes."""
    from lessons.enrich import Enricher
    from lessons.providers import WikipediaBioProvider
    from lessons.store import Store

    store = Store(_db_path())
    try:
        r = Enricher(store, WikipediaBioProvider(), refresh=refresh).enrich()
        typer.echo(
            f"enriched {r.enriched} of {r.people_total} person node(s); "
            f"skipped {r.skipped_existing} already-enriched, {r.not_found} not found."
        )
    finally:
        store.close()


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

    from lessons.seed import seed_if_empty
    from lessons.store import Store
    from lessons.web import build_app

    store = Store(_db_path())
    seeded = seed_if_empty(store)
    if seeded:
        typer.echo(
            f"first run — seeded the shipped graph "
            f"({seeded['nodes']} node(s), {seeded['edges']} edge(s))."
        )
    app_ = build_app(store)
    typer.echo(f"serving lessons on http://127.0.0.1:{port}  (Ctrl-C to stop)")
    # Always bind loopback explicitly — never 0.0.0.0 (R§5.1).
    uvicorn.run(app_, host="127.0.0.1", port=port, log_level="info")


@app.command("seed-export")
def seed_export(
    out: str = typer.Option(
        "", "--out", help="Output path (default: repo seed/lessons-seed.json)."
    ),
) -> None:
    """Export the current graph to the shipped JSON seed (the maintainer's notes).

    Run this to refresh seed/lessons-seed.json from your working DB before
    committing. Ships nodes/edges/mentions (short quotes), never transcript text.
    """
    import json

    from lessons.seed import SEED_PATH
    from lessons.store import Store

    store = Store(_db_path())
    try:
        data = store.export_seed()
    finally:
        store.close()
    out_path = Path(out) if out else SEED_PATH
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    typer.echo(
        f"exported {len(data['nodes'])} node(s), {len(data['edges'])} edge(s), "
        f"{len(data['mentions'])} mention(s) to {out_path}"
    )


@app.command()
def publish(
    out_dir: str = typer.Option(
        "dist", "--out-dir", help="Directory to write graph-bundle.json + version.json into."
    ),
    content_version: int = typer.Option(
        -1,
        "--content-version",
        help="Explicit content version. Default (-1) bumps the stored counter by one.",
    ),
) -> None:
    """Publish the current graph as a versioned, integrity-checked bundle.

    This is the maintainer's content-delivery step (the reader app consumes the
    output): it exports the graph, stamps a monotonic content_version + a
    SHA-256 integrity hash, and writes two files —
      * graph-bundle.json  — the full bundle the app downloads and imports
      * version.json        — the tiny record the app polls to detect a newer graph
    Ships short cited quotes only, never audio or transcripts.
    """
    import json

    from lessons.bundle import build_bundle, verify_bundle, version_info
    from lessons.store import Store

    store = Store(_db_path())
    try:
        if store.is_empty():
            typer.secho(
                "nothing to publish — the graph is empty (run ingest + extract first)",
                fg=typer.colors.YELLOW, err=True,
            )
            raise typer.Exit(code=1) from None
        payload = store.export_seed()
        cv = content_version if content_version >= 0 else store.bump_content_version()
    finally:
        store.close()

    bundle = build_bundle(payload, content_version=cv)
    manifest = verify_bundle(bundle)  # fail loudly here, never ship an unverifiable file

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    bundle_path = out / "graph-bundle.json"
    version_path = out / "version.json"
    bundle_path.write_text(json.dumps(bundle, ensure_ascii=False), encoding="utf-8")
    version_path.write_text(
        json.dumps(version_info(manifest), indent=2, ensure_ascii=False), encoding="utf-8"
    )
    size_kb = bundle_path.stat().st_size / 1024
    typer.echo(
        f"published content v{manifest.content_version} "
        f"(schema v{manifest.schema_version}, {manifest.counts.get('nodes', 0)} nodes, "
        f"{manifest.counts.get('edges', 0)} edges, {size_kb:.1f} KB)\n"
        f"  bundle:  {bundle_path}\n"
        f"  version: {version_path}\n"
        f"  sha256:  {manifest.payload_sha256[:16]}…"
    )


@app.command("bundle-verify")
def bundle_verify(
    bundle: str = typer.Argument(..., help="Path to a graph-bundle.json to verify."),
    emit_version: str = typer.Option(
        "", "--emit-version",
        help="Also write a derived version.json to this path (kept consistent "
        "with the verified bundle's manifest).",
    ),
) -> None:
    """Verify a graph bundle's integrity + version guards, and optionally emit a
    version.json derived from it.

    This is the release gate: it exits non-zero on a corrupt/tampered/too-new
    bundle, so a CI release workflow can refuse to publish one. Deriving
    version.json from the verified bundle (rather than trusting a separate file)
    guarantees the two never drift."""
    import json

    from lessons.bundle import BundleError, load_bundle_text, version_info

    path = Path(bundle)
    if not path.exists():
        typer.secho(f"bundle not found: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2) from None
    try:
        manifest, _payload = load_bundle_text(path.read_text(encoding="utf-8"))
    except BundleError as e:
        typer.secho(f"bundle REJECTED: {e}", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=1) from None

    typer.echo(
        f"bundle OK — content v{manifest.content_version}, schema v{manifest.schema_version}, "
        f"{manifest.counts.get('nodes', 0)} nodes, {manifest.counts.get('edges', 0)} edges"
    )
    if emit_version:
        vpath = Path(emit_version)
        vpath.parent.mkdir(parents=True, exist_ok=True)
        vpath.write_text(
            json.dumps(version_info(manifest), indent=2, ensure_ascii=False), encoding="utf-8"
        )
        typer.echo(f"wrote {vpath}")


if __name__ == "__main__":
    app()
