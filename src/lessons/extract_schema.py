"""Pydantic schemas for LLM extraction output (design §5).

The LLM is asked for JSON matching `ExtractionResult`. We validate structurally,
but tolerate the natural shapes a local model emits — a clock-string timestamp
("00:10:05") is coerced to int seconds, and nodes that lack a usable quote are
DROPPED rather than failing the whole chunk. This keeps the provenance guarantee
(a kept node always has a real quote + timestamp) without throwing away a chunk
of good extractions over a format nit.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from lessons.schema import EDGE_RELATIONS, NODE_TYPES


def coerce_timestamp(value) -> int:
    """Accept int, numeric string, or HH:MM:SS / MM:SS clock strings -> seconds."""
    if value is None:
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    s = str(value).strip()
    if not s:
        return 0
    if s.isdigit():
        return int(s)
    if ":" in s:
        parts = s.split(":")
        try:
            nums = [int(p) for p in parts]
        except ValueError:
            return 0
        total = 0
        for n in nums:
            total = total * 60 + n
        return total
    # strip a trailing 's' etc.
    digits = "".join(c for c in s if c.isdigit())
    return int(digits) if digits else 0


class NodeOut(BaseModel):
    """A node the LLM proposes, with the quote + timestamp that supports it."""

    type: str
    label: str
    summary: str | None = None
    quote: str = Field(..., min_length=1)
    timestamp_s: int = 0

    @field_validator("timestamp_s", mode="before")
    @classmethod
    def _coerce_ts(cls, v) -> int:
        return coerce_timestamp(v)

    @field_validator("type")
    @classmethod
    def _known_type(cls, v: str) -> str:
        if v not in NODE_TYPES:
            raise ValueError(f"unknown node type {v!r}")
        return v

    @field_validator("label")
    @classmethod
    def _nonempty_label(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("label must be non-empty")
        return v.strip()


class EdgeOut(BaseModel):
    """An edge between two proposed nodes, referenced by their labels."""

    src_label: str
    dst_label: str
    relation: str
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)

    @field_validator("relation")
    @classmethod
    def _known_relation(cls, v: str) -> str:
        if v not in EDGE_RELATIONS:
            raise ValueError(f"unknown relation {v!r}")
        return v


class ExtractionResult(BaseModel):
    """The whole structured payload from one chunk.

    Tolerant parsing: individual nodes that don't validate are dropped,
    not fatal — a chunk yields whatever valid items the model produced. Use
    `parse_tolerant` for that behavior; direct construction stays strict.
    """

    nodes: list[NodeOut] = Field(default_factory=list)
    edges: list[EdgeOut] = Field(default_factory=list)

    # NOTE: we deliberately do NOT drop edges whose endpoints are absent from
    # THIS chunk's node list. A concept extracted in a later chunk often links
    # to a person introduced in an earlier one; the extractor's _apply step
    # resolves edge endpoints against the accumulated cross-chunk label map and
    # skips-with-reason any that still don't resolve. Dropping here would
    # silently sever every cross-chunk concept->person link.


def parse_tolerant(data: dict) -> ExtractionResult:
    """Build an ExtractionResult, silently dropping nodes/edges that fail
    validation (bad type, empty quote, malformed edge) instead of raising. Raises
    only if `data` isn't the expected shape at all."""
    raw_nodes = data.get("nodes") or []
    raw_edges = data.get("edges") or []
    nodes: list[NodeOut] = []
    for rn in raw_nodes:
        try:
            nodes.append(NodeOut.model_validate(rn))
        except Exception:
            continue  # drop a single bad node, keep the rest
    edges: list[EdgeOut] = []
    for re_ in raw_edges:
        try:
            edges.append(EdgeOut.model_validate(re_))
        except Exception:
            continue
    return ExtractionResult(nodes=nodes, edges=edges)


def extraction_json_schema() -> dict:
    """JSON Schema for Ollama structured output (the `format` param).

    Grammar-constrains the model so it CANNOT emit a node type or edge relation
    outside our vocabulary, nor omit the fields we require. This removes the
    structural garbage a small model otherwise produces (invalid types like
    'book', missing quote fields). It does NOT fix semantic errors — a wrong
    attribution or a duplicate/misspelled entity is still possible. Derived from
    NODE_TYPES / EDGE_RELATIONS so it never drifts from the validation contract.
    """
    return {
        "type": "object",
        "properties": {
            "nodes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "type": {"type": "string", "enum": sorted(NODE_TYPES)},
                        "label": {"type": "string"},
                        "summary": {"type": "string"},
                        "quote": {"type": "string"},
                        "timestamp_s": {"type": "integer"},
                    },
                    "required": ["type", "label", "quote"],
                },
            },
            "edges": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "src_label": {"type": "string"},
                        "dst_label": {"type": "string"},
                        "relation": {"type": "string", "enum": sorted(EDGE_RELATIONS)},
                    },
                    "required": ["src_label", "dst_label", "relation"],
                },
            },
        },
        "required": ["nodes", "edges"],
    }
