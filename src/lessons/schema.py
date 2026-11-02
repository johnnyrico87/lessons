"""SQLite schema for the lessons knowledge graph (design §3).

Invariant enforced by the DAL (not SQL alone):
  * a `nodes` row with origin='podcast' must have >=1 `mentions` row;
  * a `person` node with a bio must have a `bios` row carrying a non-null license.

The schema is applied idempotently via `apply_schema`; bump SCHEMA_VERSION and add
migration SQL when the shape changes.
"""

from __future__ import annotations

SCHEMA_VERSION = 5

SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS channels (
    id      TEXT PRIMARY KEY,   -- stable channel id
    handle  TEXT,               -- e.g. @DavidSenra
    url     TEXT,
    name    TEXT                -- the host / artist, e.g. "David Senra"
);

CREATE TABLE IF NOT EXISTS videos (
    id              TEXT PRIMARY KEY,   -- YouTube video id
    channel_id      TEXT REFERENCES channels(id),
    title           TEXT,
    published_at    TEXT,
    duration_s      INTEGER,
    url             TEXT,
    transcript_path TEXT,
    ingested_at     TEXT
);

CREATE TABLE IF NOT EXISTS transcripts (
    video_id      TEXT PRIMARY KEY REFERENCES videos(id),
    text          TEXT,
    has_captions  INTEGER,   -- 1 = YouTube captions, 0 = whisper
    source        TEXT       -- 'captions' | 'whisper'
);

CREATE TABLE IF NOT EXISTS transcript_lines (   -- per-line timestamps for chunking + mentions
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    video_id    TEXT NOT NULL REFERENCES videos(id),
    start_s     INTEGER NOT NULL,
    text        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS nodes (
    id      TEXT PRIMARY KEY,
    type    TEXT NOT NULL,            -- lesson|principle|person|company|era|experience|situation
    label   TEXT NOT NULL,
    summary TEXT,
    origin  TEXT NOT NULL DEFAULT 'podcast'   -- 'podcast' | 'external'
);

CREATE TABLE IF NOT EXISTS edges (
    id          TEXT PRIMARY KEY,
    src_node_id TEXT NOT NULL REFERENCES nodes(id),
    dst_node_id TEXT NOT NULL REFERENCES nodes(id),
    -- relation: cites|applied|founded|aligns|contradicts|contrasts|
    --           produced_lesson|addresses_situation
    relation    TEXT NOT NULL,
    confidence  REAL
);

CREATE TABLE IF NOT EXISTS mentions (          -- podcast provenance
    id          TEXT PRIMARY KEY,
    node_id     TEXT NOT NULL REFERENCES nodes(id),
    video_id    TEXT NOT NULL REFERENCES videos(id),
    timestamp_s INTEGER NOT NULL,
    quote       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bios (              -- external enrichment layer
    id              TEXT PRIMARY KEY,
    person_node_id  TEXT NOT NULL REFERENCES nodes(id),
    source          TEXT NOT NULL,
    source_url      TEXT NOT NULL,
    license         TEXT NOT NULL,             -- non-null by invariant
    title           TEXT,
    summary         TEXT,
    image_url       TEXT,                       -- avatar (Wikidata image claim)
    image_license   TEXT,                       -- images carry their OWN license
    fetched_at      TEXT
);

CREATE TABLE IF NOT EXISTS extract_runs (
    id             TEXT PRIMARY KEY,
    prompt_version TEXT,
    model          TEXT,
    started_at     TEXT,
    -- Provenance fingerprint (schema v4). A video is re-extracted when ANY of
    -- these changes, not just prompt_version: the model's content digest, the
    -- sampling params, the chunk window/overlap, and a self-hash of the
    -- extractor code (prompt text + vocabularies + chunk defaults). fingerprint
    -- is the stable hash OF the tuple below and is what the skip-gate compares.
    model_digest   TEXT,   -- ollama content digest of the model tag (or '' if unknown)
    sampling       TEXT,   -- JSON of sampling params, e.g. {"temperature": 0}
    chunk_window_s  INTEGER,
    chunk_overlap_s INTEGER,
    code_sha       TEXT,   -- self-hash of the extractor's prompt + constants
    fingerprint    TEXT,   -- sha256 over the whole provenance tuple (see RunFingerprint)
    finished_at    TEXT,   -- set when the run completes (NULL = in-flight/aborted)
    videos_processed INTEGER,
    runtime_s      REAL    -- wall-clock of the whole run (latency telemetry)
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS node_decisions (   -- human entity-resolution decisions (schema v5)
    -- Keyed by the entity SHAPE (type + normalized label), NOT a node id, so a
    -- decision survives re-extraction: a re-created 'Gustav Leban' is matched by
    -- the same key and the recorded fix re-applies. The release gate applies
    -- these deterministically before publishing.
    id            TEXT PRIMARY KEY,
    node_type     TEXT NOT NULL,            -- the suspect node's type
    norm_label    TEXT NOT NULL,            -- lower(trim(label)) it applies to
    action        TEXT NOT NULL,            -- 'rename' | 'drop' | 'merge' | 'keep'
    target_label  TEXT,                     -- rename: new label; merge: label to fold into
    target_type   TEXT,                     -- merge: target node type (default = node_type)
    reason        TEXT,
    decided_at    TEXT,
    UNIQUE(node_type, norm_label)
);

CREATE INDEX IF NOT EXISTS idx_mentions_node  ON mentions(node_id);
CREATE INDEX IF NOT EXISTS idx_mentions_video ON mentions(video_id);
CREATE INDEX IF NOT EXISTS idx_edges_src      ON edges(src_node_id);
CREATE INDEX IF NOT EXISTS idx_edges_dst      ON edges(dst_node_id);
CREATE INDEX IF NOT EXISTS idx_nodes_type     ON nodes(type);
CREATE INDEX IF NOT EXISTS idx_bios_person    ON bios(person_node_id);
CREATE INDEX IF NOT EXISTS idx_tlines_video   ON transcript_lines(video_id, start_s);

-- Full-text search over transcripts (usable before extraction, R§5.2).
CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(video_id, text);
"""


NODE_TYPES = {
    "lesson", "principle", "person", "company", "era", "experience", "situation",
}

EDGE_RELATIONS = {
    "cites", "applied", "founded", "aligns",
    "contradicts", "contrasts", "produced_lesson", "addresses_situation",
}
