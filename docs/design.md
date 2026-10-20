# lessons — Design

Technical design for the requirements in `docs/requirements.md`. Local-first v1; v2 hosting sketched at the end.

---

## 1. Architecture overview

Four decoupled, resumable passes over a single SQLite graph:

```
Pass A  ingest   yt-dlp + youtube-transcript-api (+ faster-whisper fallback)  ->  transcripts + metadata
Pass B  extract  local LLM (Ollama) over transcripts                          ->  nodes + edges + mentions
Pass B2 enrich   Wikipedia/Wikidata per person node (optional)                ->  bios
Pass C  serve    FastAPI + Cytoscape.js on 127.0.0.1                          ->  situation-first search + graph explore
```

Each pass is its own CLI command and reads/writes the same `data/lessons.db`. Transcripts on disk are the durable raw material; everything else is recomputable from them.

**Why decoupled:** different failure modes (network vs. model), different speeds (minutes vs. hours), and prompt iteration without re-download. See `requirements.md` §7.

---

## 2. Components

| Component | Responsibility | Key libs |
|---|---|---|
| `lessons.ingest` | Enumerate channels, fetch transcripts/metadata, whisper fallback, de-dup, resumable | `yt-dlp`, `youtube-transcript-api`, `faster-whisper` |
| `lessons.extract` | LLM transcript → structured nodes/edges with provenance; prompt-versioned | `ollama` client (+ optional cloud client), `pydantic` for schema validation |
| `lessons.enrich` | Fetch external bios per person node; store with attribution | `requests`/`httpx`, Wikipedia/Wikidata APIs |
| `lessons.store` | SQLite schema, migrations, read/write DAL, FTS index | `sqlite3`/`sqlalchemy` |
| `lessons.search` | Situation→node mapping, divergence surfacing, ranking | embeddings (local model) + SQLite FTS fallback |
| `lessons.web` | FastAPI app, read-only graph API, static UI | `fastapi`, `uvicorn`, Cytoscape.js |
| `lessons.cli` | `ingest` / `extract` / `enrich` / `serve` / `publish` (v2) | `typer`/`click` |

---

## 3. Data model (SQLite)

Illustrative DDL; exact columns finalize in implementation. Invariant: **podcast-origin rows trace to video+minute+quote; external rows carry source+license.**

```sql
CREATE TABLE channels (
  id TEXT PRIMARY KEY, handle TEXT, url TEXT, name TEXT
);

CREATE TABLE videos (
  id TEXT PRIMARY KEY,            -- YouTube video id
  channel_id TEXT REFERENCES channels(id),
  title TEXT, published_at TEXT, duration_s INTEGER, url TEXT,
  transcript_path TEXT, ingested_at TEXT
);

CREATE TABLE transcripts (
  video_id TEXT PRIMARY KEY REFERENCES videos(id),
  text TEXT,
  has_captions INTEGER,          -- 1 = YouTube captions, 0 = whisper
  source TEXT                     -- 'captions' | 'whisper'
);

CREATE TABLE nodes (
  id TEXT PRIMARY KEY,
  type TEXT,                      -- lesson|principle|person|company|era|experience|situation
  label TEXT, summary TEXT,
  origin TEXT DEFAULT 'podcast'   -- 'podcast' | 'external'
);

CREATE TABLE edges (
  id TEXT PRIMARY KEY,
  src_node_id TEXT REFERENCES nodes(id),
  dst_node_id TEXT REFERENCES nodes(id),
  relation TEXT,                  -- cites|applied|founded|aligns|contradicts|contrasts|produced_lesson|addresses_situation
  confidence REAL
);

CREATE TABLE mentions (                 -- podcast provenance
  id TEXT PRIMARY KEY,
  node_id TEXT REFERENCES nodes(id),
  video_id TEXT REFERENCES videos(id),
  timestamp_s INTEGER,
  quote TEXT
);

CREATE TABLE bios (                      -- external enrichment layer
  id TEXT PRIMARY KEY,
  person_node_id TEXT REFERENCES nodes(id),
  source TEXT, source_url TEXT, license TEXT,
  title TEXT, summary TEXT, fetched_at TEXT
);

CREATE TABLE extract_runs (
  id TEXT PRIMARY KEY, prompt_version TEXT, model TEXT, started_at TEXT
);

-- Full-text search over transcripts (works before extraction)
CREATE VIRTUAL TABLE transcripts_fts USING fts5(video_id, text);
```

**Provenance rule enforced in the DAL:** a `nodes` row with `origin='podcast'` must have ≥1 `mentions` row; a `person` with a bio must have a `bios` row carrying a non-null `license`.

---

## 4. Situation-first retrieval (the core flow)

```
user types a situation
        |
   [embed query]  --(semantic)-->  nearest situation node(s)
        |  (fallback)
   [FTS keyword match]
        |
   traverse: situation --addresses_situation--> lessons/experiences
        |
   expand: pull contradicts/contrasts neighbors (divergence)
        |
   rank: relevance x cross-source recurrence
        |
   render result cards:
     - lesson/experience + leader + episode
     - source quote
     - deep link  https://youtu.be/<id>?t=<timestamp_s>
     - (optional) "About <leader>" bio panel, badged external
```

Design choices:
- **Semantic + keyword hybrid** so search degrades gracefully before extraction is deep (keyword-only still returns transcript hits).
- **Divergence is surfaced, not hidden** — the result set is allowed (encouraged) to contain contradictory takes.
- **No synthesis** — the service returns pointers; it never generates advice text. (Honors `requirements.md` §4.5.)

---

## 5. Extraction design

- **Chunking:** long transcripts are chunked (by time window / token budget) with overlap so lessons spanning a boundary aren't lost; each chunk carries its start timestamp so mentions keep accurate minutes.
- **Structured output:** the LLM is prompted for JSON validated against a Pydantic schema (node/edge shapes). Invalid output is retried, then skipped with a logged reason rather than corrupting the graph.
- **Prompt versioning:** `prompt_version` string bumps whenever the prompt changes; `extract` compares it to prior `extract_runs` to decide re-extract vs. skip.
- **Entity resolution:** nodes are de-duplicated by (type, normalized label) so "Rockefeller" and "John D. Rockefeller" collapse to one `person`; a simple alias table can back this later.
- **Tension detection:** the prompt explicitly asks for opposing stances on a shared situation, emitting `contradicts`/`contrasts` edges — this is a first-class extraction target, not a side effect.

---

## 6. Ingest design

- **Enumeration** via `yt-dlp` channel/`/videos` playlist (no API key).
- **Transcript** via `youtube-transcript-api`; on failure or absence, `yt-dlp` audio → `faster-whisper`.
- **De-dup** by video id, then by normalized title + duration bucket across channels.
- **Resumability** via a `videos.ingested_at` check; re-runs skip completed ids.
- **Rate-limit handling:** exponential backoff + jitter on caption fetches; a configurable concurrency cap.
- **Dry-run probe** (`lessons ingest --probe N`) samples N videos per channel and reports caption coverage % and median per-video latency — this produces the real full-backfill time estimate instead of a guess.

### 6a. RSS source (preferred) + retention model

**Why RSS over YouTube.** YouTube actively IP-blocks `youtube-transcript-api` caption requests, and downloading YouTube audio is the most explicitly ToS-prohibited action. A podcast's **public RSS feed** is the better source on both axes: the audio enclosures are published *for* podcast clients to fetch (robust, not blocked), and consuming a feed as intended is far more defensible. All three target shows expose audio via Megaphone feeds and publish **no** transcripts, so the path is uniform: **RSS → download MP3 → local Whisper → extract**.

- `lessons ingest --source rss --feed <slug|url> [--limit N]`. Known slugs: `founders`, `david-senra`, `how-to-take-over-the-world`.
- `RssProvider` matches the `YouTubeProvider` seam (stdlib feed parsing, no new dep), so the Ingester runs unchanged. `fetch_transcript` always returns `None` (feeds carry audio) → the Ingester falls through to `whisper_transcribe`, which downloads the enclosure, transcribes with faster-whisper into timestamped lines, and **deletes the audio**.
- **Dependency pin:** `av>=11,<14` — faster-whisper 1.2.x calls a PyAV kwarg (`metadata_errors`) that PyAV ≥14 removed. Verified end-to-end on a real 4.5-min episode (66s, 47 timestamped lines).

**Retention model (the graph is the product; everything upstream is a disposable build input):**

| Artifact | Default | Why |
|---|---|---|
| MP3 audio | **always deleted** after transcription | riskiest to hoard (a full catalog copy); pure intermediate |
| full transcript | kept only for re-extraction | the re-extraction cache — re-run Pass B at a new prompt with no re-download/re-Whisper |
| graph + short quotes | kept | this *is* the app; also the cleanest fair-use posture (derived, short quotes, deep links) |

---

## 7. Web app / serve design

- **FastAPI** serves a small read-only JSON API: `/search` (situation-first), `/fts`, `/graph`, `/node/{id}`, `/bio/{person}`, `/splash` (seeded leader elements).
- **Cytoscape.js** front-end for the graph view; a dedicated situation search page and a splash/landing page.
- **Bind:** `uvicorn --host 127.0.0.1 --port <port>` explicitly; never `0.0.0.0` in v1.
- **Origin badge:** every rendered claim shows a podcast badge (with video deep link) or an external badge (with source + license link) so users always know provenance.

### 7a. Citation model (cross-cutting)

Every renderable unit carries a structured citation object, assembled in the DAL so the UI never improvises attribution. Two shapes:

```
PodcastCitation {            ExternalCitation {
  channel_name, channel_url,   source_name,       // "Wikipedia"
  episode_title, published_at, source_url,
  video_url,                   license            // "CC BY-SA"
  timestamp_s,               }
  deep_link,   // https://youtu.be/<id>?t=<s>
  host,        // e.g. "David Senra"  (artist/host)
  leaders[]    // e.g. ["John D. Rockefeller"] (subjects)
}
```

- `host` (the channel artist) and `leaders` (the subjects discussed) are **separate fields**, never conflated — satisfies requirements §4a.6.
- The DAL refuses to emit a podcast unit without a resolvable `PodcastCitation` (ties to the §3 provenance invariant), so an uncitable node is simply not served (§4a.5).
- Podcast vs. external citations render with distinct styling and iconography (§4a.2–3); all URLs are real `<a>` links (§4a.4).

### 7b. Splash / landing page

```
            name          "quote…"
        ┌──────────────────────────────┐   ← leader chips float in the
        │   What is your current        │     ambient layer around (never
   face │   situation or challenge…?    │ face  over) the search hero
        │  [ ___________________ ] (→)  │
        └──────────────────────────────┘
      "quote…"        name         face
```

- The search box is the hero; an **ambient layer** of leader elements (name chips, profile avatars, short pull-quotes) floats around it on a lower z-index with reduced opacity, so it never competes for the primary action (§4b.4).
- Elements come from `/splash`: real `person` nodes + a representative `mentions` quote once extraction has run; before that, a **seeded set** (the three channel hosts + a few well-known leaders) so the page is never empty (§4b.3).
- Motion is CSS-driven gentle drift, **gated on `prefers-reduced-motion`** → static layout when the user opts out (§4b.5). A pause control appears if drift runs continuously.
- Avatars: prefer public-domain/CC images (Wikidata image claim), else an **initials avatar** fallback; external images are linked/attributed, never re-hosted beyond fair local caching (§4b.6).
- **Download/get-app buttons** sit in a quiet corner of the splash (header or footer, not competing with the search hero): **"Install as app"** (PWA, Chromium) + **"View on GitHub"**. Behavior and fallbacks per §9a. No OS-specific installer buttons.

### 7c. Results-page UI/UX quality bar

A small, explicit design system so the results page clears the bar in §4c:

- **Card:** one lesson/experience per card. Internal padding on a consistent scale (e.g. 20–24px); vertical rhythm between cards generous and uniform (e.g. 16px gap). Visual hierarchy top-to-bottom: **leader + episode → the quote (centerpiece) → citation line**.
- **Quote as centerpiece:** larger, slightly looser line-height, treated as a pull-quote; the citation sits beneath as a quieter secondary line (§4c.6).
- **Lines & borders:** hairline dividers (1px, low-contrast), no heavy boxes; rely on spacing and type weight for separation, not thick rules (§4c.2).
- **Citations & URLs:** styled link with a visible host/source label (e.g. `youtu.be · 42:15` or `Wikipedia · CC BY-SA`), clearly clickable, never raw or illegibly truncated (§4c.3).
- **Theme tokens only:** all color via CSS variables (background + text set together on every surface) so it survives dark mode — no hard-coded hex (§4c.4). This matches the dashboard widget theming rules if a results view is ever rendered as a widget.
- **Divergence pairing:** contrasting takes on the same situation render as a visually linked pair/group (e.g. a two-column "vs." band or a joined card) so the disagreement is legible at a glance (§4c.5).
- **Responsive:** single-column fluid cards that stay legible from laptop down to tablet width, forward-compatible with the v2 PWA (§4c.7).

---

## 8. Project layout

```
lessons/
  pyproject.toml
  README.md
  LICENSE
  .env.example
  .gitignore                 # data/, .env
  docs/
    SPEC.md  requirements.md  design.md
  src/lessons/
    cli.py  ingest.py  extract.py  enrich.py  store.py  search.py  web/
    prompts/extract_v1.txt
  tests/
    fixtures/sample_transcript.json
    test_store.py  test_extract.py  test_search.py
  data/                      # gitignored: transcripts/, audio/, lessons.db
```

---

## 9. Testing strategy
- **Unit:** store DAL (schema invariants), extract JSON validation, search ranking — all against the committed fixture, no network.
- **CI:** lint (ruff) + tests only; never runs ingest/extract/enrich.
- **Fixture:** one small `sample_transcript.json` + a tiny expected node/edge set so extract logic is testable without an LLM (mock the LLM call, assert the parse/validate/store path).

---

## 9a. Distribution: PWA + open source (no native installers)

**Decision (supersedes the earlier PyInstaller/Briefcase native-bundle plan):** cross-platform reach comes from the **browser**, not three OS installers. Two distribution surfaces:

1. **Installable PWA (Chromium).** The FastAPI app serves a **web app manifest** (`manifest.webmanifest`: name, icons, `display: standalone`, theme/background color) and registers a **service worker** that caches the app shell + viewed content for offline reference. Chromium browsers (Chrome, Edge, Brave, Arc) then offer "Install app," placing it in the dock/Start menu on macOS, Windows, and Linux alike — one artifact, three OSes, no signing/notarization problem.
2. **Open source on GitHub.** Developers clone and run (`pip install -e .` + `lessons serve`). The splash page's "View on GitHub" button links here.

**Why this over native bundles.** It removes the whole PyInstaller/Briefcase build matrix, code-signing, notarization, and per-OS installer maintenance — the heaviest, most brittle part of last iteration's plan — in exchange for "install from a Chromium browser." For a local-first v1 aimed at developers/maintainers that's the right trade; the frictionless public install lands in v2 when a public origin exists.

**Install-affordance by engine (splash).** Install support is not uniform, so the button adapts to the browser and never renders a dead action:

| Engine | App runs? | Offline SW? | Install route | Splash shows |
|---|---|---|---|---|
| **Chromium** (Chrome/Edge/Brave/Arc) | yes | yes | `beforeinstallprompt` → one-click | **"Install as app"** button → `prompt()` |
| **Safari** (macOS Sonoma+, iPadOS/iOS) | yes | yes | **manual** — Share → "Add to Dock" / "Add to Home Screen" (no JS event) | concise **"Add to Dock / Home Screen"** instructions |
| **Firefox desktop** | yes | yes | none (install removed) | brief **"pin as a tab"** note |

The front-end feature-detects `beforeinstallprompt` and UA-sniffs for the Safari/iOS instruction copy; "View on GitHub" is always a plain styled link regardless. **Safari is a first-class path, not a fallback** — it is the *only* install route for iPhone/iPad, which the v2 roadmap names explicitly — so the app ships `apple-touch-icon` + `apple-mobile-web-app-capable` / `apple-mobile-web-app-status-bar-style` meta tags for a clean iOS home-screen result.

**Serving model.** v1: the PWA installs from the local self-hosted origin (`127.0.0.1` or a LAN host the maintainer runs). v2: it installs from the public read-only API origin (S3+CloudFront). The manifest/service worker are written once and serve both.

**Repo hygiene.** No build binaries committed; the PWA is *served*, and source is the distributed form (requirements §7a.6).

---

## 10. v2 hosting (sketch, not built in v1)
- Local ingest/extract/enrich stay on the maintainer's machine; a `lessons publish` command exports the derived graph.
- Read-only graph served from a **static export + CDN** (cheapest) or a **thin API server** if the graph outgrows client-side delivery. Likely AWS S3 + CloudFront (existing deploy skill).
- Web app ships a PWA manifest + service worker (installable on laptop/iPad, offline cache of viewed graph).
- **Install on each target device:** laptop (Chromium) = one-click button; **iPad/iPhone = Safari "Add to Home Screen"** — this is the *only* iOS install route, so the public origin (HTTPS, which iOS requires for home-screen PWAs) and the Safari meta tags (§9a) are what make the named iPad target real. Firefox desktop remains run-as-a-tab.
- A public **HTTPS** origin is a hard prerequisite for installable PWAs on iOS/iPadOS (Safari will not offer "Add to Home Screen" over plain HTTP), so S3 + CloudFront (TLS by default) is the natural fit.
- The same read-only API is the foundation for a future native mobile app — a client, not a rewrite.

---

## 11. Decisions pending (from requirements §10)
1. License (MIT vs Apache-2.0).
2. Extraction default model (Ollama tier).
3. Embedding model for situation matching (local, e.g. a small sentence-transformer).
4. Bio source balance (Wikidata CC0 facts vs Wikipedia CC BY-SA prose).
