"""Phase 1 — store/DAL tests (design §3, §7a; requirements §4a, §8.2).

All in-memory SQLite, no network, no heavy deps.
"""

from __future__ import annotations

import pytest

from lessons.citations import (
    ExternalCitation,
    PodcastCitation,
    UncitableNodeError,
    youtube_deep_link,
)
from lessons.store import Mention, Store


@pytest.fixture
def store():
    s = Store.memory()
    s.add_channel("C1", "@DavidSenra", "https://youtube.com/@DavidSenra", "David Senra")
    s.add_video(
        "SAMPLE0001", "C1", "Sample — John D. Rockefeller",
        "2024-01-15", 3600, "https://youtu.be/SAMPLE0001",
    )
    yield s
    s.close()


# --- schema / basic writes -------------------------------------------------

def test_schema_applies_and_counts_zero(store):
    for t in ("nodes", "edges", "mentions", "bios"):
        assert store.count(t) == 0
    assert store.count("channels") == 1
    assert store.count("videos") == 1


def test_rejects_unknown_node_type(store):
    with pytest.raises(ValueError):
        store.add_external_node("widget", "nope")


def test_rejects_unknown_edge_relation(store):
    a = store.add_external_node("person", "A")
    b = store.add_external_node("person", "B")
    with pytest.raises(ValueError):
        store.add_edge(a, b, "befriends")


# --- provenance invariant --------------------------------------------------

def test_podcast_node_requires_a_mention(store):
    with pytest.raises(UncitableNodeError):
        store.add_podcast_node("principle", "secrecy", mentions=[])


def test_podcast_node_with_mention_is_stored_and_citable(store):
    nid = store.add_podcast_node(
        "principle", "secrecy as advantage",
        mentions=[Mention("SAMPLE0001", 120, "He kept his plans secret.")],
    )
    assert store.count("nodes") == 1
    assert store.count("mentions") == 1
    cites = store.cite_node(nid)
    assert len(cites) == 1
    assert isinstance(cites[0], PodcastCitation)


def test_mention_requires_quote_and_timestamp_rolls_back(store):
    with pytest.raises(UncitableNodeError):
        store.add_podcast_node(
            "lesson", "bad", mentions=[Mention("SAMPLE0001", 10, "")]
        )
    # rollback: nothing persisted
    assert store.count("nodes") == 0
    assert store.count("mentions") == 0


def test_cite_node_rejects_external_origin(store):
    nid = store.add_external_node("person", "Someone")
    with pytest.raises(UncitableNodeError):
        store.cite_node(nid)


# --- host vs leader separation (§4a.6) ------------------------------------

def test_host_and_leader_are_separate_fields(store):
    # person node = the leader; host comes from the channel
    person = store.add_podcast_node(
        "person", "John D. Rockefeller",
        mentions=[Mention("SAMPLE0001", 120, "Rockefeller kept plans secret.")],
    )
    cite = store.cite_node(person)[0]
    assert cite.host == "David Senra"             # artist/host
    assert cite.leaders == ["John D. Rockefeller"]  # subject
    assert cite.host not in cite.leaders


def test_leader_resolved_via_edge_for_nonperson_node(store):
    person = store.add_podcast_node(
        "person", "Rockefeller",
        mentions=[Mention("SAMPLE0001", 100, "Rockefeller...")],
    )
    principle = store.add_podcast_node(
        "principle", "secrecy",
        mentions=[Mention("SAMPLE0001", 120, "secrecy as advantage")],
    )
    store.add_edge(person, principle, "applied")
    cite = store.cite_node(principle)[0]
    assert cite.leaders == ["Rockefeller"]
    assert cite.host == "David Senra"


# --- deep link -------------------------------------------------------------

def test_deep_link_timestamp(store):
    nid = store.add_podcast_node(
        "lesson", "x", mentions=[Mention("SAMPLE0001", 185, "...")]
    )
    cite = store.cite_node(nid)[0]
    assert cite.deep_link == "https://youtu.be/SAMPLE0001?t=185"


@pytest.mark.parametrize(
    "url,ts,expected",
    [
        ("https://youtu.be/ABC123", 42, "https://youtu.be/ABC123?t=42"),
        ("https://www.youtube.com/watch?v=ABC123", 42, "https://youtu.be/ABC123?t=42"),
        ("https://youtu.be/ABC123?t=9", 30, "https://youtu.be/ABC123?t=30"),
    ],
)
def test_youtube_deep_link_forms(url, ts, expected):
    assert youtube_deep_link(url, ts) == expected


# --- bios ------------------------------------------------------------------

def test_bio_requires_license(store):
    person = store.add_external_node("person", "Carnegie")
    with pytest.raises(ValueError):
        store.add_bio(person, "Wikipedia", "https://en.wikipedia.org/wiki/x", license="")


def test_bio_citation_assembled(store):
    person = store.add_external_node("person", "Carnegie")
    store.add_bio(person, "Wikipedia", "https://en.wikipedia.org/wiki/Andrew_Carnegie", "CC BY-SA")
    cite = store.cite_bio(person)
    assert isinstance(cite, ExternalCitation)
    assert cite.label == "Wikipedia · CC BY-SA"


def test_cite_bio_none_when_absent(store):
    person = store.add_external_node("person", "NoBio")
    assert store.cite_bio(person) is None


# --- entity resolution -----------------------------------------------------

def test_upsert_collapses_by_normalized_label(store):
    a = store.upsert_node_by_label(
        "person", "Rockefeller", mentions=[Mention("SAMPLE0001", 100, "a")]
    )
    b = store.upsert_node_by_label(
        "person", "  rockefeller ", mentions=[Mention("SAMPLE0001", 200, "b")]
    )
    assert a == b
    assert store.count("nodes") == 1
    assert store.count("mentions") == 2


# --- FTS -------------------------------------------------------------------

def test_fts_search_finds_transcript(store):
    store.add_transcript("SAMPLE0001", "Rockefeller valued secrecy above all.", "captions")
    hits = store.fts_search("secrecy")
    assert len(hits) == 1
    assert hits[0]["video_id"] == "SAMPLE0001"


# --- transcript line persistence -------------------------------------------

def test_lines_round_trip(store):
    lines = [
        {"start_s": 0, "text": "first"},
        {"start_s": 42, "text": "second"},
    ]
    store.add_transcript("SAMPLE0001", "first\nsecond", "captions", lines=lines)
    got = store.lines_for("SAMPLE0001")
    assert got == lines


def test_lines_absent_returns_empty(store):
    store.add_transcript("SAMPLE0001", "no lines here", "captions")
    assert store.lines_for("SAMPLE0001") == []


def test_reingest_replaces_lines(store):
    store.add_transcript("SAMPLE0001", "a", "captions", lines=[{"start_s": 1, "text": "a"}])
    store.add_transcript(
        "SAMPLE0001", "b", "captions", lines=[{"start_s": 2, "text": "b"}]
    )
    assert store.lines_for("SAMPLE0001") == [{"start_s": 2, "text": "b"}]


# --- bio image fields + schema upgrade -------------------------------------

def test_bio_image_fields_round_trip(store):
    person = store.add_external_node("person", "Carnegie")
    store.add_bio(
        person, "Wikipedia", "https://en.wikipedia.org/wiki/Andrew_Carnegie",
        "CC BY-SA 4.0", summary="Steel.", image_url="https://img/c.jpg",
        image_license="see Commons",
    )
    bio = store.bio_for(person)
    assert bio["image_url"] == "https://img/c.jpg"
    assert bio["image_license"] == "see Commons"


def test_add_bio_is_idempotent_per_person(store):
    person = store.add_external_node("person", "X")
    store.add_bio(person, "Wikipedia", "u1", "CC BY-SA 4.0", summary="first")
    store.add_bio(person, "Wikipedia", "u2", "CC BY-SA 4.0", summary="second")
    assert store.count("bios") == 1
    assert store.bio_for(person)["summary"] == "second"


def test_old_bios_table_gains_image_columns(tmp_path):
    import sqlite3
    p = tmp_path / "old.db"
    c = sqlite3.connect(p)
    # a pre-v3 bios table with no image columns
    c.execute(
        "CREATE TABLE bios(id TEXT PRIMARY KEY, person_node_id TEXT, source TEXT, "
        "source_url TEXT, license TEXT, title TEXT, summary TEXT, fetched_at TEXT)"
    )
    c.commit()
    c.close()
    s = Store(p)  # _migrate should ALTER in image_url / image_license
    cols = {r[1] for r in s.conn.execute("PRAGMA table_info(bios)").fetchall()}
    assert "image_url" in cols and "image_license" in cols
    s.close()
