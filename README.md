# lessons

A **local-first knowledge graph** of learnings from business/history video podcasts. Describe a situation you're facing and get pointers to what great founders and historical figures actually did about it — with the exact quote, episode, and the YouTube minute where they said it.

> **Thesis:** great leaders mostly *diverged* on the hard problems. This tool doesn't manufacture fake "common threads" — it shows you *how differently* they solved the *same* situation, each backed by the source. See [`docs/SPEC.md`](docs/SPEC.md).

---

## Status

🚧 **Early scaffold (Phase 0).** The commands exist and `lessons --help` works; the passes are stubs until their phase lands. Build plan: [`docs/tasks.md`](docs/tasks.md).

## How it works — four decoupled passes

```
ingest   download transcripts + metadata (no LLM)          -> data/
extract  local LLM turns transcripts into a graph           -> nodes/edges
enrich   (optional) attach external bios to people          -> bios
serve    situation-first search + graph explorer on 127.0.0.1
```

Each pass is its own command and is independently resumable. Transcripts on disk are the durable raw material; everything else is recomputable.

## Setup (developers)

Requires **Python 3.11+**.

```
git clone https://github.com/OWNER/lessons
cd lessons
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
lessons --help
```

Install a pass's heavy dependencies only when you need them:

```
pip install -e ".[ingest]"    # yt-dlp, youtube-transcript-api, faster-whisper
pip install -e ".[extract]"   # ollama client, pydantic
pip install -e ".[serve]"     # fastapi, uvicorn
```

Copy `.env.example` to `.env` if you want the optional cloud-LLM path; the app runs fully local with no keys.

## Install as an app

Once `lessons serve` is running, Chromium browsers offer a one-click **Install as app** from the splash page. Safari (macOS/iPad) installs via **Share → Add to Dock / Add to Home Screen**. Firefox desktop has no install — pin it as a tab. (Per-browser detail: [`docs/design.md`](docs/design.md) §9a.)

## What is NOT committed

No secrets, no scraped data. `data/` (transcripts, audio, the SQLite DB, bios, model cache) and `.env` are gitignored — you reproduce your own local DB by running the passes.

## Docs

- [`docs/SPEC.md`](docs/SPEC.md) — product narrative & thesis
- [`docs/requirements.md`](docs/requirements.md) — user stories + acceptance criteria
- [`docs/design.md`](docs/design.md) — architecture, schema, flows
- [`docs/tasks.md`](docs/tasks.md) — the phased build plan

## License

[MIT](LICENSE).

## Attribution & sources

This tool indexes and **links to** publicly available podcast content; it does not redistribute transcripts or audio. External biographical facts come from Wikidata (CC0) and Wikipedia (CC BY-SA) with attribution. Every claim in the app cites its source.
