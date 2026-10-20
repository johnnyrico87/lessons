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
git clone https://github.com/johnnyrico87/lessons
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

## Intended use, content & attribution

**`lessons` is a personal research tool, not a content service.** It is meant to
run locally so an individual can learn from podcasts they already follow.

- The **graph holds no MP3 or full-transcript data** — only a derived set of
  nodes/edges and **short quotations that link back** to the source at its
  timestamp.
- Podcast **audio is downloaded only transiently to transcribe locally, then
  deleted.** Full transcripts are not kept by default and are never committed.
- Source material belongs to its creators; every item **cites and links to** the
  original. Prefer sources published for consumption (a podcast's public **RSS
  feed**) over scraping platforms whose terms forbid it.
- External bios come from Wikidata (CC0) and Wikipedia (CC BY-SA), attributed
  in-app, never committed.

See [`NOTICE.md`](NOTICE.md) for the full statement of intent, retention, and
your responsibilities as a user. This is design intent, not legal advice.
