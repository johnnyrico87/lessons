"""Phase 7 — enrich tests (requirements §3, design §8).

Drives the Enricher with a fake BioProvider — no network. Proves enrichment,
resumability, not-found handling, licensing enforcement, and the external-origin
separation.
"""

from __future__ import annotations

import pytest

from lessons.enrich import Bio, Enricher
from lessons.store import Mention, Store


class FakeBioProvider:
    def __init__(self, bios):
        self._bios = bios            # label -> Bio | None
        self.calls = []

    def fetch_bio(self, person_label):
        self.calls.append(person_label)
        return self._bios.get(person_label)


@pytest.fixture
def store():
    s = Store.memory()
    s.add_channel("C1", "@DavidSenra", "u", "David Senra")
    s.add_video("v1", "C1", "T", "2024-01-01", 600, "https://youtu.be/v1")
    s.add_podcast_node("person", "Rockefeller", mentions=[Mention("v1", 1, "q")])
    s.add_podcast_node("person", "Carnegie", mentions=[Mention("v1", 2, "q")])
    s.add_podcast_node("principle", "secrecy", mentions=[Mention("v1", 3, "q")])
    yield s
    s.close()


def _bio(summary="A famous industrialist.", img=None):
    return Bio(
        source="Wikipedia",
        source_url="https://en.wikipedia.org/wiki/x",
        license="CC BY-SA 4.0",
        title="X",
        summary=summary,
        image_url=img,
        image_license=("see Wikimedia Commons" if img else None),
    )


def test_enriches_only_person_nodes(store):
    prov = FakeBioProvider({"Rockefeller": _bio(), "Carnegie": _bio()})
    r = Enricher(store, prov).enrich()
    assert r.people_total == 2           # the 'principle' node is not a person
    assert r.enriched == 2
    assert set(prov.calls) == {"Rockefeller", "Carnegie"}


def test_skips_already_enriched(store):
    prov = FakeBioProvider({"Rockefeller": _bio(), "Carnegie": _bio()})
    Enricher(store, prov).enrich()
    r2 = Enricher(store, prov).enrich()
    assert r2.enriched == 0
    assert r2.skipped_existing == 2


def test_refresh_reenriches(store):
    prov = FakeBioProvider({"Rockefeller": _bio(), "Carnegie": _bio()})
    Enricher(store, prov).enrich()
    r2 = Enricher(store, prov, refresh=True).enrich()
    assert r2.enriched == 2


def test_not_found_counts(store):
    prov = FakeBioProvider({"Rockefeller": _bio(), "Carnegie": None})
    r = Enricher(store, prov).enrich()
    assert r.enriched == 1
    assert r.not_found == 1


def test_unlicensed_bio_is_not_stored(store):
    bad = Bio(source="S", source_url="u", license="", summary="x")
    prov = FakeBioProvider({"Rockefeller": bad, "Carnegie": _bio()})
    r = Enricher(store, prov).enrich()
    assert r.enriched == 1          # only Carnegie
    assert r.not_found == 1


def test_bio_persisted_with_image_and_license(store):
    prov = FakeBioProvider({"Rockefeller": _bio(img="https://img/x.jpg"), "Carnegie": _bio()})
    Enricher(store, prov).enrich()
    pid = store.conn.execute(
        "SELECT id FROM nodes WHERE label='Rockefeller'"
    ).fetchone()["id"]
    bio = store.bio_for(pid)
    assert bio["license"] == "CC BY-SA 4.0"
    assert bio["image_url"] == "https://img/x.jpg"
    assert bio["image_license"] == "see Wikimedia Commons"
    # external citation reads back
    cite = store.cite_bio(pid)
    assert cite.label == "Wikipedia · CC BY-SA 4.0"
