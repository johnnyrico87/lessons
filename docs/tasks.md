# lessons — Tasks

Ordered, checkable build plan derived from `docs/requirements.md` and `docs/design.md`.
Each task cites the requirement (R§) and/or design (D§) it satisfies. Build order is **thin-slice first**: prove the ingest → extract → search → serve loop on a handful of videos before any full backfill.

**Legend:** `[ ]` todo · `[~]` in progress · `[x]` done. Phases are gated — finish a phase's "exit check" before moving on.

---

## Phase 0 — Project scaffold & OSS hygiene  (R§6, R§7a, D§8)

- [ ] 0.1 Create repo skeleton: `pyproject.toml` (package `lessons`, Python ≥3.11), `src/lessons/`, `tests/`, `docs/` (already has SPEC/requirements/design/tasks).
- [ ] 0.2 Add `LICENSE` — **MIT** (pending confirm, R§10.1) — and `README.md` with clone → setup → run steps (R§6.3).
- [ ] 0.3 Add `.gitignore` (`data/`, `.env`, `__pycache__`, build artifacts) and `.env.example` (no real keys) (R§6.1–6.2).
- [ ] 0.4 Add `ruff` config + `pytest`; set up `src/lessons/__init__.py` and a `lessons.cli` entry point (typer) exposing `ingest|extract|enrich|serve` stubs (D§2).
- [ ] 0.5 GitHub Actions CI: lint (ruff) + tests only, never ingests (R§6.4).
- [ ] 0.6 Commit a tiny `tests/fixtures/sample_transcript.json` so tests never hit YouTube (R§6.5, D§9).

**Exit check:** `pip install -e .` works; `lessons --help` lists all commands; CI is green on an empty test.

---

## Phase 1 — Store (SQLite graph + DAL)  (R§8.2, D§3)

- [x] 1.1 Implement schema/migrations: `channels, videos, transcripts, nodes, edges, mentions, bios, extract_runs`, and `transcripts_fts` (D§3).
- [x] 1.2 Build the read/write DAL with the **provenance invariant enforced**: a `nodes` row with `origin='podcast'` must have ≥1 `mentions`; a `person` with a bio must carry a non-null `license` (D§3, R§8.2).
- [x] 1.3 Implement the **structured citation assembler** in the DAL — `PodcastCitation` (channel, episode, video_url, timestamp, deep_link, host, leaders[]) and `ExternalCitation` (source, url, license); `host` and `leaders` are separate fields (R§4a, D§7a).
- [x] 1.4 DAL refuses to emit a podcast unit without a resolvable citation (R§4a.5).
- [x] 1.5 Unit tests against the fixture: schema invariants, citation assembly, uncitable-node rejection (D§9).

**Exit check:** store tests pass with no network; inserting a citationless podcast node is rejected.

---

## Phase 2 — Ingest (Pass A)  (R§1, D§6)

- [ ] 2.1 Channel enumeration via `yt-dlp` `/videos` playlist → `channels` + `videos` rows (id, title, date, duration, url) (R§1.1, D§6).
- [ ] 2.2 Transcript fetch via `youtube-transcript-api` with per-line timestamps → `transcripts` (`source='captions'`) + file under `data/transcripts/` (R§1.2, R§1.4).
- [ ] 2.3 Whisper fallback: no captions → `yt-dlp` audio → `faster-whisper` → `transcripts` (`source='whisper'`) (R§1.3).
- [ ] 2.4 Resumability: skip already-ingested video ids via `videos.ingested_at` (R§1.5, R§7.2).
- [ ] 2.5 De-dup across channels by video id, then normalized title + duration bucket — handles the likely Senra/Founders overlap (R§1.6).
- [ ] 2.6 Rate-limit handling: exponential backoff + jitter, configurable concurrency cap (R§1.7).
- [ ] 2.7 `lessons ingest --probe N` dry-run: sample N/channel, report caption-coverage % + median per-video latency (R§1.8, D§6). **This produces the real backfill time estimate.**

**Exit check:** `lessons ingest --probe 5` runs on the three channels, writes rows + files, prints the coverage/latency report. **Thin-slice corpus (~5/channel) now exists.**

---

## Phase 3 — Extract (Pass B)  (R§2, D§5)

- [ ] 3.1 Ollama client (default) + optional cloud client behind `.env` key (R§2.2).
- [ ] 3.2 Transcript chunking by time/token window with overlap; each chunk keeps its start timestamp so mentions stay accurate (D§5).
- [ ] 3.3 Extraction prompt (`prompts/extract_v1.txt`) emitting JSON for node types `lesson|principle|person|company|era|experience|situation` and edges `cites|applied|founded|aligns|contradicts|contrasts|produced_lesson|addresses_situation` (R§2.3).
- [ ] 3.4 Pydantic validation of LLM JSON; invalid output retried then skipped-with-reason, never corrupting the graph (D§5).
- [ ] 3.5 Attach provenance (`mentions`: video + timestamp + quote) to every extracted node/edge (R§2.4).
- [ ] 3.6 Entity resolution: de-dup nodes by (type, normalized label) so "Rockefeller" / "John D. Rockefeller" collapse (D§5).
- [ ] 3.7 **Tension detection** — prompt explicitly asks for opposing stances on a shared situation → `contradicts`/`contrasts` edges as a first-class target (R§2.7, D§5).
- [ ] 3.8 Prompt versioning: record `prompt_version` + `model` in `extract_runs`; re-run skips same-version, re-extracts on bump (R§2.5–2.6).
- [ ] 3.9 Guard: never merge distinct views into an average "common thread"; frequency informs ranking only (R§2.8).
- [ ] 3.10 Tests: mock the LLM, assert parse → validate → store path on the fixture (D§9).

**Exit check:** `lessons extract` over the thin-slice transcripts fills `nodes/edges/mentions`; at least one `contradicts`/`contrasts` edge appears; re-run at same prompt version is a no-op.

---

## Phase 4 — Search (situation-first)  (R§4, D§4)

- [ ] 4.1 Situation→node mapping: embed query (local sentence-transformer, R§10 embedding decision) → nearest `situation` node(s), **FTS keyword fallback** so it works before extraction is deep (R§4.2, D§4).
- [ ] 4.2 Traverse `situation --addresses_situation--> lessons/experiences`; expand `contradicts/contrasts` neighbors to surface divergence (R§4.4, D§4).
- [ ] 4.3 Rank by relevance × cross-source recurrence (durable conviction > offhand remark) (R§4.6).
- [ ] 4.4 Result shape = pointers only: lesson/experience + leader + episode + quote + timestamp deep link; **no synthesized answer** (R§4.3, R§4.5).
- [ ] 4.5 Tests: ranking + divergence surfacing on the fixture graph (D§9).

**Exit check:** a free-text situation query returns cited pointer results including at least one divergent pair, with zero synthesized advice text.

---

## Phase 5 — Serve: API + splash + results UI  (R§4a–4c, R§4b, R§5, D§7)

- [ ] 5.1 FastAPI read-only API: `/search`, `/fts`, `/graph`, `/node/{id}`, `/bio/{person}`, `/splash`; bind `127.0.0.1` explicitly, never `0.0.0.0` (R§5.1, D§7).
- [ ] 5.2 **Splash page** — search box hero with the lead prompt; ambient floating layer of leader name chips / avatars / pull-quotes from `/splash` (real nodes, seeded set pre-extraction); never obstructs the box; `prefers-reduced-motion` → static (R§4b.1–5, D§7b).
- [ ] 5.3 Avatars: public-domain/CC images (Wikidata) else initials fallback; attributed, not re-hosted (R§4b.6).
- [ ] 5.4 **Results page quality bar:** card padding + uniform spacing; hairline dividers (no heavy borders); quote as pull-quote centerpiece; citation as quiet secondary line; styled clickable URLs with host label; theme tokens only (dark-mode safe); divergent takes visually paired; responsive laptop↔tablet (R§4c.1–7, D§7c).
- [ ] 5.5 **Citations everywhere:** every card/panel shows channel + episode + leader(s) + timestamp deep link; podcast vs external styled distinctly; external shows source+url+license; all URLs real links; host (artist) separate from leader (subject) (R§4a.1–6, D§7a).
- [ ] 5.6 Graph explore view (Cytoscape.js): nodes/edges filterable by channel/episode/type; node select shows quotes + episodes + deep links; bio "About" panel badged external (R§5.3–5.5).
- [ ] 5.7 FTS transcript search usable with zero extraction done (R§5.2).

**Exit check:** `lessons serve` opens the splash; a situation search renders polished cited cards; the graph view loads; everything on `127.0.0.1`.

---

## Phase 6 — PWA + distribution  (R§7a, R§4b.7–10, D§9a)

- [ ] 6.1 `manifest.webmanifest` (name, icons, `display: standalone`, theme/background color) + service worker caching app shell + viewed content (R§7a.1, R§7a.5, D§9a).
- [ ] 6.2 Splash **download buttons**: "Install as app" + "View on GitHub" in a quiet corner, not competing with the search hero (R§4b.7, D§7b).
- [ ] 6.3 **Per-engine install affordance:** Chromium `beforeinstallprompt` → one-click; Safari → "Add to Dock / Add to Home Screen" instructions; Firefox desktop → "pin as a tab" note; never a dead button (R§4b.8, R§4b.10, D§9a).
- [ ] 6.4 Safari first-class: ship `apple-touch-icon` + `apple-mobile-web-app-*` meta tags for clean iOS/iPad home-screen icon (R§4b.9).
- [ ] 6.5 No native OS installers; no binaries committed; source is the distributed form (R§7a.4, R§7a.6).

**Exit check:** app installs one-click in Chrome; Safari shows correct Add-to-Dock copy; Firefox shows the pin-tab note; manifest/SW pass a Lighthouse PWA audit on the local origin.

---

## Phase 7 — Enrich (Pass B2, optional)  (R§3, D§2)

- [ ] 7.1 `lessons enrich`: fetch bio per `person` node from Wikipedia/Wikidata → `bios` with `origin=external` (R§3.1).
- [ ] 7.2 Store `source`, `source_url`, `license` for every bio; prefer **Wikidata CC0 facts** + optional **Wikipedia CC BY-SA prose** (R§3.2, R§10.4).
- [ ] 7.3 Keep bios out of situation-first results; never present as podcast speech (R§3.3).
- [ ] 7.4 Never commit bio text; fetch into local DB; app fully functional with enrich skipped (R§3.4–3.5).

**Exit check:** `lessons enrich` adds cited bios; skipping it leaves the app fully working.

---

## Phase 8 — Full backfill & release

- [ ] 8.1 Use the Phase 2.7 probe numbers to schedule the full ingest across all three channels (resumable) (R§1).
- [ ] 8.2 Full extract pass over the complete transcript set (R§2).
- [ ] 8.3 README polish: real screenshots, the per-browser install matrix, "bring your own Ollama" note, the model-provisioning first-run note.
- [ ] 8.4 Tag first open-source release on GitHub (source only).

**Exit check:** a stranger can clone, run `lessons ingest/extract/serve`, and install the PWA — no committed data, no paid key required.

---

## Decisions to confirm before/at build (from requirements §10)
- [ ] License — **MIT** vs Apache-2.0 (blocks 0.2).
- [ ] Extraction default — **Ollama-local** confirmed (blocks 3.1).
- [ ] Default Ollama model tier (blocks 3.1).
- [ ] Embedding model for situation matching (blocks 4.1).
- [ ] Bio source balance — Wikidata CC0 vs Wikipedia CC BY-SA (blocks 7.2).

## v2 (not in this plan — see SPEC §12 / design §10)
Public HTTPS origin + `lessons publish` export + CDN hosting; iPad install via Safari over HTTPS; future native mobile on the same read-only API.
