"""Phase 3 — extract tests (requirements §2, design §5).

Drives the Extractor with a fake LLMClient — no Ollama, no network. Proves
validation, provenance, tension detection, prompt-version gating, entity
resolution, and chunking.
"""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from lessons.extract import Extractor, chunk_transcript
from lessons.extract_schema import ExtractionResult
from lessons.store import Store


class FakeLLM:
    """Returns a scripted response. `model_name` lets extract_runs record it.
    `responses` is a list consumed per-call; a str that isn't JSON exercises the
    invalid path."""

    def __init__(self, responses, model_name="fake-model"):
        self._responses = list(responses)
        self.model_name = model_name
        self.calls = 0

    def complete(self, prompt, transcript_chunk):
        self.calls += 1
        if self._responses:
            r = self._responses.pop(0)
        else:
            r = '{"nodes": [], "edges": []}'
        return r if isinstance(r, str) else json.dumps(r)


def _payload(nodes, edges=None):
    return json.dumps({"nodes": nodes, "edges": edges or []})


@pytest.fixture
def store():
    s = Store.memory()
    s.add_channel("C1", "@DavidSenra", "https://youtube.com/@DavidSenra", "David Senra")
    s.add_video("v1", "C1", "Rockefeller", "2024-01-01", 600, "https://youtu.be/v1")
    s.add_transcript("v1", "Rockefeller valued secrecy. Others preached openness.", "captions")
    yield s
    s.close()


# --- schema ---------------------------------------------------------------

def test_schema_rejects_unknown_type():
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {"nodes": [{"type": "widget", "label": "x", "quote": "q", "timestamp_s": 1}]}
        )


def test_schema_requires_quote_and_timestamp():
    with pytest.raises(ValidationError):
        ExtractionResult.model_validate(
            {"nodes": [{"type": "lesson", "label": "x"}]}  # missing quote/timestamp
        )


# --- valid extraction + provenance ----------------------------------------

def test_valid_extraction_writes_nodes_with_provenance(store):
    resp = _payload(
        nodes=[
            {"type": "person", "label": "Rockefeller", "quote": "Rockefeller valued secrecy.",
             "timestamp_s": 10},
            {"type": "principle", "label": "secrecy", "quote": "valued secrecy", "timestamp_s": 12},
        ],
        edges=[{"src_label": "Rockefeller", "dst_label": "secrecy", "relation": "applied"}],
    )
    llm = FakeLLM([resp])
    r = Extractor(store, llm).extract(["v1"])
    assert r.nodes_added == 2
    assert r.edges_added == 1
    assert store.count("nodes") == 2
    assert store.count("mentions") == 2  # provenance attached
    assert store.count("edges") == 1


# --- invalid JSON skip-with-reason (D§5) ----------------------------------

def test_invalid_json_skipped_with_reason_after_retries(store):
    llm = FakeLLM(["not json", "still not json", "{bad"])  # all invalid
    r = Extractor(store, llm, max_retries=2).extract(["v1"])
    assert r.chunks_invalid_skipped == 1
    assert store.count("nodes") == 0          # graph not corrupted
    assert any("invalid JSON" in s for s in r.skips)
    assert llm.calls == 3                      # 1 + 2 retries


def test_retry_recovers_on_second_attempt(store):
    good = _payload([{"type": "lesson", "label": "focus", "quote": "focus wins", "timestamp_s": 5}])
    llm = FakeLLM(["garbage", good])
    r = Extractor(store, llm, max_retries=2).extract(["v1"])
    assert r.nodes_added == 1
    assert r.chunks_invalid_skipped == 0


# --- tension detection (R§2.7) --------------------------------------------

def test_tension_edge_counted(store):
    resp = _payload(
        nodes=[
            {"type": "principle", "label": "secrecy", "quote": "secrecy", "timestamp_s": 10},
            {"type": "principle", "label": "openness", "quote": "openness", "timestamp_s": 20},
        ],
        edges=[{"src_label": "secrecy", "dst_label": "openness", "relation": "contradicts"}],
    )
    r = Extractor(store, FakeLLM([resp])).extract(["v1"])
    assert r.tension_edges == 1


# --- prompt-version gating (R§2.5-2.6) ------------------------------------

def test_same_prompt_version_skips_on_rerun(store):
    resp = _payload([{"type": "lesson", "label": "x", "quote": "q", "timestamp_s": 1}])
    Extractor(store, FakeLLM([resp]), prompt_version="v1").extract(["v1"])
    r2 = Extractor(store, FakeLLM([resp]), prompt_version="v1").extract(["v1"])
    assert r2.videos_skipped_same_version == 1
    assert r2.videos_processed == 0


def test_bumped_prompt_version_reextracts(store):
    resp = _payload([{"type": "lesson", "label": "x", "quote": "q", "timestamp_s": 1}])
    Extractor(store, FakeLLM([resp]), prompt_version="v1").extract(["v1"])
    r2 = Extractor(store, FakeLLM([resp]), prompt_version="v2").extract(["v1"])
    assert r2.videos_processed == 1
    assert r2.videos_skipped_same_version == 0


# --- entity resolution (D§5) ----------------------------------------------

def test_entity_resolution_collapses_same_label(store):
    store.add_video("v2", "C1", "More Rockefeller", "2024-03-01", 600, "https://youtu.be/v2")
    store.add_transcript("v2", "Rockefeller again.", "captions")
    resp1 = _payload(
        [{"type": "person", "label": "Rockefeller", "quote": "a", "timestamp_s": 1}]
    )
    resp2 = _payload(
        [{"type": "person", "label": "  rockefeller ", "quote": "b", "timestamp_s": 2}]
    )
    ex = Extractor(store, FakeLLM([resp1, resp2]))
    ex.extract(["v1", "v2"])
    # one person node, two mentions
    people = store.conn.execute("SELECT COUNT(*) n FROM nodes WHERE type='person'").fetchone()["n"]
    assert people == 1


# --- extract_runs recorded -------------------------------------------------

def test_extract_run_records_model_and_version(store):
    Extractor(store, FakeLLM([]), prompt_version="v1").extract(["v1"])
    row = store.conn.execute(
        "SELECT prompt_version, model FROM extract_runs ORDER BY started_at DESC LIMIT 1"
    ).fetchone()
    assert row["prompt_version"] == "v1"
    assert row["model"] == "fake-model"


# --- chunker ---------------------------------------------------------------

def test_chunk_transcript_windows_and_overlap():
    lines = [{"start_s": s, "text": f"line{s}"} for s in range(0, 1800, 60)]  # 30 lines, 0..1740
    chunks = chunk_transcript(lines, window_s=600, overlap_s=60)
    assert len(chunks) >= 3
    assert chunks[0].start_s == 0
    # chunks are ordered and each keeps a real start timestamp
    starts = [c.start_s for c in chunks]
    assert starts == sorted(starts)


def test_chunk_empty_is_empty():
    assert chunk_transcript([]) == []
