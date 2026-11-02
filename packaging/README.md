# Packaging — the read-only reader app

Bundles the **reader** (FastAPI + uvicorn + the static UI + a baked graph
bundle) into a self-contained app with [PyInstaller]. Reader-only: no
ingest/extract/Ollama/Whisper — the graph ships as the baked bundle and updates
via the background auto-update wired into `serve` startup.

## Build

```
cd packaging
uv run --with pyinstaller --with fastapi --with uvicorn \
    pyinstaller reader.spec --noconfirm
```

Produces `dist/lessons/` (onedir). Run `dist/lessons/lessons` — it boots uvicorn
on a free loopback port and opens your browser at it.

## The baked bundle

`packaging/graph-bundle.json` is the graph shipped *inside* the app, so it is
populated on first launch with no network. Regenerate it from your working graph:

```
lessons publish --out-dir packaging --strict   # writes graph-bundle.json (+ version.json)
```

(`--strict` enforces the entity-resolution gate — don't ship un-reviewed noise.)
The committed one is built from the repo seed (the curated starter graph).

## Honest limitations (v1)

This produces a **working local app**, not a distributable release:

- **Not signed or notarized.** macOS Gatekeeper will warn ("cannot be opened").
  Signing + notarization need an Apple Developer cert ($99/yr) — a separate step.
- **Single-arch.** The build targets the build machine's architecture. A
  `universal2` (Intel + Apple Silicon) build is a follow-up.
- **Console build.** `console=True` in the spec keeps a terminal window so
  startup/errors are visible; flip it for a windowed app once stable.

## Auto-update

Set the update channel so the packaged app pulls newer graphs:

```
LESSONS_UPDATE_VERSION_URL=https://…/version.json
LESSONS_UPDATE_BUNDLE_URL=https://…/graph-bundle.json
```

(Or the `serve --update-*-url` flags.) The check runs in the background at
startup and is non-fatal when offline.
