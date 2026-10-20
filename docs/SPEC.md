# lessons — Functional Specification

> A local-first tool that ingests transcripts from business/history video podcasts, extracts "learnings" with a local LLM, builds a knowledge graph, and lets you explore it — **locally in v1, then as a hosted web app + installable PWA (v2), and a mobile quick-reference app (future).**

**Status:** Draft · **License (planned):** MIT · **Repo:** open-source on GitHub
**Roadmap:** v1 local-first → v2 hosted web app + PWA → future native mobile

---

## 1. Purpose & Goal

Build a **local, open-source** tool to explore the *learnings* from history and business video podcasts — the lessons, principles, people, companies, and eras discussed — as a browsable **knowledge graph**, not just a search box.

Initial sources (YouTube channels):

| Channel | URL |
|---|---|
| David Senra | https://www.youtube.com/@DavidSenra/videos |
| Founders Podcast | https://www.youtube.com/@founderspodcast1/videos |
| TakeOver Pod | https://www.youtube.com/@TakeOverPod/videos |

> **De-dup note:** David Senra's *Founders* show and `@founderspodcast1` may be the same content re-posted. The ingest pass must detect and collapse duplicate videos (by video ID and by title/duration similarity) so the graph isn't doubled.

---

## 2. Design Principles

1. **Local-first.** Everything runs on `127.0.0.1`. No cloud dependency required to use the tool.
2. **Clone-and-go.** A stranger can `git clone`, follow the README, and get a working app — no paid keys required.
3. **No secrets, no scraped data in the repo.** API keys live in `.env` (gitignored); transcripts/audio/DB live in `data/` (gitignored). The repo ships *code + schema*, each user builds their own local DB.
4. **Resumable, decoupled passes.** Download, extract, and serve are independent so a failure in one never wastes another's work.
5. **LLM optional for Step 1, required only for the graph.** You always get searchable transcripts; the "learnings graph" is an opt-in enrichment.

---

## 3. Architecture — Three Passes

```
Pass A (ingest):   yt-dlp / youtube-transcript-api  ->  raw transcripts saved to disk + SQLite
                   (fast, network-bound, NO LLM, resumable)

Pass B (extract):  local LLM (Ollama) reads each saved transcript  ->  structured nodes + edges
                   (slow, CPU/GPU-bound, NO network, resumable)

Pass B2 (enrich):  fetch external bios (Wikipedia/Wikidata) for each person node  ->  bio layer
                   (optional, network-bound, resumable — see §8)

Pass C (serve):    local web app reads the graph  ->  you explore
```

Kept separate on purpose:
- **Different failure modes** — network/rate-limit vs. model/prompt. Decoupled = each independently resumable.
- **Speed mismatch** — downloads take minutes; local LLM extraction takes hours. The network step never blocks on the model.
- **Prompt iteration is free of re-downloads** — tweak the extraction prompt and re-run Pass B over transcripts already on disk.
- **Transcripts are the durable raw material** — everything downstream is recomputable from them.

The explore app reads whatever the graph currently holds, so you can browse raw transcripts right after Pass A and watch the graph fill in as Pass B runs.

---

## 4. Technology Stack

| Layer | Choice | Why |
|---|---|---|
| Channel enumeration + metadata + audio | `yt-dlp` | No API key, robust, gives titles/dates/durations |
| Transcript text | `youtube-transcript-api` | Fast, no key, pulls existing captions |
| Transcript fallback (no captions) | `faster-whisper` | Local speech-to-text, free |
| Entity/lesson extraction | **LLM — Ollama default, cloud opt-in** | Repo runs for anyone with no paid key |
| Graph store | **SQLite** (nodes/edges tables) | Zero-install, clone-and-go |
| Explore app | FastAPI + Cytoscape.js, bound to `127.0.0.1` only | Visual graph, no cloud |

> **No LLM needed for Step 1.** Captions already exist as text; `youtube-transcript-api`/`yt-dlp` just *download* them, and `faster-whisper` is speech-to-text (not an LLM) for gaps. The LLM appears only in Pass B.

---

## 5. Functional Requirements

### 5.1 Pass A — Ingest (`lessons ingest`)
- Enumerate all videos for each configured channel via `yt-dlp`.
- For each video: capture metadata (id, title, channel, publish date, duration, URL).
- Fetch transcript via `youtube-transcript-api`; if no captions, download audio and transcribe with `faster-whisper`.
- Persist transcript to disk (`data/transcripts/<video_id>.txt` or `.json` with timestamps) and a row in SQLite.
- **Resumable:** skip videos already ingested; re-runnable without re-fetching.
- **De-duplicate** videos across channels (same ID; near-identical title+duration).
- **Rate-limit aware:** retry/backoff on YouTube throttling.

### 5.2 Pass B — Extract (`lessons extract`)
- Read each saved transcript (never re-fetches from YouTube).
- Run the LLM (Ollama by default) to emit structured **nodes** and **edges** (schema §6).
- Attach a source reference to every node/edge: originating video + approximate timestamp.
- **Resumable:** skip transcripts already extracted at the current prompt version; re-runnable after a prompt change.
- Engine is swappable: Ollama (default, local) or a cloud LLM via `.env` key (opt-in).

### 5.3 Pass C — Serve / Explore (`lessons serve`)
- Local web app on `127.0.0.1:<port>` (explicit bind, never `0.0.0.0`).
- **Situation-first search (primary entry point).** The search box leads with the prompt *"What is your current situation or challenge you're trying to solve?"* The app answers with references and pointers — the relevant lessons/experiences, each with the source quote and a deep link to the **exact video minute(s)** where a podcaster discusses it. This is the product's front door; see §7.4 for the UX.
- **Full-text search** across all transcripts, available as a secondary/fallback mode (works even with zero extraction done).
- **Graph view:** nodes (lessons, principles, people, companies, eras, experiences, situations) with edges between them; filter by channel/episode/entity type.
- **Node detail:** click a node to see its source quote(s), the episode(s) it came from, and a deep link to the YouTube timestamp.
- Reads live graph state — usable after Pass A, richer after Pass B.

---

## 6. Data Model (SQLite — nodes & edges)

**Core tables**
- `channels(id, handle, url, name)`
- `videos(id, channel_id, title, published_at, duration_s, url, transcript_path, ingested_at)`
- `transcripts(video_id, text, has_captions, source)` — raw material
- `nodes(id, type, label, summary, origin)` — type ∈ {lesson, principle, person, company, era, **experience, situation**}; `origin` ∈ {**podcast, external**} marks whether a node came from a transcript or the enrichment layer
- `edges(id, src_node_id, dst_node_id, relation, confidence)` — relation ∈ {cites, applied, founded, aligns, **contradicts, contrasts, produced_lesson, addresses_situation**}
- `mentions(node_id, video_id, timestamp_s, quote)` — podcast provenance: links a node back to a video + minute + quote

**Enrichment layer (external biography — see §8)**
- `bios(id, person_node_id, source, source_url, license, title, summary, fetched_at)` — external biographical content attached to a `person` node, kept in its own table so it never mixes with podcast provenance

> **`experience` and `situation` are deliberate node types, not decoration.** An `experience` is the lived event a leader describes; a `situation` is the decision context it belongs to (hiring, pricing, near-death moment, succession, capital allocation). Together with the `produced_lesson` and `contradicts`/`contrasts` edges they make §7 (the learning thesis) queryable — see below.

**Extraction provenance:** `extract_runs(id, prompt_version, model, started_at)` so a re-run at a new prompt version is detectable and resumable.

> Schema is illustrative — final column set is a design-stage detail. The invariant: **every PODCAST-origin node and edge is traceable to a video + timestamp + quote.** External-origin (bio) content carries its own `source_url` + `license` attribution instead, and the UI always shows which origin a claim came from (§8).

---

## 7. Query Capabilities & Learning Thesis

### 7.1 The cadre of questions the graph can answer
**Frequency / salience** — most common ideas, principles, people, companies, eras; a given leader's recurring obsessions; the lesson-densest episodes (good entry points).
**Alignment / similarity** — which leaders share the most principles; clusters that form a school of thought; and the inverse — leaders who hold *opposing* principles on the same topic.
**Links between ideas** — lineage (who cited/was influenced by whom); idea↔idea co-occurrence, enablement, contradiction; experience→lesson (the story that produced a principle); bridge ideas linking otherwise-separate leader clusters.
**Path / exploration** — shortest path between two leaders through shared ideas; traverse out from any node; nearest-neighbor lessons.
**Provenance** — every answer drills to the exact quote + episode + YouTube timestamp; no claim floats free.

### 7.2 The Senra tension (a design constraint, not a footnote)
Senra's observation — *there aren't many common threads between great founders* — is largely correct, and the design treats it as a feature:
- **Surface principles differ and often contradict.** Optimizing for "common ideas" flattens exactly the detail that matters and tempts the extractor to invent shallow links.
- What genuinely recurs is a thin layer of meta-traits (obsession, extreme focus, learning from history, high agency) — true but near-platitudes alone.
- The real value is **divergence with provenance**: *how differently* great people solved the *same* problem, each backed by the story that made it work.

**Therefore the graph is NOT optimized to answer "what's common."** It is optimized to answer: *"given a situation, what did people who faced it actually do — and how did they disagree?"*

### 7.3 Four commitments that maximize learning
1. **Tensions are first-class.** `contradicts`/`contrasts` edges, not only `cites`/`aligns`. The flagship view is "same problem, opposing answers, each leader's reasoning."
2. **Every lesson is anchored to its experience.** `experience → produced_lesson`. A principle without its story is a fortune cookie; the quote makes it transferable. This is *why* the quote+timestamp invariant is core value, not just citation hygiene.
3. **The unit of retrieval is a situation, not a topic.** `situation` nodes (hiring, pricing, near-death, succession, capital allocation) power the killer query: *"I'm facing X — show me everyone who faced X and what they did,"* which respects divergence instead of erasing it.
4. **Durable conviction ≠ one-off remark.** Rank by cross-episode / cross-leader recurrence so repeated convictions outrank offhand lines — **frequency informs ranking, it never manufactures a common thread** (no fake averaging of distinct views).

> **Product thesis:** *learn from how great people diverged on the same hard problems.* A plain "most common ideas" list is the weakest thing this could ship; the contradiction-and-context map is the strongest — and it honors Senra's point rather than contradicting it.

### 7.4 The search page — situation-first (primary UX)
The graph-search page leads with one prompt, not a keyword field:

> **"What is your current situation or challenge you're trying to solve?"**

The user describes a real problem in plain language (*"my co-founder and I disagree on whether to raise"*, *"first senior hire isn't working out"*, *"we're nearly out of cash"*). The app then returns **references and pointers**, each result being:

- the **lesson / experience** that speaks to the situation,
- **who** said it (which leader, which podcaster/episode),
- the **source quote** (the actual words), and
- a **deep link to the exact video minute(s)** where it's discussed — the user can jump straight to that moment on YouTube.

**How the free-text situation maps to the graph (design-stage detail):**
1. Map the user's description to the nearest `situation` node(s) — via embeddings/semantic match, with keyword fallback so it works before extraction is rich.
2. Traverse `addresses_situation` → lessons/experiences, and surface `contradicts`/`contrasts` neighbors so the user sees *differing* takes on the same problem, not one flattened answer.
3. Rank by relevance + cross-source recurrence (durable conviction over offhand remark), each result carrying its provenance.

**Honest design notes:**
- Results are **pointers into the source**, never a synthesized "here's the answer." The app sends you to what a specific person actually said and the minute they said it — the user does the learning. This keeps it truthful and sidesteps the risk of the app inventing advice.
- A good result set often shows **disagreement** (two leaders, opposite calls on the same situation). That's a feature — it's the §7.2 thesis made visible at the exact moment of need.
- Secondary modes remain: plain full-text search and free graph exploration for users who'd rather browse than ask.

---

## 8. Enrichment Layer — External Biographical Content

An optional **layer** of external biographical content (Wikipedia and similar) attached to each leader already in the graph, so a result can show not just *what a podcaster said about Rockefeller* but *who Rockefeller was* — life dates, companies, era, a short bio — for context.

### 8.1 How it layers on (not into) the podcast graph
- The bio layer attaches to existing `person` nodes; it **adds context, it does not become a lesson/principle**. Bios never feed the situation-first results as if a podcaster said them.
- Stored in a separate `bios` table (§6) with `origin=external`, so podcast provenance (video + minute + quote) and external provenance (source URL + license) are **never conflated**.
- A new pass — **Pass B2 (enrich)**, run as `lessons enrich` — fetches a bio for each `person` node after extraction. Decoupled and resumable like the others; skippable entirely (the app works without it).

### 8.2 What the user sees
- On a leader's node / in a result card: a short **"About"** panel — life dates, known for, companies, era — clearly badged with its source and **"from Wikipedia"** (or whichever source), visibly distinct from the podcast quote.
- The distinction is explicit in the UI: *"Senra said …"* (podcast, with video deep link) vs. *"Wikipedia says …"* (external, with source link). The user always knows which is which.

### 8.3 Licensing & OSS constraints (must-get-right)
- **Wikipedia is CC BY-SA.** Reusable **with attribution**, but share-alike is "viral" — derivatives inherit the license. So: store and display attribution + a link to the article + its license, and keep bio text in its own layer rather than baking it into other content.
- **Prefer clearly-licensed sources.** Wikipedia/Wikidata first (open, attributable). Avoid scraping sites with restrictive terms; never ship bio *text* in the repo (same rule as transcripts — the `enrich` pass fetches it locally into each user's own DB).
- **Attribution is a schema field, not an afterthought:** `source`, `source_url`, `license` are stored per bio so the UI and any export can always credit correctly.
- Wikidata (structured, CC0) is a good companion for the *facts* (birth/death, companies founded) where CC0 removes the share-alike constraint entirely; Wikipedia prose for the narrative summary.

### 8.4 Scope
- Enrichment is **opt-in and layered** — v1 can ship without it; it slots in as Pass B2 when wanted.
- v1 target: Wikipedia/Wikidata only, one bio per `person`. Additional sources are config-driven later.

---

## 9. Out of Scope for v1 (planned for v2 — see §12)
- Cloud hosting / public URL / multi-user / auth — **deferred to v2.**
- Installable PWA / mobile app — **deferred to v2 / future.**
- Shipping transcripts or audio in the repo (legal + size) — **permanent exclusion, all versions.**
- Channels beyond the three listed (config-driven, easy to add later).
- Automatic re-ingest on a schedule (manual commands for v1).

---

## 10. OSS Hygiene (repo requirements)
- `LICENSE` (MIT), `README.md` with clone→setup→run steps.
- `.env.example`; real `.env`, `data/` gitignored.
- `pyproject.toml`; CI runs **lint + tests only** (never ingestion).
- A small transcript **fixture** so tests don't hit YouTube.

---

## 11. Build Plan — Thin Slice First

**v1.0 (thin slice):** scaffold repo → ingest a small sample per channel (handful of videos) → stand up SQLite graph + minimal explore page → run extraction over the sample → prove the full loop end-to-end.

**v1.1:** backfill the three channels' full back catalogs; harden rate-limiting, de-dup, and the extraction prompt.

---

## 12. v2 — Hosted Web App, Public URL & PWA

v2 turns the local explorer into a **shareable, installable product** without throwing away the v1 design. The key move: **the heavy work stays local; only the derived graph gets published.**

### 11.1 What gets hosted vs. what stays local
- **Stays local/offline (unchanged from v1):** Pass A (ingest) and Pass B (extract). Downloading transcripts and running the LLM happen on a maintainer's machine — never on the public server. This keeps the legal and cost story clean: **we publish a derived knowledge graph, not redistributed YouTube transcripts.**
- **Gets published:** the graph (nodes/edges/mentions) plus short quote snippets with deep links back to YouTube at the timestamp. A build step exports the local SQLite graph into a form the hosted app serves.

### 11.2 Hosted architecture
```
Local (maintainer):  ingest + extract  ->  SQLite graph  ->  export/publish step
                                                                     |
Public server:                              read-only graph API  <---+
                                                     |
Clients:                    Web app (public URL)  +  installable PWA (laptop / iPad)
```
- **Read-only public API** over the published graph (search, graph traversal, node detail). No user-generated writes in v2.
- **Web app at a public HTTPS URL** — the same explore UI as v1, served to anyone.
- **PWA** — the web app ships a manifest + service worker so users can **"install" it** to a laptop or iPad home screen, with offline caching of the graph they've browsed for quick reference.

### 11.3 Functional requirements (v2)
- Public HTTPS URL serving the explore app (TLS, a real domain). **HTTPS is non-negotiable** — iOS/iPadOS Safari only offers "Add to Home Screen" over HTTPS, so without TLS the iPad target below is impossible.
- **PWA installability:** web app manifest (name, icons, theme), service worker caching the app shell + recently viewed graph data for offline quick-reference.
- **Per-browser install behavior** (the app *runs* in all engines; only the install affordance differs):
  - **Chromium** (Chrome/Edge/Brave/Arc) — one-click "Install as app" button via `beforeinstallprompt`.
  - **Safari** — **first-class, not a fallback:** manual "Add to Dock" (macOS Sonoma+) / **"Add to Home Screen" (iPadOS/iOS)**. This is the **only** install route for iPad/iPhone, so Safari-specific meta tags (`apple-touch-icon`, `apple-mobile-web-app-*`) ship so the iOS icon/launch is clean. The named **iPad** target depends entirely on this path.
  - **Firefox desktop** — install was removed from Firefox; users run it as a pinned tab/bookmark. Shown as a short note, never a dead button.
- **Read-only, no auth for browsing** (public knowledge); any future save/collection feature would introduce accounts — explicitly deferred.
- A **publish command** (`lessons publish`) that exports the local graph and pushes it to the hosting target, so updating the public site is one maintainer command after a fresh extract.
- Same provenance guarantee as v1: every node/edge still deep-links to its source video + timestamp.

### 11.4 Hosting shape (to decide at v2 design stage)
Because this is a **read-only graph**, hosting can be cheap and static-leaning. Candidate shapes, cheapest first:
- **Static export + CDN** — pre-render the graph as static JSON/HTML, host on a CDN/static host. No server to run, pennies to host. Best if graph updates are periodic (they are — after each extract).
- **Thin API server** — a small FastAPI/edge-function service over the published graph for live search/traversal. Needed only if the graph is too large to ship fully to the client.
- The user has an AWS account on file; an AWS static-site + CDN path (S3 + CloudFront) is a natural fit and there's an existing deploy skill for it. **Decision deferred to v2 design.**

### 11.5 Future — native mobile app
- A **mobile app for quick reference** — "the best knowledge from these leaders" in your pocket — is the long-term goal, **not scoped for v2.**
- The v2 read-only graph API is the foundation: a native app (or the PWA on mobile) consumes the same API. Designing the API cleanly in v2 makes the mobile app a client, not a rewrite.

### 11.6 What v2 does NOT change
- Ingest + extract remain local, offline, resumable (v1 §5.1–5.2 unchanged).
- Transcripts/audio are still never committed or publicly redistributed.
- The graph data model (§6) is the shared contract across local app, web app, PWA, and future mobile.

---

## 13. Open Decisions (confirm before scaffolding)
1. **License** — MIT assumed; confirm or swap for Apache-2.0.
2. **Extraction default** — Ollama-local assumed; confirm (cloud opt-in stays either way).
3. **Which Ollama model** to default to (e.g. a mid-size instruct model) — can be a sensible default now, tunable later.
4. **v2 hosting shape** — static export + CDN (cheapest) vs. thin API server; and whether to target the user's existing AWS account. *Decide at v2 design stage, not needed for v1 scaffolding.*
