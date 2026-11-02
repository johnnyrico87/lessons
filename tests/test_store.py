"""Phase 1 — store/DAL tests (design §3, §7a; requirements §4a, §8.2).

All in-memory SQLite, no network, no heavy deps.
"""

from __future__ import annotations

import pytest

from lessons.citations import (
    ExternalCitation,
    PodcastCitation,
    UncitableNodeError,
    media_embed,
    youtube_deep_link,
    youtube_video_id,
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


# --- deep-link bug fix: non-YouTube urls no longer fabricate links ---------

@pytest.mark.parametrize(
    "mp3_url",
    [
        "https://pdst.fm/e/chrt.fm/track/XXX/pscrb.fm/rss/p/foo/episode-123.mp3",
        "https://feeds.megaphone.fm/ATHLLC5883700320/audio.mp3",
        "https://example.com/some/path/clip.mp3",
    ],
)
def test_non_youtube_url_has_no_deep_link(mp3_url):
    # Previously this fabricated youtu.be/<trailing-segment> — a dead link.
    assert youtube_video_id(mp3_url) is None
    assert youtube_deep_link(mp3_url, 60) is None


def test_media_embed_youtube():
    emb = media_embed("https://youtu.be/dQw4w9WgXcQ", 185)
    assert emb["type"] == "youtube"
    assert emb["video_id"] == "dQw4w9WgXcQ"
    assert emb["src"] == (
        "https://www.youtube-nocookie.com/embed/dQw4w9WgXcQ?start=185&rel=0"
    )
    assert emb["start_s"] == 185


def test_media_embed_audio_fallback():
    mp3 = "https://feeds.megaphone.fm/ATHLLC/audio.mp3"
    emb = media_embed(mp3, 42)
    assert emb["type"] == "audio"
    assert emb["src"] == mp3
    assert emb["start_s"] == 42


def test_media_embed_none_for_unplayable():
    assert media_embed("", 0) is None
    assert media_embed("ftp://nope/x", 0) is None


def test_node_facts_carry_embed(store):
    # A lesson tied to a person, both mentioned in a YouTube-origin video, gets
    # a youtube embed descriptor on the fact for the drawer's click-to-play.
    person = store.add_podcast_node(
        "person", "Musashi", mentions=[Mention("SAMPLE0001", 10, "a swordsman")]
    )
    lesson = store.add_podcast_node(
        "lesson", "The Five Rings", mentions=[Mention("SAMPLE0001", 185, "a quote")]
    )
    store.add_edge(person, lesson, "produced_lesson")
    facts = store.node_facts(person)
    lesson_fact = next(f for f in facts if f["label"] == "The Five Rings")
    assert lesson_fact["embed"] is not None
    assert lesson_fact["embed"]["type"] == "youtube"
    assert lesson_fact["embed"]["start_s"] == 185


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


# --- seed export / import + first-run seeding ------------------------------

def _tiny_graph(s: Store) -> None:
    s.add_channel("C1", "@TakeOverPod", "https://youtube.com/@TakeOverPod", "Ben Wilson")
    s.add_video("v1", "C1", "Crowds", "2024-01-01", 341, "https://youtu.be/v1")
    s.add_transcript("v1", "secret transcript text", "whisper")
    a = s.add_podcast_node("person", "Le Bon",
                           mentions=[Mention("v1", 60, "studied crowds")])
    b = s.add_podcast_node("person", "Hitler",
                           mentions=[Mention("v1", 900, "spoke his way to power")])
    s.add_edge(a, b, "cites", 0.8)


def test_seed_export_excludes_transcript_text():
    s = Store.memory()
    _tiny_graph(s)
    seed = s.export_seed()
    assert "transcripts" not in seed                 # notes, not source text
    assert "secret transcript text" not in __import__("json").dumps(seed)
    assert len(seed["nodes"]) == 2 and len(seed["edges"]) == 1
    assert len(seed["mentions"]) == 2
    s.close()


def test_seed_roundtrip_rebuilds_identical_graph():
    src = Store.memory()
    _tiny_graph(src)
    seed = src.export_seed()
    src.close()

    dst = Store.memory()
    assert dst.is_empty()
    counts = dst.import_seed(seed)
    assert counts["nodes"] == 2 and counts["edges"] == 1
    assert not dst.is_empty()
    # citations resolve on the rebuilt graph (deep link + host)
    nid = dst.conn.execute("SELECT id FROM nodes WHERE label='Le Bon'").fetchone()["id"]
    cites = dst.cite_node(nid)
    assert cites and cites[0].host == "Ben Wilson"
    dst.close()


def test_seed_import_is_idempotent():
    src = Store.memory()
    _tiny_graph(src)
    seed = src.export_seed()
    src.close()
    dst = Store.memory()
    dst.import_seed(seed)
    dst.import_seed(seed)  # second import must not duplicate
    assert dst.count("nodes") == 2
    assert dst.count("edges") == 1
    dst.close()


def test_seed_if_empty_only_seeds_empty_db(tmp_path):
    import json

    from lessons.seed import seed_if_empty

    src = Store.memory()
    _tiny_graph(src)
    seed_file = tmp_path / "seed.json"
    seed_file.write_text(json.dumps(src.export_seed()), encoding="utf-8")
    src.close()

    s = Store.memory()
    first = seed_if_empty(s, path=seed_file)
    assert first and first["nodes"] == 2
    second = seed_if_empty(s, path=seed_file)   # already populated -> no-op
    assert second is None
    s.close()


def test_shipped_seed_file_is_valid_and_importable():
    """The committed seed/lessons-seed.json must load and rebuild a non-empty
    graph — this is what a fresh clone receives on first run."""
    from lessons.seed import SEED_PATH, load_seed

    assert SEED_PATH.exists(), "shipped seed file missing"
    seed = load_seed()
    assert seed and seed.get("nodes"), "shipped seed has no nodes"
    s = Store.memory()
    counts = s.import_seed(seed)
    assert counts["nodes"] >= 1
    assert not s.is_empty()
    s.close()
