"""Pass B — extract (requirements §2, design §5).

Turns stored transcripts into graph nodes/edges with provenance, using a local
LLM. The LLM call goes through the `LLMClient` protocol so the chunking,
validation, entity-resolution, provenance and tension logic are fully testable
with a fake client — no Ollama, no network.

Key guarantees:
  * JSON validated against Pydantic schemas; invalid output retried then
    skipped-with-reason, never corrupting the graph (R§2 / D§5).
  * every node carries a mention (video + timestamp + quote) — the Store's
    provenance invariant enforces this (R§2.4).
  * tensions (contradicts/contrasts) are a first-class extraction target (R§2.7).
  * prompt_version gates re-extraction: same version => skip, bump => re-extract
    (R§2.5-2.6).
  * frequency never merges distinct views into an average (R§2.8) — we only add
    nodes/edges the model emits; we never synthesize a "common thread".
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from pydantic import ValidationError

from lessons.extract_schema import ExtractionResult
from lessons.store import Mention, Store

PROMPT_VERSION = "v1"

# The extraction prompt. Kept here (mirrors prompts/extract_v1.txt) so the
# version string and the instruction travel together.
EXTRACT_PROMPT = """You extract structured knowledge from a podcast transcript chunk.

Return ONLY JSON of the form:
{"nodes": [{"type","label","summary","quote","timestamp_s"}],
 "edges": [{"src_label","dst_label","relation","confidence"}]}

node types: lesson, principle, person, company, era, experience, situation
edge relations: cites, applied, founded, aligns, contradicts, contrasts,
                produced_lesson, addresses_situation

Rules:
- Every node MUST include the exact supporting quote and its timestamp_s.
- Prefer SPECIFIC lessons tied to the experience that produced them.
- When two people take OPPOSING stances on the same situation, emit a
  contradicts/contrasts edge — tensions are as valuable as agreements.
- Do NOT invent a shared "common thread". Only emit what the text supports.
- If the chunk has no durable learning, return empty lists.
"""


class LLMClient(Protocol):
    """The network boundary. `complete` returns the model's raw text (JSON)."""

    def complete(self, prompt: str, transcript_chunk: str) -> str: ...


# --- chunking --------------------------------------------------------------

@dataclass
class Chunk:
    text: str
    start_s: int


def chunk_transcript(
    lines: list[dict],
    window_s: int = 600,
    overlap_s: int = 60,
) -> list[Chunk]:
    """Split timestamped transcript lines into overlapping time windows.

    `lines` = [{"start_s": int, "text": str}, ...]. Each chunk keeps the
    timestamp of its first line so mentions stay accurate (D§5). Overlap keeps a
    lesson that spans a boundary from being lost.
    """
    if not lines:
        return []
    lines = sorted(lines, key=lambda line: line["start_s"])
    chunks: list[Chunk] = []
    i = 0
    n = len(lines)
    while i < n:
        start = lines[i]["start_s"]
        window_end = start + window_s
        j = i
        buf: list[str] = []
        while j < n and lines[j]["start_s"] < window_end:
            buf.append(lines[j]["text"])
            j += 1
        chunks.append(Chunk(text="\n".join(buf), start_s=start))
        if j >= n:
            break
        # advance to the first line at/after (window_end - overlap)
        next_start_time = window_end - overlap_s
        k = j
        while k > i and lines[k - 1]["start_s"] >= next_start_time:
            k -= 1
        i = max(k, i + 1)
    return chunks


# --- extraction ------------------------------------------------------------

def _id() -> str:
    return uuid.uuid4().hex


@dataclass
class ExtractResult:
    videos_processed: int = 0
    videos_skipped_same_version: int = 0
    chunks_total: int = 0
    chunks_invalid_skipped: int = 0
    nodes_added: int = 0
    edges_added: int = 0
    tension_edges: int = 0
    skips: list[str] = field(default_factory=list)


class Extractor:
    def __init__(
        self,
        store: Store,
        llm: LLMClient,
        prompt_version: str = PROMPT_VERSION,
        max_retries: int = 2,
    ):
        self.store = store
        self.llm = llm
        self.prompt_version = prompt_version
        self.max_retries = max_retries

    def _already_extracted(self, video_id: str) -> bool:
        """A video is done at this prompt version if an extract_run at this
        version is recorded for it (via meta key). Bumping the version re-does it.
        """
        row = self.store.conn.execute(
            "SELECT value FROM meta WHERE key=?", (f"extracted:{video_id}",)
        ).fetchone()
        return bool(row and row["value"] == self.prompt_version)

    def _mark_extracted(self, video_id: str) -> None:
        self.store.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)",
            (f"extracted:{video_id}", self.prompt_version),
        )
        self.store.conn.commit()

    def _parse(self, raw: str) -> ExtractionResult | None:
        try:
            data = json.loads(raw)
            return ExtractionResult.model_validate(data)
        except (json.JSONDecodeError, ValidationError):
            return None

    def extract(self, video_ids: list[str] | None = None) -> ExtractResult:
        result = ExtractResult()

        run_id = _id()
        self.store.conn.execute(
            "INSERT INTO extract_runs(id, prompt_version, model, started_at) VALUES (?,?,?,?)",
            (run_id, self.prompt_version, getattr(self.llm, "model_name", "unknown"),
             datetime.now(UTC).isoformat()),
        )
        self.store.conn.commit()

        if video_ids is None:
            rows = self.store.conn.execute("SELECT video_id FROM transcripts").fetchall()
            video_ids = [r["video_id"] for r in rows]

        for vid in video_ids:
            if self._already_extracted(vid):
                result.videos_skipped_same_version += 1
                continue
            self._extract_one(vid, result)
            self._mark_extracted(vid)
            result.videos_processed += 1

        return result

    def _extract_one(self, video_id: str, result: ExtractResult) -> None:
        trow = self.store.conn.execute(
            "SELECT text FROM transcripts WHERE video_id=?", (video_id,)
        ).fetchone()
        if not trow:
            result.skips.append(f"{video_id}: no transcript")
            return

        # Reconstruct timestamped lines if stored; else one chunk at t=0.
        lines = self._lines_for(video_id, trow["text"])
        chunks = chunk_transcript(lines)
        result.chunks_total += len(chunks)

        # label -> node_id within this video, so edges can resolve by label.
        label_to_node: dict[tuple[str, str], str] = {}

        for chunk in chunks:
            parsed = self._complete_with_retries(chunk)
            if parsed is None:
                result.chunks_invalid_skipped += 1
                result.skips.append(f"{video_id}@{chunk.start_s}s: invalid JSON after retries")
                continue
            self._apply(video_id, chunk, parsed, label_to_node, result)

    def _complete_with_retries(self, chunk: Chunk) -> ExtractionResult | None:
        for _ in range(self.max_retries + 1):
            raw = self.llm.complete(EXTRACT_PROMPT, chunk.text)
            parsed = self._parse(raw)
            if parsed is not None:
                return parsed
        return None

    def _apply(
        self,
        video_id: str,
        chunk: Chunk,
        parsed: ExtractionResult,
        label_to_node: dict[tuple[str, str], str],
        result: ExtractResult,
    ) -> None:
        for node in parsed.nodes:
            ts = node.timestamp_s if node.timestamp_s else chunk.start_s
            node_id = self.store.upsert_node_by_label(
                node.type,
                node.label,
                mentions=[Mention(video_id, ts, node.quote)],
                summary=node.summary,
            )
            key = (node.type, node.label.strip().lower())
            if key not in label_to_node:
                label_to_node[key] = node_id
                result.nodes_added += 1

        for edge in parsed.edges:
            src = self._resolve_label(edge.src_label, label_to_node)
            dst = self._resolve_label(edge.dst_label, label_to_node)
            if not src or not dst:
                result.skips.append(
                    f"{video_id}@{chunk.start_s}s: edge {edge.relation} refers to unknown label"
                )
                continue
            self.store.add_edge(src, dst, edge.relation, edge.confidence)
            result.edges_added += 1
            if edge.relation in ("contradicts", "contrasts"):
                result.tension_edges += 1

    @staticmethod
    def _resolve_label(label: str, label_to_node: dict[tuple[str, str], str]) -> str | None:
        norm = label.strip().lower()
        for (_type, lbl), node_id in label_to_node.items():
            if lbl == norm:
                return node_id
        return None

    def _lines_for(self, video_id: str, fallback_text: str) -> list[dict]:
        """Use persisted per-line timestamps when available (so chunks and
        mentions carry real minutes); fall back to a single t=0 chunk only when
        no lines were stored (e.g. a text-only transcript)."""
        lines = self.store.lines_for(video_id)
        if lines:
            return lines
        return [{"start_s": 0, "text": fallback_text}]
