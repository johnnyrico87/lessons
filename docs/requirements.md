# lessons — Requirements

Derived from `docs/SPEC.md`. User stories with acceptance criteria (EARS-style: WHEN/IF … THE SYSTEM SHALL …). "Leaders" = the people of history & business discussed in the podcasts.

**Sources (v1):** YouTube channels — David Senra (`@DavidSenra`), Founders Podcast (`@founderspodcast1`), TakeOver Pod (`@TakeOverPod`).

---

## 1. Ingest transcripts (Pass A)

**Story:** As a maintainer, I want to pull every video's transcript + metadata from the configured channels, so the corpus exists locally as durable raw material.

**Acceptance criteria**
1. WHEN `lessons ingest` is run, THE SYSTEM SHALL enumerate all videos for each configured channel and record id, title, channel, publish date, duration, and URL.
2. WHEN a video has captions, THE SYSTEM SHALL fetch the caption text (no LLM) and store it with per-line timestamps.
3. IF a video has no captions, THE SYSTEM SHALL download its audio and transcribe it locally with faster-whisper, marking the transcript `source=whisper`.
4. THE SYSTEM SHALL persist each transcript to disk under `data/transcripts/` and a row in SQLite.
5. WHEN ingest is re-run, THE SYSTEM SHALL skip videos already ingested (resumable) without re-fetching.
6. WHEN the same video appears on more than one channel (same id, or near-identical title+duration), THE SYSTEM SHALL de-duplicate it to a single record.
7. IF YouTube rate-limits a request, THE SYSTEM SHALL back off and retry rather than fail the whole run.
8. THE SYSTEM SHALL support a dry-run probe over a small sample that reports caption-coverage % and per-video latency before a full backfill.

---

## 2. Extract learnings (Pass B)

**Story:** As a maintainer, I want an LLM to turn transcripts into a structured graph of lessons, principles, people, companies, eras, experiences, and situations, so the corpus becomes explorable knowledge.

**Acceptance criteria**
1. WHEN `lessons extract` is run, THE SYSTEM SHALL read saved transcripts only and never re-fetch from YouTube.
2. THE SYSTEM SHALL use a local LLM (Ollama) by default, and MAY use a cloud LLM when a key is configured in `.env` (opt-in).
3. THE SYSTEM SHALL emit `nodes` (type ∈ lesson, principle, person, company, era, experience, situation) and `edges` (relation ∈ cites, applied, founded, aligns, contradicts, contrasts, produced_lesson, addresses_situation).
4. THE SYSTEM SHALL attach provenance to every extracted node/edge: originating video + approximate timestamp + source quote (`mentions`).
5. THE SYSTEM SHALL record each run's `prompt_version` and `model` (`extract_runs`).
6. WHEN extract is re-run at the same prompt version, THE SYSTEM SHALL skip already-extracted transcripts; WHEN the prompt version changes, THE SYSTEM SHALL re-extract.
7. THE SYSTEM SHALL model tensions (`contradicts`/`contrasts`) as first-class, not only agreements.
8. THE SYSTEM SHALL NOT manufacture a "common thread"; frequency MAY inform ranking but SHALL NOT merge distinct views into an average.

---

## 3. Enrich with external biographies (Pass B2, optional)

**Story:** As a user, I want a short factual bio for each leader, so I have context on who they were alongside what the podcasters said.

**Acceptance criteria**
1. WHEN `lessons enrich` is run, THE SYSTEM SHALL fetch a bio for each `person` node from Wikipedia/Wikidata and store it in the `bios` table with `origin=external`.
2. THE SYSTEM SHALL store `source`, `source_url`, and `license` for every bio.
3. THE SYSTEM SHALL keep external bios separate from podcast-sourced nodes and SHALL NOT feed bio content into situation-first results as if a podcaster said it.
4. THE SYSTEM SHALL NOT commit bio text to the repo; `enrich` fetches into each user's local DB.
5. THE SYSTEM SHALL function fully with enrichment skipped.

---

## 4. Situation-first search (primary UX, Pass C)

**Story:** As a user facing a real problem, I want to describe my situation and get pointers to what these leaders actually did about it, with the exact source, so I can learn at the moment of need.

**Acceptance criteria**
1. THE SYSTEM SHALL present the search box with the lead prompt: *"What is your current situation or challenge you're trying to solve?"*
2. WHEN a user submits a free-text situation, THE SYSTEM SHALL map it to the nearest `situation` node(s) via semantic match with keyword fallback.
3. THE SYSTEM SHALL return results as pointers, each carrying: the lesson/experience, who said it (leader + episode), the source quote, and a deep link to the exact video minute(s).
4. WHEN multiple leaders addressed the same situation differently, THE SYSTEM SHALL surface the divergence (`contradicts`/`contrasts`) rather than a single flattened answer.
5. THE SYSTEM SHALL NOT present a synthesized "here's the answer"; results point into the source and the user does the learning.
6. THE SYSTEM SHALL rank by relevance + cross-source recurrence (durable conviction over offhand remark).

---

## 4a. Mandatory citation & attribution (cross-cutting, Pass C)

**Story:** As a user, I want every piece of knowledge in the app to name exactly where it came from — the channel, the episode, the leader it's about, and the external source for any bio — so I can trust it and go to the source.

**Acceptance criteria**
1. THE SYSTEM SHALL render a citation on every result card and node panel, naming: the **channel** (with its handle/URL), the **episode** (title + publish date + video URL), the **leader(s)** the content is about, and the **exact timestamp** deep link.
2. THE SYSTEM SHALL format a podcast-origin citation distinctly from an external (bio) citation, so a user never mistakes "what a podcaster said" for "what Wikipedia says."
3. WHEN content derives from an external bio, THE SYSTEM SHALL show the external **source name, source URL, and license** (e.g. "Wikipedia · CC BY-SA").
4. THE SYSTEM SHALL make every citation URL a real, clickable link (channel URL, episode URL, timestamped video URL, external source URL) — never bare un-linked text.
5. THE SYSTEM SHALL NOT display any lesson, quote, or node without a resolvable citation; a node that cannot be cited SHALL NOT be shown.
6. THE SYSTEM SHALL attribute the **artist/host of the channel** (e.g. David Senra) separately from the **leader discussed** (e.g. Rockefeller) — these are distinct citation fields, not conflated.

---

## 4b. Splash / landing page (Pass C)

**Story:** As a first-time visitor, I want an inviting landing page that makes the search box the hero and shows me the leaders this knowledge comes from, so the tool feels alive and I know whose wisdom I'm about to search.

**Acceptance criteria**
1. THE SYSTEM SHALL present a splash page whose focal point is the situation-first search box with the lead prompt *"What is your current situation or challenge you're trying to solve?"*
2. THE SYSTEM SHALL surround the search box with visual elements representing the leaders in the corpus — **names, profile pictures, and/or short pull-quotes** — arranged as ambient/floating elements around the box.
3. THE SYSTEM SHALL source splash leader elements from real graph data (person nodes + their quotes), not placeholder text, once extraction has run; before extraction it MAY show the channel hosts/known leaders as a seeded set.
4. THE SYSTEM SHALL ensure the floating elements never obstruct or compete with the search box for the user's primary action (search stays unmistakably the hero).
5. THE SYSTEM SHALL respect the reduced-motion preference: when set, floating elements SHALL be static rather than animated.
6. THE SYSTEM SHALL keep profile-picture usage within licensing limits (link/attribute external images; prefer public-domain/CC or avatar fallback initials when a licensed image is unavailable).
7. THE SYSTEM SHALL present **download/get-the-app buttons** on the splash page offering: **"Install as app" (PWA)** on Chromium-based browsers, and **"View on GitHub" (open source)** linking to the repo. THE SYSTEM SHALL NOT offer macOS- or Windows-specific native installers.
8. THE SYSTEM SHALL adapt the install affordance to the browser: **Chromium** — show the one-click "Install as app" button wired to `beforeinstallprompt`; **Safari (macOS/iPadOS/iOS)** — show concise **"Add to Dock / Add to Home Screen"** instructions (no JS install event exists); **Firefox desktop** — show a brief "install not available; pin as a tab" note. THE SYSTEM SHALL never present a dead/broken install button.
9. THE SYSTEM SHALL treat the **Safari install path as first-class**, not a fallback, because iPad/iPhone installs run through Safari's "Add to Home Screen" — this is the only install route for the iPad target named in the v2 roadmap. THE SYSTEM SHALL ship the `apple-touch-icon` and `apple-mobile-web-app-*` meta tags needed for a polished Safari/iOS home-screen icon.
10. THE SYSTEM SHALL keep the web app itself (render + service-worker offline cache) functional in all three engines; only the *install affordance* differs by browser.

---

## 4c. Results-page UI/UX quality bar (Pass C)

**Story:** As a user, I want the search results page to look and feel polished and trustworthy, so the content is easy to scan and the citations read as credible.

**Acceptance criteria**
1. THE SYSTEM SHALL render each result as a **card** with consistent internal padding, a clear visual hierarchy (leader → lesson/quote → citation), and generous, consistent **spacing** between cards.
2. THE SYSTEM SHALL use restrained, consistent **line/border thickness** and dividers (hairline rules, not heavy borders) and a single coherent type scale.
3. THE SYSTEM SHALL display **URLs and citations** as styled, clearly-clickable links with a visible host/source label — never raw, unstyled, or truncated-illegibly.
4. THE SYSTEM SHALL maintain readable contrast and a theme-consistent palette (no hard-coded colors that break in dark mode); every surface sets both background and text color.
5. WHEN results contain divergent takes, THE SYSTEM SHALL visually group or pair the contrasting cards so the disagreement is legible at a glance.
6. THE SYSTEM SHALL keep the quote as the typographic centerpiece of each card (e.g. a pull-quote treatment), with the citation as a quieter secondary line.
7. THE SYSTEM SHALL be responsive and legible on laptop and tablet widths (forward-compatible with the v2 PWA on laptop/iPad).

---

## 5. Explore the graph (Pass C, secondary)

**Story:** As a user, I want to browse the graph and search transcripts directly, so I can explore even before extraction is complete.

**Acceptance criteria**
1. THE SYSTEM SHALL serve a local web app bound to `127.0.0.1` with an explicit bind address (never `0.0.0.0`).
2. THE SYSTEM SHALL provide full-text transcript search that works with zero extraction done.
3. THE SYSTEM SHALL render a graph view with nodes and edges, filterable by channel/episode/entity type.
4. WHEN a node is selected, THE SYSTEM SHALL show its source quote(s), episode(s), and YouTube timestamp deep link(s).
5. WHEN a node has a bio, THE SYSTEM SHALL show an "About" panel badged with its external source, visibly distinct from podcast quotes.
6. THE SYSTEM SHALL read live graph state so the app is usable after Pass A and richer after Pass B/B2.

---

## 6. Open-source hygiene

**Story:** As a stranger cloning the repo, I want it to run without secrets or scraped data committed, so I can reproduce the local DB myself.

**Acceptance criteria**
1. THE SYSTEM SHALL keep API keys in `.env` (gitignored) with a committed `.env.example`.
2. THE SYSTEM SHALL gitignore `data/` (transcripts, audio, DB, bios) and SHALL NOT commit transcripts, audio, or bio text.
3. THE SYSTEM SHALL ship a `LICENSE` (MIT, pending confirmation) and a `README` with clone → setup → run steps.
4. THE SYSTEM SHALL provide CI that runs lint + tests only and never performs ingestion.
5. THE SYSTEM SHALL ship a small transcript fixture so tests do not hit YouTube.
6. THE SYSTEM SHALL run end-to-end with no paid key (Ollama local default).

---

## 7. Decoupled, resumable pipeline

**Story:** As a maintainer, I want ingest, extract, enrich, and serve to be independent, so a failure in one never wastes another's work.

**Acceptance criteria**
1. THE SYSTEM SHALL implement ingest, extract, enrich, and serve as separate commands.
2. THE SYSTEM SHALL make ingest, extract, and enrich independently resumable.
3. WHEN the extraction prompt is improved, THE SYSTEM SHALL allow re-running extract over existing transcripts with no re-download.
4. THE SYSTEM SHALL treat transcripts as the durable raw material from which all downstream data is recomputable.

---

## 7a. Distribution: PWA + open source (no OS-specific installers)

**Story:** As an end user, I want to either install the app straight from my Chromium browser or get the source from GitHub, so I never deal with OS-specific installers — and as a developer I can clone and run it.

**Decision (supersedes earlier native-installer plan):** the app is distributed as (a) an **installable PWA** for Chromium-based browsers and (b) **open source on GitHub**. No `.dmg`/`.exe`/AppImage native installers are built. "Cross-platform" is achieved via the browser, which already runs on macOS, Windows, and Linux.

**Acceptance criteria**
1. THE SYSTEM SHALL ship a valid **PWA** (web app manifest + service worker) that Chromium-based browsers (Chrome, Edge, Brave, Arc, …) can install to the desktop/home screen on macOS, Windows, and Linux.
2. THE SYSTEM SHALL offer the **"Install as app"** action from the splash page (see §4b.7–8), and SHALL degrade gracefully where install is unsupported.
3. THE SYSTEM SHALL offer a **"View on GitHub"** path so developers can clone and run from source (`pip install -e .` + run).
4. THE SYSTEM SHALL NOT build or ship macOS- or Windows-specific native installers; cross-platform support comes from the browser runtime.
5. WHEN installed as a PWA, THE SYSTEM SHALL work against its backing server (local for v1 self-host; the public read-only API in v2) and cache viewed content for offline reference per the service worker.
6. THE SYSTEM SHALL keep binary/build artifacts out of the git repo; the PWA is served, not committed, and source is the distributed form on GitHub.

> Note: this trades "zero-toolchain native app for non-developers" for "install from the browser." For v1 (local-first, developer/maintainer audience) that's the right call; the frictionless public PWA is fully realized in v2 when a public URL exists to install from.

---

## 8. Non-functional requirements
1. **Local-first:** no cloud dependency required for v1; everything runs on `127.0.0.1`.
2. **Provenance integrity:** every podcast-origin claim traces to video + minute + quote; every external claim carries source + license.
3. **Truthfulness:** the app points to what was actually said; it does not invent advice.
4. **Portability:** zero-install graph store (SQLite); clone-and-go.

---

## 9. Out of scope (v1) / deferred to v2
- Public hosting / public URL for the PWA to install from (v1 PWA installs from a local/self-hosted origin; the public origin is v2).
- Native mobile app (future).
- Redistributing transcripts, audio, or bio text (permanent exclusion).
- Channels beyond the three configured.
- Scheduled auto-ingest.

---

## 10. Open decisions (confirm before build)
1. License — MIT assumed (vs. Apache-2.0).
2. Extraction default — Ollama-local assumed (cloud opt-in either way).
3. Default Ollama model.
4. Bio facts source balance — Wikidata CC0 facts vs. Wikipedia CC BY-SA prose.
