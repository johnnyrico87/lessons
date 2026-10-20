"""Phase 4 — situation-first search tests (requirements §4, design §4).

Builds a small graph in-memory and drives Search with NO embedder (keyword
fallback) — proving situation mapping, pointer assembly with citations,
divergence surfacing, recurrence ranking, and the no-synthesis result shape.
"""

from __future__ import annotations

import pytest

from lessons.search import Search, _keyword_overlap
from lessons.store import Mention, Store


@pytest.fixture
def graph():
    """A situation addressed by two contradicting principles, each from a leader,
    one of which recurs across two episodes."""
    s = Store.memory()
    s.add_channel("C1", "@DavidSenra", "https://youtube.com/@DavidSenra", "David Senra")
    s.add_video("v1", "C1", "Rockefeller", "2024-01-01", 600, "https://youtu.be/v1")
    s.add_video("v2", "C1", "Rockefeller again", "2024-02-01", 600, "https://youtu.be/v2")

    sit = s.add_podcast_node(
        "situation", "deciding how much to disclose to partners",
        mentions=[Mention("v1", 30, "the question of what to tell your partners")],
    )
    secrecy = s.add_podcast_node(
        "principle", "keep plans secret",
        mentions=[
            Mention("v1", 120, "He kept his plans secret even from partners."),
            Mention("v2", 90, "Again, secrecy was his edge."),  # recurs across 2 videos
        ],
    )
    openness = s.add_podcast_node(
        "principle", "radical transparency",
        mentions=[Mention("v1", 640, "Others preached radical transparency.")],
    )
    rockefeller = s.add_podcast_node(
        "person", "Rockefeller",
        mentions=[Mention("v1", 100, "Rockefeller...")],
    )
    s.add_edge(sit, secrecy, "addresses_situation")
    s.add_edge(sit, openness, "addresses_situation")
    s.add_edge(secrecy, openness, "contradicts")
    s.add_edge(rockefeller, secrecy, "applied")
    yield s, {"sit": sit, "secrecy": secrecy, "openness": openness, "rockefeller": rockefeller}
    s.close()


# --- situation mapping (keyword fallback) ---------------------------------

def test_maps_free_text_to_situation(graph):
    s, _ = graph
    res = Search(s).search("how much should I disclose to my partners")
    assert res.used_embeddings is False
    assert any("disclose to partners" in lbl for lbl in res.matched_situations)
    assert len(res.pointers) == 2  # both principles addressing the situation


# --- pointers carry citations, never synthesis (R§4.3, R§4.5) -------------

def test_pointers_are_cited(graph):
    s, _ = graph
    res = Search(s).search("disclose to partners")
    for p in res.pointers:
        assert p.citations, "every pointer must carry a citation"
        c = p.citations[0]
        assert c.host == "David Senra"
        assert c.deep_link.startswith("https://youtu.be/")
    # result shape has no synthesized 'answer' field
    assert "answer" not in res.to_dict()


# --- divergence surfaced (R§4.4) ------------------------------------------

def test_divergence_is_surfaced(graph):
    s, ids = graph
    res = Search(s).search("disclose to partners")
    secrecy = next(p for p in res.pointers if p.node_id == ids["secrecy"])
    openness = next(p for p in res.pointers if p.node_id == ids["openness"])
    # each flags the other as a divergent take
    assert ids["openness"] in secrecy.divergent_from
    assert ids["secrecy"] in openness.divergent_from


# --- recurrence ranking (R§4.6) -------------------------------------------

def test_recurring_conviction_outranks_offhand(graph):
    s, ids = graph
    res = Search(s).search("disclose to partners")
    # 'secrecy' recurs across 2 videos; 'openness' appears once → secrecy ranks first
    assert res.pointers[0].node_id == ids["secrecy"]
    assert res.pointers[0].score > res.pointers[1].score


# --- no situations / empty graph ------------------------------------------

def test_no_situations_returns_empty(graph):
    s, _ = graph
    # a query that maps to no situation node
    res = Search(s).search("quantum chromodynamics tax policy")
    assert res.pointers == []


# --- embedder path --------------------------------------------------------

class FakeEmbedder:
    """Maps known phrases to simple vectors so cosine prefers the right one."""

    def embed(self, text):
        t = text.lower()
        disclose = 1.0 if ("disclose" in t or "partner" in t) else 0.0
        other = 1.0 if "quantum" in t else 0.0
        return [disclose, other]


def test_embedder_path_sets_flag_and_matches(graph):
    s, _ = graph
    res = Search(s, embedder=FakeEmbedder()).search("what do I disclose to partners?")
    assert res.used_embeddings is True
    assert len(res.pointers) == 2


# --- helper ----------------------------------------------------------------

def test_keyword_overlap_basic():
    assert _keyword_overlap("disclose to partners", "how to disclose to your partners") > 0
    assert _keyword_overlap("", "anything") == 0.0
