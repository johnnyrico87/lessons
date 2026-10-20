"""Situation-first search (requirements §4, design §4).

The core product flow: a user types a situation in plain language; we map it to
the nearest situation node(s), traverse to the lessons/experiences that address
them, expand to contradicting/contrasting takes (divergence), rank, and return
CITED POINTERS — never a synthesized answer.

Embeddings are optional: the `Embedder` protocol lets a real sentence-transformer
plug in, but search degrades to keyword matching (SQLite FTS + label match) when
no embedder is present, so it works before extraction is deep (R§4.2, D§4). The
ranking/traversal/divergence logic is pure and fully testable with no model.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from lessons.citations import PodcastCitation
from lessons.store import Store


class Embedder(Protocol):
    """Optional semantic matcher. Returns a vector for a text."""

    def embed(self, text: str) -> list[float]: ...


@dataclass
class ResultPointer:
    """One cited pointer — NOT a synthesized answer (R§4.5)."""

    node_id: str
    node_type: str          # lesson | experience | principle | ...
    label: str
    summary: str | None
    citations: list[PodcastCitation]
    score: float
    divergent_from: list[str] = field(default_factory=list)  # node_ids this contrasts with

    def to_dict(self) -> dict:
        return {
            "node_id": self.node_id,
            "node_type": self.node_type,
            "label": self.label,
            "summary": self.summary,
            "citations": [c.to_dict() for c in self.citations],
            "score": round(self.score, 4),
            "divergent_from": list(self.divergent_from),
        }


@dataclass
class SearchResults:
    query: str
    matched_situations: list[str]            # situation node labels we mapped to
    pointers: list[ResultPointer]
    used_embeddings: bool

    def to_dict(self) -> dict:
        return {
            "query": self.query,
            "matched_situations": self.matched_situations,
            "pointers": [p.to_dict() for p in self.pointers],
            "used_embeddings": self.used_embeddings,
        }


def _cosine(a: list[float], b: list[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


# relations that carry a lesson/experience away from a situation
_ADDRESS_RELATIONS = ("addresses_situation",)
_TENSION_RELATIONS = ("contradicts", "contrasts")


class Search:
    def __init__(self, store: Store, embedder: Embedder | None = None):
        self.store = store
        self.embedder = embedder

    # --- situation mapping ------------------------------------------------
    def _match_situations(self, query: str, k: int = 3) -> list[tuple[str, str, float]]:
        """Return up to k (node_id, label, score) situation nodes best matching
        the query. Semantic when an embedder is present, else keyword overlap."""
        rows = self.store.conn.execute(
            "SELECT id, label, summary FROM nodes WHERE type='situation'"
        ).fetchall()
        if not rows:
            return []

        if self.embedder is not None:
            qv = self.embedder.embed(query)
            scored = []
            for r in rows:
                text = r["label"] + " " + (r["summary"] or "")
                scored.append((r["id"], r["label"], _cosine(qv, self.embedder.embed(text))))
        else:
            scored = []
            for r in rows:
                text = r["label"] + " " + (r["summary"] or "")
                scored.append((r["id"], r["label"], _keyword_overlap(query, text)))
        scored = [s for s in scored if s[2] > 0]
        scored.sort(key=lambda s: s[2], reverse=True)
        return scored[:k]

    # --- recurrence (ranking signal) --------------------------------------
    def _recurrence(self, node_id: str) -> int:
        """Cross-source recurrence: how many DISTINCT videos mention this node.
        A durable conviction (many episodes) outranks an offhand remark (one)."""
        row = self.store.conn.execute(
            "SELECT COUNT(DISTINCT video_id) AS n FROM mentions WHERE node_id=?", (node_id,)
        ).fetchone()
        return row["n"] if row else 0

    # --- main flow --------------------------------------------------------
    def search(self, query: str, limit: int = 10) -> SearchResults:
        used_emb = self.embedder is not None
        situations = self._match_situations(query)
        matched_labels = [s[1] for s in situations]

        # Gather lessons/experiences that address the matched situations.
        pointer_ids: dict[str, float] = {}   # node_id -> relevance (situation match score)
        for sit_id, _label, sscore in situations:
            for target_id in self._addressed_by(sit_id):
                pointer_ids[target_id] = max(pointer_ids.get(target_id, 0.0), sscore)

        # Build pointers, attach citations + divergence, rank.
        pointers: list[ResultPointer] = []
        for node_id, relevance in pointer_ids.items():
            node = self.store.conn.execute(
                "SELECT id, type, label, summary, origin FROM nodes WHERE id=?", (node_id,)
            ).fetchone()
            if node is None or node["origin"] != "podcast":
                continue
            try:
                citations = self.store.cite_node(node_id)
            except Exception:
                continue  # uncitable → never served (§4a.5)
            recurrence = self._recurrence(node_id)
            divergent = self._divergent_neighbors(node_id)
            score = relevance * (1.0 + 0.25 * recurrence) + (0.5 if divergent else 0.0)
            pointers.append(
                ResultPointer(
                    node_id=node_id,
                    node_type=node["type"],
                    label=node["label"],
                    summary=node["summary"],
                    citations=citations,
                    score=score,
                    divergent_from=divergent,
                )
            )

        pointers.sort(key=lambda p: p.score, reverse=True)
        return SearchResults(query, matched_labels, pointers[:limit], used_emb)

    def _addressed_by(self, situation_id: str) -> list[str]:
        rows = self.store.conn.execute(
            f"""SELECT CASE WHEN src_node_id=? THEN dst_node_id ELSE src_node_id END AS other
                FROM edges
                WHERE relation IN ({",".join("?" * len(_ADDRESS_RELATIONS))})
                  AND (src_node_id=? OR dst_node_id=?)""",
            (situation_id, *_ADDRESS_RELATIONS, situation_id, situation_id),
        ).fetchall()
        return [r["other"] for r in rows]

    def _divergent_neighbors(self, node_id: str) -> list[str]:
        rows = self.store.conn.execute(
            f"""SELECT CASE WHEN src_node_id=? THEN dst_node_id ELSE src_node_id END AS other
                FROM edges
                WHERE relation IN ({",".join("?" * len(_TENSION_RELATIONS))})
                  AND (src_node_id=? OR dst_node_id=?)""",
            (node_id, *_TENSION_RELATIONS, node_id, node_id),
        ).fetchall()
        return [r["other"] for r in rows]


def _keyword_overlap(query: str, text: str) -> float:
    """Jaccard-ish overlap of lowercased word sets — the no-embedder fallback."""
    q = {w for w in _words(query) if len(w) > 2}
    t = {w for w in _words(text) if len(w) > 2}
    if not q or not t:
        return 0.0
    return len(q & t) / len(q)


def _words(s: str) -> list[str]:
    return [w.strip(".,!?;:\"'()").lower() for w in s.split()]
