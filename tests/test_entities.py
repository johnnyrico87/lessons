"""Entity-resolution decisions + audit (the release gate).

Proves suspect detection flags the real noise, decisions are durable + keyed by
entity shape (survive re-extraction), and apply mutates the graph correctly and
transactionally.
"""

from __future__ import annotations

from lessons.entities import (
    apply_decisions,
    audit,
    record_decision,
    undecided_suspects,
)
from lessons.store import Mention, Store


def _store():
    s = Store.memory()
    s.add_channel("C1", "@h", "https://x", "Host")
    s.add_video("v1", "C1", "ep", "2024-01-01", 600, "https://youtu.be/v1")
    return s


def _person(s, label, q="quote"):
    return s.add_podcast_node("person", label, mentions=[Mention("v1", 1, q)])


# --- audit / detection -----------------------------------------------------

def test_audit_flags_single_token_person():
    s = _store()
    try:
        _person(s, "Wilson")                 # single token → suspect
        _person(s, "Miyamoto Musashi")       # full name → clean
        labels = {sp.label for sp in audit(s)}
        assert "Wilson" in labels
        assert "Miyamoto Musashi" not in labels
    finally:
        s.close()


def test_audit_flags_single_word_era():
    s = _store()
    try:
        s.add_podcast_node("era", "Nazism", mentions=[Mention("v1", 1, "q")])
        s.add_podcast_node("era", "1600s Japan", mentions=[Mention("v1", 2, "q")])
        flagged = {sp.label for sp in audit(s)}
        assert "Nazism" in flagged            # single word era → suspect
        assert "1600s Japan" not in flagged
    finally:
        s.close()


def test_audit_flags_description_like_company():
    s = _store()
    try:
        s.add_podcast_node("company", "Leban's work", mentions=[Mention("v1", 1, "q")])
        assert any("description" in r for sp in audit(s) for r in sp.reasons)
    finally:
        s.close()


def test_clean_graph_has_no_suspects():
    s = _store()
    try:
        _person(s, "John D. Rockefeller")
        s.add_podcast_node(
            "situation", "a hostile takeover attempt", mentions=[Mention("v1", 1, "q")]
        )
        assert audit(s) == []
    finally:
        s.close()


# --- decisions: record, gate, survive re-extraction ------------------------

def test_decision_clears_the_gate():
    s = _store()
    try:
        _person(s, "Wilson")
        assert len(undecided_suspects(s)) == 1
        record_decision(s, "person", "Wilson", "drop", reason="host")
        assert undecided_suspects(s) == []      # decided → no longer blocks publish
    finally:
        s.close()


def test_decision_survives_reextraction_by_shape():
    s = _store()
    try:
        _person(s, "Gustav Leban")
        record_decision(s, "person", "Gustav Leban", "rename", target_label="Gustave Le Bon")
        # simulate re-extraction deleting + re-creating the same bad label
        s.conn.execute("DELETE FROM mentions")
        s.conn.execute("DELETE FROM nodes")
        s.conn.commit()
        _person(s, "Gustav Leban")
        # the decision (keyed by type+norm label) still matches the re-created node
        assert undecided_suspects(s) == []
    finally:
        s.close()


# --- apply -----------------------------------------------------------------

def test_apply_rename_in_place():
    s = _store()
    try:
        _person(s, "Gustav Leban")
        record_decision(s, "person", "Gustav Leban", "rename", target_label="Gustave Le Bon")
        r = apply_decisions(s)
        assert r.renamed == 1
        labels = {x["label"] for x in s.conn.execute("SELECT label FROM nodes")}
        assert labels == {"Gustave Le Bon"}
    finally:
        s.close()


def test_apply_drop_cascades():
    s = _store()
    try:
        host = _person(s, "Wilson")
        subj = _person(s, "Adolf Hitler")
        s.add_edge(subj, host, "cites")          # edge touching the dropped node
        record_decision(s, "person", "Wilson", "drop")
        r = apply_decisions(s)
        assert r.dropped == 1
        assert {x["label"] for x in s.conn.execute("SELECT label FROM nodes")} == {"Adolf Hitler"}
        assert s.conn.execute("SELECT COUNT(*) n FROM edges").fetchone()["n"] == 0
        assert s.conn.execute(
            "SELECT COUNT(*) n FROM mentions WHERE node_id=?", (host,)
        ).fetchone()["n"] == 0
    finally:
        s.close()


def test_apply_merge_repoints_edges_and_mentions():
    s = _store()
    try:
        _person(s, "Gustave Le Bon", q="canonical")
        dupe = _person(s, "Leban", q="variant")
        other = _person(s, "Vladimir Lenin")
        s.add_edge(other, dupe, "cites")         # edge into the dupe must repoint
        record_decision(s, "person", "Leban", "merge", target_label="Gustave Le Bon")
        r = apply_decisions(s)
        assert r.merged == 1
        labels = {x["label"] for x in s.conn.execute("SELECT label FROM nodes")}
        assert "Leban" not in labels and "Gustave Le Bon" in labels
        # the edge now points at the canonical node, and both mentions live there
        tgt = s.conn.execute(
            "SELECT id FROM nodes WHERE label='Gustave Le Bon'"
        ).fetchone()["id"]
        assert s.conn.execute(
            "SELECT COUNT(*) n FROM edges WHERE dst_node_id=?", (tgt,)
        ).fetchone()["n"] == 1
        assert s.conn.execute(
            "SELECT COUNT(*) n FROM mentions WHERE node_id=?", (tgt,)
        ).fetchone()["n"] == 2
    finally:
        s.close()


def test_apply_keep_is_noop():
    s = _store()
    try:
        _person(s, "Wilson")
        record_decision(s, "person", "Wilson", "keep", reason="really is a subject named Wilson")
        r = apply_decisions(s)
        assert r.kept == 1 and r.dropped == 0
        assert {x["label"] for x in s.conn.execute("SELECT label FROM nodes")} == {"Wilson"}
    finally:
        s.close()


def test_record_decision_validates_action():
    s = _store()
    try:
        import pytest
        with pytest.raises(ValueError, match="unknown action"):
            record_decision(s, "person", "X", "nuke")
        with pytest.raises(ValueError, match="rename requires"):
            record_decision(s, "person", "X", "rename")
    finally:
        s.close()
