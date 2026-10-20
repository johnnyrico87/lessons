"""The data-access layer over the SQLite graph (design §3, §7a).

Enforces the provenance invariant in Python (SQLite alone can't express it):

  * add_node(origin='podcast') must be accompanied by >=1 mention — callers use
    add_podcast_node(), which requires at least one mention atomically;
  * add_bio() requires a non-null license;
  * cite_node() / emitting a podcast unit without a resolvable citation raises
    UncitableNodeError (§4a.5).
"""

from __future__ import annotations

import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from lessons.citations import (
    ExternalCitation,
    PodcastCitation,
    UncitableNodeError,
)
from lessons.schema import (
    EDGE_RELATIONS,
    NODE_TYPES,
    SCHEMA_SQL,
    SCHEMA_VERSION,
)


@dataclass
class Mention:
    video_id: str
    timestamp_s: int
    quote: str


def _id() -> str:
    return uuid.uuid4().hex


class Store:
    """Thin DAL. Open with Store(path) or Store.memory() for tests."""

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys = ON;")
        self._apply_schema()

    @classmethod
    def memory(cls) -> Store:
        return cls(":memory:")

    def close(self) -> None:
        self.conn.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- schema -----------------------------------------------------------
    def _apply_schema(self) -> None:
        self.conn.executescript(SCHEMA_SQL)
        self.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES ('schema_version', ?)",
            (str(SCHEMA_VERSION),),
        )
        self.conn.commit()

    # --- channels / videos / transcripts ----------------------------------
    def add_channel(self, id: str, handle: str, url: str, name: str) -> str:
        self.conn.execute(
            "INSERT OR REPLACE INTO channels(id, handle, url, name) VALUES (?,?,?,?)",
            (id, handle, url, name),
        )
        self.conn.commit()
        return id

    def add_video(
        self,
        id: str,
        channel_id: str,
        title: str,
        published_at: str,
        duration_s: int,
        url: str,
        transcript_path: str | None = None,
        ingested_at: str | None = None,
    ) -> str:
        self.conn.execute(
            """INSERT OR REPLACE INTO videos
               (id, channel_id, title, published_at, duration_s, url, transcript_path, ingested_at)
               VALUES (?,?,?,?,?,?,?,?)""",
            (id, channel_id, title, published_at, duration_s, url, transcript_path, ingested_at),
        )
        self.conn.commit()
        return id

    def add_transcript(
        self,
        video_id: str,
        text: str,
        source: str,
        lines: list[dict] | None = None,
    ) -> None:
        if source not in ("captions", "whisper"):
            raise ValueError(f"transcript source must be captions|whisper, got {source!r}")
        has_captions = 1 if source == "captions" else 0
        self.conn.execute(
            "INSERT OR REPLACE INTO transcripts(video_id, text, has_captions, source) "
            "VALUES (?,?,?,?)",
            (video_id, text, has_captions, source),
        )
        self.conn.execute(
            "INSERT INTO transcripts_fts(video_id, text) VALUES (?,?)",
            (video_id, text),
        )
        if lines:
            # Replace any existing lines for this video (idempotent re-ingest).
            self.conn.execute("DELETE FROM transcript_lines WHERE video_id=?", (video_id,))
            self.conn.executemany(
                "INSERT INTO transcript_lines(video_id, start_s, text) VALUES (?,?,?)",
                [(video_id, int(line["start_s"]), line["text"]) for line in lines],
            )
        self.conn.commit()

    def lines_for(self, video_id: str) -> list[dict]:
        """Return stored per-line timestamps for a video, ordered by start.
        Empty list when none were persisted (e.g. a text-only transcript)."""
        rows = self.conn.execute(
            "SELECT start_s, text FROM transcript_lines WHERE video_id=? ORDER BY start_s",
            (video_id,),
        ).fetchall()
        return [{"start_s": r["start_s"], "text": r["text"]} for r in rows]

    # --- nodes ------------------------------------------------------------
    def add_podcast_node(
        self,
        type: str,
        label: str,
        mentions: Sequence[Mention],
        summary: str | None = None,
    ) -> str:
        """Add a podcast-origin node WITH its provenance, atomically.

        Enforces the invariant: a podcast node is never created without >=1 mention.
        """
        self._check_node_type(type)
        if not mentions:
            raise UncitableNodeError(
                f"podcast node {label!r} ({type}) requires at least one mention"
            )
        node_id = _id()
        try:
            self.conn.execute(
                "INSERT INTO nodes(id, type, label, summary, origin) VALUES (?,?,?,?, 'podcast')",
                (node_id, type, label, summary),
            )
            for m in mentions:
                if not m.quote or m.timestamp_s is None:
                    raise UncitableNodeError("each mention needs a quote and a timestamp")
                self.conn.execute(
                    "INSERT INTO mentions(id, node_id, video_id, timestamp_s, quote) "
                    "VALUES (?,?,?,?,?)",
                    (_id(), node_id, m.video_id, int(m.timestamp_s), m.quote),
                )
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise
        return node_id

    def add_external_node(self, type: str, label: str, summary: str | None = None) -> str:
        """Add an external-origin node (e.g. a person we only know from a bio)."""
        self._check_node_type(type)
        node_id = _id()
        self.conn.execute(
            "INSERT INTO nodes(id, type, label, summary, origin) VALUES (?,?,?,?, 'external')",
            (node_id, type, label, summary),
        )
        self.conn.commit()
        return node_id

    def upsert_node_by_label(
        self,
        type: str,
        label: str,
        mentions: Sequence[Mention] | None = None,
        summary: str | None = None,
    ) -> str:
        """Entity resolution hook: collapse by (type, normalized label).

        If a node of this (type, normalized label) exists, reuse it and append any
        new mentions; otherwise create it. Podcast provenance still required when
        mentions are supplied.
        """
        self._check_node_type(type)
        norm = label.strip().lower()
        row = self.conn.execute(
            "SELECT id, origin FROM nodes WHERE type=? AND lower(trim(label))=?",
            (type, norm),
        ).fetchone()
        if row:
            node_id = row["id"]
            for m in mentions or []:
                self.conn.execute(
                    "INSERT INTO mentions(id, node_id, video_id, timestamp_s, quote) "
                    "VALUES (?,?,?,?,?)",
                    (_id(), node_id, m.video_id, int(m.timestamp_s), m.quote),
                )
            self.conn.commit()
            return node_id
        if mentions:
            return self.add_podcast_node(type, label, mentions, summary)
        return self.add_external_node(type, label, summary)

    # --- edges ------------------------------------------------------------
    def add_edge(
        self, src_node_id: str, dst_node_id: str, relation: str, confidence: float | None = None
    ) -> str:
        if relation not in EDGE_RELATIONS:
            raise ValueError(f"unknown relation {relation!r}")
        edge_id = _id()
        self.conn.execute(
            "INSERT INTO edges(id, src_node_id, dst_node_id, relation, confidence) "
            "VALUES (?,?,?,?,?)",
            (edge_id, src_node_id, dst_node_id, relation, confidence),
        )
        self.conn.commit()
        return edge_id

    # --- bios -------------------------------------------------------------
    def add_bio(
        self,
        person_node_id: str,
        source: str,
        source_url: str,
        license: str,
        title: str | None = None,
        summary: str | None = None,
        fetched_at: str | None = None,
    ) -> str:
        if not license:
            raise ValueError("a bio requires a non-null license (invariant)")
        bio_id = _id()
        self.conn.execute(
            "INSERT INTO bios"
            "(id, person_node_id, source, source_url, license, title, summary, fetched_at) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (bio_id, person_node_id, source, source_url, license, title, summary, fetched_at),
        )
        self.conn.commit()
        return bio_id

    # --- citation assembly (read) -----------------------------------------
    def cite_node(self, node_id: str) -> list[PodcastCitation]:
        """Assemble PodcastCitation(s) for a podcast node — one per mention.

        Raises UncitableNodeError if a podcast node has no resolvable citation,
        so an uncitable node is never served (§4a.5).
        """
        node = self.conn.execute(
            "SELECT id, type, label, origin FROM nodes WHERE id=?", (node_id,)
        ).fetchone()
        if node is None:
            raise UncitableNodeError(f"no node {node_id!r}")
        if node["origin"] != "podcast":
            raise UncitableNodeError(
                f"cite_node is for podcast nodes; {node_id!r} is origin={node['origin']}"
            )
        rows = self.conn.execute(
            """SELECT m.timestamp_s, m.quote,
                      v.id AS video_id, v.title, v.published_at, v.url AS video_url,
                      c.name AS host, c.url AS channel_url, c.handle AS channel_handle
               FROM mentions m
               JOIN videos v   ON v.id = m.video_id
               JOIN channels c ON c.id = v.channel_id
               WHERE m.node_id = ?
               ORDER BY m.timestamp_s""",
            (node_id,),
        ).fetchall()
        if not rows:
            raise UncitableNodeError(f"podcast node {node_id!r} has no mentions — cannot cite")

        leaders = self._leaders_for_node(node_id, node["type"], node["label"])
        citations: list[PodcastCitation] = []
        for r in rows:
            citations.append(
                PodcastCitation(
                    channel_name=r["channel_handle"] or r["host"] or "",
                    channel_url=r["channel_url"] or "",
                    episode_title=r["title"] or "",
                    published_at=r["published_at"] or "",
                    video_url=r["video_url"] or "",
                    timestamp_s=int(r["timestamp_s"]),
                    host=r["host"] or "",
                    leaders=leaders,
                )
            )
        return citations

    def cite_bio(self, person_node_id: str) -> ExternalCitation | None:
        row = self.conn.execute(
            "SELECT source, source_url, license FROM bios WHERE person_node_id=? LIMIT 1",
            (person_node_id,),
        ).fetchone()
        if row is None:
            return None
        return ExternalCitation(
            source_name=row["source"], source_url=row["source_url"], license=row["license"]
        )

    def _leaders_for_node(self, node_id: str, node_type: str, label: str) -> list[str]:
        """The subjects (leaders) a node is about — distinct from the host.

        If the node is itself a person, it IS the leader. Otherwise, the leaders are
        the person nodes connected to it by an edge.
        """
        if node_type == "person":
            return [label]
        rows = self.conn.execute(
            """SELECT DISTINCT n.label
               FROM edges e JOIN nodes n ON n.id IN (e.src_node_id, e.dst_node_id)
               WHERE (e.src_node_id=? OR e.dst_node_id=?) AND n.type='person' AND n.id<>?""",
            (node_id, node_id, node_id),
        ).fetchall()
        return [r["label"] for r in rows]

    # --- reads / counts ---------------------------------------------------
    def count(self, table: str) -> int:
        allowed = {"channels", "videos", "transcripts", "nodes", "edges", "mentions", "bios"}
        if table not in allowed:
            raise ValueError(f"cannot count unknown table {table!r}")
        return self.conn.execute(f"SELECT COUNT(*) AS n FROM {table}").fetchone()["n"]

    def fts_search(self, query: str, limit: int = 20) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT video_id, snippet(transcripts_fts, 1, '[', ']', '…', 12) AS snippet "
            "FROM transcripts_fts WHERE transcripts_fts MATCH ? LIMIT ?",
            (query, limit),
        ).fetchall()

    def _check_node_type(self, type: str) -> None:
        if type not in NODE_TYPES:
            raise ValueError(f"unknown node type {type!r}; allowed: {sorted(NODE_TYPES)}")
