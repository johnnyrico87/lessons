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

def test_backstop_links_orphan_concept_to_sole_person(store):
    # Model emits a person + a lesson but NO edge between them. With exactly one
    # person in the chunk, the backstop attaches the lesson to that person.
    payload = _payload(
        nodes=[
            {"type":"person","label":"Rockefeller","quote":"secrecy","timestamp_s":1},
            {"type":"lesson","label":"Keep plans secret","quote":"kept secret","timestamp_s":2},
        ],
        edges=[],  # model forgot to link them
    )
    r = Extractor(store, FakeLLM([payload])).extract(["v1"])
    assert r.backstop_edges == 1
    assert r.edges_added == 1
    # the edge connects the person to the lesson with produced_lesson
    edge = store.conn.execute(
        "SELECT s.label AS src, d.label AS dst, e.relation FROM edges e "
        "JOIN nodes s ON s.id=e.src_node_id JOIN nodes d ON d.id=e.dst_node_id"
    ).fetchone()
    assert edge["src"] == "Rockefeller"
    assert edge["dst"] == "Keep plans secret"
    assert edge["relation"] == "produced_lesson"


def test_backstop_does_not_guess_when_multiple_people(store):
    # Two people in the chunk -> ambiguous subject -> backstop must NOT link,
    # because a wrong attribution is worse than a missing one.
    payload = _payload(
        nodes=[
            {"type":"person","label":"Rockefeller","quote":"secrecy","timestamp_s":1},
            {"type":"person","label":"Carnegie","quote":"openness","timestamp_s":2},
            {"type":"lesson","label":"Keep plans secret","quote":"secret","timestamp_s":3},
        ],
        edges=[],
    )
    r = Extractor(store, FakeLLM([payload])).extract(["v1"])
    assert r.backstop_edges == 0
    assert r.edges_added == 0


def test_backstop_skips_already_linked_concept(store):
    # A concept the model DID link is not double-linked by the backstop.
    payload = _payload(
        nodes=[
            {"type":"person","label":"Rockefeller","quote":"secrecy","timestamp_s":1},
            {"type":"lesson","label":"Keep plans secret","quote":"kept secret","timestamp_s":2},
        ],
        edges=[{"src_label":"Rockefeller","dst_label":"Keep plans secret","relation":"applied"}],
    )
    r = Extractor(store, FakeLLM([payload])).extract(["v1"])
    assert r.backstop_edges == 0
    assert r.edges_added == 1  # only the model's own edge


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


# --- extract uses persisted real timestamps (not t=0) ----------------------

def test_extract_chunks_on_persisted_line_timestamps(store):
    # Re-ingest v1's transcript WITH timestamped lines well past t=0.
    store.add_transcript(
        "v1",
        "early\nlate",
        "captions",
        lines=[{"start_s": 300, "text": "early"}, {"start_s": 1500, "text": "late"}],
    )

    # LLM emits a node WITHOUT its own timestamp detail => falls back to the
    # chunk's real start_s, which must come from the persisted lines, not 0.
    seen_chunks = []

    class ChunkAwareLLM:
        model_name = "fake"

        def complete(self, prompt, transcript_chunk):
            seen_chunks.append(transcript_chunk)
            # node timestamp_s=0 so the stored mention timestamp is driven by
            # the chunk start (the thing we're verifying)
            return _payload(
                [{"type": "lesson", "label": "x", "quote": "q", "timestamp_s": 0}]
            )

    Extractor(store, ChunkAwareLLM()).extract(["v1"])
    # chunks were built from the real lines (300s and 1500s are >600s apart,
    # so they land in separate windows)
    joined = "\n".join(seen_chunks)
    assert "early" in joined and "late" in joined
    # a mention landed at the first chunk's real start (300), never 0
    row = store.conn.execute(
        "SELECT MIN(timestamp_s) AS t FROM mentions"
    ).fetchone()
    assert row["t"] == 300


# --- tolerant parsing (local-model robustness) -----------------------------

def test_timestamp_clock_string_coerced():
    from lessons.extract_schema import coerce_timestamp, parse_tolerant
    assert coerce_timestamp("00:10:05") == 605
    assert coerce_timestamp("2:30") == 150
    assert coerce_timestamp("90") == 90
    assert coerce_timestamp(42) == 42
    assert coerce_timestamp("") == 0
    r = parse_tolerant({"nodes": [
        {"type": "person", "label": "Musashi", "quote": "a swordsman", "timestamp_s": "00:10:05"}
    ]})
    assert r.nodes[0].timestamp_s == 605


def test_bad_node_dropped_not_whole_chunk():
    from lessons.extract_schema import parse_tolerant
    r = parse_tolerant({"nodes": [
        {"type": "person", "label": "Good", "quote": "real quote", "timestamp_s": 1},
        {"type": "person", "label": "NoQuote", "quote": "", "timestamp_s": 2},   # dropped
        {"type": "widget", "label": "BadType", "quote": "x", "timestamp_s": 3},  # dropped
    ]})
    assert [n.label for n in r.nodes] == ["Good"]


def test_parser_keeps_edges_for_cross_chunk_resolution():
    # The parser no longer drops an edge whose endpoint is absent from THIS
    # chunk — a concept often links to a person from an earlier chunk, so
    # endpoint resolution is deferred to _apply (cross-chunk label map).
    from lessons.extract_schema import parse_tolerant
    r = parse_tolerant({
        "nodes": [{"type": "person", "label": "A", "quote": "q", "timestamp_s": 1}],
        "edges": [{"src_label": "A", "dst_label": "Ghost", "relation": "cites"}],
    })
    assert len(r.edges) == 1  # kept; _apply decides if it resolves


def test_apply_skips_truly_dangling_edge(store):
    # An edge to a label that never appears in ANY chunk is skipped-with-reason
    # at apply time, not silently merged.
    import json as _json
    payload = _json.dumps({
        "nodes": [{"type": "person", "label": "A", "quote": "q", "timestamp_s": 1}],
        "edges": [{"src_label": "A", "dst_label": "Ghost", "relation": "cites"}],
    })

    class LLM:
        model_name = "fake"
        def complete(self, p, c): return payload

    r = Extractor(store, LLM()).extract(["v1"])
    assert r.edges_added == 0
    assert any("unknown label" in s for s in r.skips)


def test_extractor_salvages_valid_nodes_from_mixed_output(store):
    import json as _json
    payload = _json.dumps({"nodes": [
        {"type": "person", "label": "Musashi", "summary": "swordsman",
         "quote": "a traveling samurai", "timestamp_s": "00:00:10"},
        {"type": "person", "label": "DaVinci", "quote": "", "timestamp_s": "00:04:20"},
    ]})

    class LLM:
        model_name = "fake"
        def complete(self, p, c): return payload

    r = Extractor(store, LLM()).extract(["v1"])
    assert r.nodes_added == 1          # Musashi kept, empty-quote DaVinci dropped
    assert r.chunks_invalid_skipped == 0
    ts = store.conn.execute("SELECT timestamp_s FROM mentions").fetchone()["timestamp_s"]
    assert ts == 10                    # clock-string coerced
