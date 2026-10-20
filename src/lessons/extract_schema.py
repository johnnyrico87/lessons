"""Pydantic schemas for LLM extraction output (design §5).

The LLM is asked for JSON matching `ExtractionResult`. Invalid output is
rejected by validation (caller retries, then skips-with-reason) so malformed
model output never corrupts the graph.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, field_validator

from lessons.schema import EDGE_RELATIONS, NODE_TYPES


class NodeOut(BaseModel):
    """A node the LLM proposes, with the quote + timestamp that supports it."""

    type: str
    label: str
    summary: str | None = None
    quote: str = Field(..., min_length=1)
    timestamp_s: int = Field(..., ge=0)

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
    """The whole structured payload from one chunk."""

    nodes: list[NodeOut] = Field(default_factory=list)
    edges: list[EdgeOut] = Field(default_factory=list)
