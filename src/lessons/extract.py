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

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from lessons.extract_schema import ExtractionResult, parse_tolerant
from lessons.store import Mention, Store

PROMPT_VERSION = "v5"

# Default chunking params — part of the run fingerprint, so changing them
# forces re-extraction (a different window/overlap yields a different graph).
CHUNK_WINDOW_S = 600
CHUNK_OVERLAP_S = 60

# The extraction prompt. Kept here (mirrors prompts/extract_v1.txt) so the
# version string and the instruction travel together.
EXTRACT_PROMPT = """You extract structured knowledge from a podcast transcript chunk.

Return ONLY JSON of the form:
{"nodes": [{"type","label","summary","quote","timestamp_s"}],
 "edges": [{"src_label","dst_label","relation","confidence"}]}

node types: lesson, principle, person, company, era, experience, situation
edge relations: cites, applied, founded, aligns, contradicts, contrasts,
                produced_lesson, addresses_situation

THE MOST IMPORTANT RULE — LINK EVERY CONCEPT TO A PERSON OR COMPANY:
A lesson, principle, experience, or situation NEVER stands alone. For EVERY
concept node you emit, you MUST also emit an edge connecting it to the person
(or company) who produced, embodies, applied, or faced it — using that person's
EXACT label as it appears in your nodes list. A concept with no edge to a person
is useless and will be discarded. Prefer these relations:
  person --produced_lesson--> lesson/principle   (they taught or demonstrated it)
  person --applied--> lesson/principle            (they put it into practice)
  person --addresses_situation--> situation       (they faced or responded to it)
  person --experience--> is wrong; instead: person applied/produced from an experience,
    so link the experience to the person with `applied` or `produced_lesson`.
If a concept truly belongs to no named person in this chunk, DROP it rather than
leaving it unlinked.

Rules:
- Every node MUST include the exact supporting quote and its timestamp_s.
- Attribute concepts to the SUBJECT being discussed (the leader), NOT the podcast
  host/narrator. Do not create a person node for the host or interviewer.
- Use the person's full real name as the label (e.g. "Gustave Le Bon", not a
  paraphrase or a role like "Austrian fellow").
- Prefer SPECIFIC lessons tied to the experience that produced them.
- When two people take OPPOSING stances on the same situation, emit a
  contradicts/contrasts edge — tensions are as valuable as agreements.
- Do NOT invent a shared "common thread". Only emit what the text supports.
- If the chunk has no durable learning, return empty lists.

Edge pattern (do NOT copy these words — they are a shape, not content): for a
lesson the subject taught, emit person --produced_lesson--> lesson; for a
situation they faced, person --addresses_situation--> situation; for a principle
they lived by, person --applied--> principle. Always use the subject's exact
person label as src_label and the concept's exact label as dst_label.
"""


class LLMClient(Protocol):
    """The network boundary. `complete` returns the model's raw text (JSON)."""

    def complete(self, prompt: str, transcript_chunk: str) -> str: ...


def _extractor_code_sha() -> str:
    """A stable self-hash of the extraction LOGIC that shapes the graph: the
    prompt text, the node/edge vocabularies, and the chunk defaults. An installed
    package has no git SHA at runtime, so we hash the inputs that actually change
    the output. Bumping PROMPT_VERSION changes the prompt and thus this hash;
    editing the prompt body without bumping the version STILL changes it — which
    is the point (silent prompt drift is caught)."""
    from lessons.extract_schema import EDGE_RELATIONS, NODE_TYPES

    material = "\x1f".join(
        [
            EXTRACT_PROMPT,
            ",".join(sorted(NODE_TYPES)),
            ",".join(sorted(EDGE_RELATIONS)),
            f"window={CHUNK_WINDOW_S}",
            f"overlap={CHUNK_OVERLAP_S}",
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


@dataclass
class RunFingerprint:
    """Everything that, if changed, should force re-extraction. The `fingerprint`
    is a hash over the whole tuple; the skip-gate compares THAT, not just the
    prompt version — so a new model digest, different sampling, or a changed
    chunk window all correctly invalidate a prior extraction."""

    prompt_version: str
    model: str
    model_digest: str
    sampling: dict
    chunk_window_s: int
    chunk_overlap_s: int
    code_sha: str

    @classmethod
    def compute(cls, llm: LLMClient, prompt_version: str) -> RunFingerprint:
        model = getattr(llm, "model_name", "unknown")
        digest_fn = getattr(llm, "model_digest", None)
        model_digest = ""
        if callable(digest_fn):
            try:
                model_digest = digest_fn() or ""
            except Exception:
                model_digest = ""
        sampling = getattr(llm, "sampling", {}) or {}
        return cls(
            prompt_version=prompt_version,
            model=model,
            model_digest=model_digest,
            sampling=dict(sampling),
            chunk_window_s=CHUNK_WINDOW_S,
            chunk_overlap_s=CHUNK_OVERLAP_S,
            code_sha=_extractor_code_sha(),
        )

    @property
    def sampling_json(self) -> str:
        return json.dumps(self.sampling, sort_keys=True)

    @property
    def fingerprint(self) -> str:
        """Stable sha256 over the whole tuple — the skip-gate's comparison key."""
        material = "\x1f".join(
            [
                self.prompt_version,
                self.model,
                self.model_digest,
                self.sampling_json,
                str(self.chunk_window_s),
                str(self.chunk_overlap_s),
                self.code_sha,
            ]
        )
        return hashlib.sha256(material.encode("utf-8")).hexdigest()


# --- chunking --------------------------------------------------------------

@dataclass
class Chunk:
    text: str
    start_s: int


def chunk_transcript(
    lines: list[dict],
    window_s: int = CHUNK_WINDOW_S,
    overlap_s: int = CHUNK_OVERLAP_S,
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
    backstop_edges: int = 0
    rejected_edges: int = 0
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
        # The provenance fingerprint for THIS extractor configuration. Computed
        # once (a model_digest lookup may hit the Ollama server) and reused as
        # both the skip-gate key and the recorded run provenance.
        self.fp = RunFingerprint.compute(llm, prompt_version)

    def _already_extracted(self, video_id: str) -> bool:
        """A video is done when the LAST extraction recorded for it used the
        SAME fingerprint as this run. Any change — prompt version, model digest,
        sampling, chunk window, or extractor code — yields a new fingerprint and
        so re-extracts. The fingerprint is stored under a per-video meta key.

        Back-compat: a DB written before v4 recorded only the bare
        prompt_version under `extracted:<id>`. A stored value that equals this
        run's prompt_version (not its fingerprint) is treated as a match so we
        don't needlessly re-extract every pre-v4 video on first upgrade."""
        row = self.store.conn.execute(
            "SELECT value FROM meta WHERE key=?", (f"extracted:{video_id}",)
        ).fetchone()
        if not row:
            return False
        stored = row["value"]
        return stored == self.fp.fingerprint or stored == self.prompt_version

    def _mark_extracted(self, video_id: str) -> None:
        self.store.conn.execute(
            "INSERT OR REPLACE INTO meta(key, value) VALUES (?,?)",
            (f"extracted:{video_id}", self.fp.fingerprint),
        )
        self.store.conn.commit()

    def _parse(self, raw: str) -> ExtractionResult | None:
        """Parse model output tolerantly. Returns a result (possibly with fewer
        nodes than the model emitted) when at least the JSON shape is valid;
        None only when no JSON object can be found at all."""
        text = raw.strip()
        # Models sometimes wrap JSON in prose or ```json fences — extract the
        # outermost {...} object.
        if not text.startswith("{"):
            start = text.find("{")
            end = text.rfind("}")
            if start == -1 or end == -1 or end <= start:
                return None
            text = text[start : end + 1]
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return None
        if not isinstance(data, dict):
            return None
        return parse_tolerant(data)

    def extract(self, video_ids: list[str] | None = None) -> ExtractResult:
        result = ExtractResult()

        run_id = _id()
        started = time.monotonic()
        fp = self.fp
        self.store.conn.execute(
            """INSERT INTO extract_runs
               (id, prompt_version, model, started_at, model_digest, sampling,
                chunk_window_s, chunk_overlap_s, code_sha, fingerprint)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                run_id,
                fp.prompt_version,
                fp.model,
                datetime.now(UTC).isoformat(),
                fp.model_digest,
                fp.sampling_json,
                fp.chunk_window_s,
                fp.chunk_overlap_s,
                fp.code_sha,
                fp.fingerprint,
            ),
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

        # Finalize the run row with completion telemetry (latency + count).
        self.store.conn.execute(
            "UPDATE extract_runs SET finished_at=?, runtime_s=?, videos_processed=? WHERE id=?",
            (
                datetime.now(UTC).isoformat(),
                round(time.monotonic() - started, 3),
                result.videos_processed,
                run_id,
            ),
        )
        self.store.conn.commit()

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

    # concept types that should always hang off a person/company, plus the
    # relation used when the backstop auto-links an orphan to the sole subject.
    _CONCEPT_TYPES = ("lesson", "principle", "experience", "situation")
    _BACKSTOP_RELATION = {
        "lesson": "produced_lesson",
        "principle": "applied",
        "experience": "applied",
        "situation": "addresses_situation",
    }
    # Entity-resolution guard: a relation whose TARGET type is fixed. A small
    # model emits garbage like `person produced_lesson person` or
    # `person addresses_situation era`; we reject any edge whose destination
    # type is not in the allowed set, so semantic noise never enters the graph.
    _RELATION_TARGET_TYPES = {
        "produced_lesson": {"lesson", "principle"},
        "addresses_situation": {"situation"},
        "founded": {"company"},
    }

    def _apply(
        self,
        video_id: str,
        chunk: Chunk,
        parsed: ExtractionResult,
        label_to_node: dict[tuple[str, str], str],
        result: ExtractResult,
    ) -> None:
        # Node ids introduced (or re-seen) in THIS chunk, split by role, so the
        # backstop can reason about this chunk's subject(s) and orphans.
        chunk_person_ids: list[str] = []
        chunk_concept_ids: list[str] = []

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
            if node.type == "person":
                chunk_person_ids.append(node_id)
            elif node.type in self._CONCEPT_TYPES:
                chunk_concept_ids.append((node.type, node_id))

        # id -> node type, so we can validate an edge's target type below.
        id_to_type = {nid: t for (t, _lbl), nid in label_to_node.items()}

        linked_concepts: set[str] = set()
        for edge in parsed.edges:
            src = self._resolve_label(edge.src_label, label_to_node)
            dst = self._resolve_label(edge.dst_label, label_to_node)
            if not src or not dst:
                result.skips.append(
                    f"{video_id}@{chunk.start_s}s: edge {edge.relation} refers to unknown label"
                )
                continue
            # Entity-resolution guard: reject a relation whose target is the
            # wrong kind of node (e.g. produced_lesson -> a person).
            allowed = self._RELATION_TARGET_TYPES.get(edge.relation)
            if allowed is not None and id_to_type.get(dst) not in allowed:
                result.rejected_edges += 1
                result.skips.append(
                    f"{video_id}@{chunk.start_s}s: edge {edge.relation} -> "
                    f"{id_to_type.get(dst)!r} rejected (target must be {sorted(allowed)})"
                )
                continue
            self.store.add_edge(src, dst, edge.relation, edge.confidence)
            result.edges_added += 1
            linked_concepts.add(src)
            linked_concepts.add(dst)
            if edge.relation in ("contradicts", "contrasts"):
                result.tension_edges += 1

        # Conservative backstop: a lesson/principle/experience/situation that the
        # model left UNLINKED is useless (it can never reach the detail drawer).
        # When the chunk has EXACTLY ONE person, that person is unambiguously the
        # subject, so attach each orphan concept to them. We DON'T guess when the
        # chunk names several people — a wrong link is worse than a missing one.
        if len(chunk_person_ids) == 1:
            subject = chunk_person_ids[0]
            for ctype, cid in chunk_concept_ids:
                if cid in linked_concepts:
                    continue
                relation = self._BACKSTOP_RELATION.get(ctype, "produced_lesson")
                self.store.add_edge(subject, cid, relation, confidence=None)
                result.edges_added += 1
                result.backstop_edges += 1

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
