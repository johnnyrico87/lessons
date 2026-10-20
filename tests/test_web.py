"""Phase 5 — web API tests (requirements §4a-4c, §5; design §7).

Uses FastAPI TestClient against build_app(store) with an in-memory graph — no
running server, no network.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from lessons.store import Mention, Store
from lessons.web import build_app


@pytest.fixture
def client():
    s = Store.memory()
    s.add_channel("C1", "@DavidSenra", "https://youtube.com/@DavidSenra", "David Senra")
    s.add_video("v1", "C1", "Rockefeller", "2024-01-01", 600, "https://youtu.be/v1")
    s.add_transcript("v1", "Rockefeller valued secrecy. Others were open.", "captions")
    sit = s.add_podcast_node(
        "situation", "deciding how much to disclose to partners",
        mentions=[Mention("v1", 30, "what to tell your partners")],
    )
    secrecy = s.add_podcast_node(
        "principle", "keep plans secret",
        mentions=[Mention("v1", 120, "He kept his plans secret.")],
    )
    openness = s.add_podcast_node(
        "principle", "radical transparency",
        mentions=[Mention("v1", 640, "Others preached radical transparency.")],
    )
    s.add_edge(sit, secrecy, "addresses_situation")
    s.add_edge(sit, openness, "addresses_situation")
    s.add_edge(secrecy, openness, "contradicts")
    c = TestClient(build_app(s))
    c._store = s  # keep a handle
    c._ids = {"sit": sit, "secrecy": secrecy, "openness": openness}
    yield c
    s.close()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json()["ok"] is True


# --- search endpoint ------------------------------------------------------

def test_search_returns_cited_pointers_no_synthesis(client):
    r = client.get("/api/search", params={"q": "how much to disclose to partners"})
    assert r.status_code == 200
    data = r.json()
    assert "answer" not in data                 # never synthesized
    assert len(data["pointers"]) == 2
    for p in data["pointers"]:
        assert p["citations"]
        c = p["citations"][0]
        assert c["host"] == "David Senra"       # host separate from leaders
        assert c["deep_link"].startswith("https://youtu.be/")


def test_search_surfaces_divergence(client):
    r = client.get("/api/search", params={"q": "disclose to partners"})
    pointers = r.json()["pointers"]
    assert any(p["divergent_from"] for p in pointers)


def test_search_requires_query(client):
    assert client.get("/api/search").status_code == 422  # q required


# --- fts ------------------------------------------------------------------

def test_fts_endpoint(client):
    r = client.get("/api/fts", params={"q": "secrecy"})
    assert r.status_code == 200
    assert r.json()["hits"]


# --- splash ---------------------------------------------------------------

def test_splash_seeded_when_no_person_nodes(client):
    # This graph has no 'person' node, so splash falls back to the seed set.
    r = client.get("/api/splash")
    data = r.json()
    assert data["seeded"] is True
    assert any(e["kind"] == "host" for e in data["elements"])


def test_splash_uses_real_person_nodes_when_present(client):
    # Add a person node with a quote; splash should now use real data.
    s = client._store
    s.add_podcast_node(
        "person", "John D. Rockefeller",
        mentions=[Mention("v1", 100, "Rockefeller kept his plans secret from everyone.")],
    )
    r = client.get("/api/splash")
    data = r.json()
    assert data["seeded"] is False
    labels = [e["label"] for e in data["elements"]]
    assert "John D. Rockefeller" in labels


# --- node detail ----------------------------------------------------------

def test_node_detail_has_citations(client):
    r = client.get(f"/api/node/{client._ids['secrecy']}")
    assert r.status_code == 200
    data = r.json()
    assert data["origin"] == "podcast"
    assert data["citations"]
    assert data["citations"][0]["host"] == "David Senra"


def test_node_404(client):
    assert client.get("/api/node/nope").status_code == 404


# --- static UI ------------------------------------------------------------

def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "situation or challenge" in r.text


def test_manifest_served(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.json()["display"] == "standalone"


# --- enrichment surfaces (Phase 7) ----------------------------------------

def test_node_detail_includes_external_bio_badged(client):
    s = client._store
    pid = s.add_podcast_node(
        "person", "Rockefeller", mentions=[Mention("v1", 100, "Rockefeller...")]
    )
    s.add_bio(
        pid, "Wikipedia", "https://en.wikipedia.org/wiki/John_D._Rockefeller",
        "CC BY-SA 4.0", title="John D. Rockefeller", summary="Industrialist.",
        image_url="https://img/r.jpg", image_license="see Wikimedia Commons",
    )
    data = client.get(f"/api/node/{pid}").json()
    assert data["origin"] == "podcast"          # the person node itself
    assert data["bio"]["origin"] == "external"  # bio clearly badged external
    assert data["bio"]["license"] == "CC BY-SA 4.0"
    assert data["bio"]["image_url"] == "https://img/r.jpg"
    # podcast citations and the external bio are separate keys, never merged
    assert "citations" in data and "bio" in data


def test_splash_includes_avatar_when_enriched(client):
    s = client._store
    pid = s.add_podcast_node(
        "person", "Carnegie", mentions=[Mention("v1", 50, "Carnegie built steel.")]
    )
    s.add_bio(
        pid, "Wikipedia", "https://en.wikipedia.org/wiki/Andrew_Carnegie",
        "CC BY-SA 4.0", image_url="https://img/c.jpg", image_license="see Commons",
    )
    data = client.get("/api/splash").json()
    carnegie = next(e for e in data["elements"] if e["label"] == "Carnegie")
    assert carnegie["image_url"] == "https://img/c.jpg"


# --- PWA assets (Phase 6) --------------------------------------------------

def test_service_worker_served_from_root(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert "application/javascript" in r.headers["content-type"]
    assert "serviceWorker".lower() in r.text.lower() or "caches" in r.text


def test_icons_served(client):
    for path in ("/static/icon-192.png", "/static/icon-512.png"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.content[:8] == b"\x89PNG\r\n\x1a\n"  # real PNG signature


def test_manifest_lists_icons_and_maskable(client):
    data = client.get("/manifest.webmanifest").json()
    srcs = [i["src"] for i in data["icons"]]
    assert "/static/icon-512.png" in srcs
    assert any("maskable" in i.get("purpose", "") for i in data["icons"])
