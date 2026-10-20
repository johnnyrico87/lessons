"""SQLite schema for the lessons knowledge graph (design §3).

Invariant enforced by the DAL (not SQL alone):
  * a `nodes` row with origin='podcast' must have >=1 `mentions` row;
  * a `person` node with a bio must have a `bios` row carrying a non-null license.

The schema is applied idempotently via `apply_schema`; bump SCHEMA_VERSION and add
migration SQL when the shape changes.
"""

from __future__ import annotations

SCHEMA_VERSION = 3

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
    started_at     TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    key   TEXT PRIMARY KEY,
    value TEXT
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
