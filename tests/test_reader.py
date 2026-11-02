"""Reader-side bundle loading + auto-update.

Proves the consumer contract: first run seeds from the baked bundle, a newer
content_version REPLACES the graph (removed rows actually disappear), an older
or equal version is a no-op, and a corrupted bundle is refused WITHOUT touching
the installed graph.
"""

from __future__ import annotations

import json

from lessons.bundle import build_bundle
from lessons.reader import (
    apply_bundle_text,
    install_baked_bundle,
    installed_content_version,
)
from lessons.store import Mention, Store


def _graph(labels, content_version):
    """Build a bundle whose payload contains one person node per label."""
    s = Store.memory()
    s.add_channel("C1", "@h", "https://x", "Host")
    s.add_video("v1", "C1", "ep", "2024-01-01", 600, "https://youtu.be/v1")
    for i, lbl in enumerate(labels):
        s.add_podcast_node("person", lbl, mentions=[Mention("v1", i + 1, f"q{i}")])
    payload = s.export_seed()
    s.close()
    return build_bundle(payload, content_version=content_version)


def _labels(store):
    return {r["label"] for r in store.conn.execute("SELECT label FROM nodes").fetchall()}


def test_first_run_seeds_from_baked_bundle(tmp_path):
    bundle = _graph(["Rockefeller", "Carnegie"], content_version=1)
    p = tmp_path / "graph-bundle.json"
    p.write_text(json.dumps(bundle), encoding="utf-8")
    s = Store.memory()
    try:
        out = install_baked_bundle(s, p)
        assert out.status == "seeded" and out.to_version == 1 and out.changed
        assert _labels(s) == {"Rockefeller", "Carnegie"}
        assert installed_content_version(s) == 1
    finally:
        s.close()


def test_missing_baked_bundle_is_nonfatal(tmp_path):
    s = Store.memory()
    try:
        out = install_baked_bundle(s, tmp_path / "nope.json")
        assert out.status == "error" and not out.changed
        assert s.is_empty()  # app simply starts empty
    finally:
        s.close()


def test_newer_bundle_replaces_and_removes(tmp_path):
    s = Store.memory()
    try:
        apply_bundle_text(s, json.dumps(_graph(["A", "B", "C"], 1)), first_run=True)
        assert _labels(s) == {"A", "B", "C"}
        # v2 drops B and C, adds D — replace must mirror that exactly
        out = apply_bundle_text(s, json.dumps(_graph(["A", "D"], 2)), first_run=False)
        assert out.status == "updated" and out.from_version == 1 and out.to_version == 2
        assert _labels(s) == {"A", "D"}            # B, C gone; D present
        assert installed_content_version(s) == 2
    finally:
        s.close()


def test_older_or_equal_bundle_is_noop(tmp_path):
    s = Store.memory()
    try:
        apply_bundle_text(s, json.dumps(_graph(["A", "B"], 5)), first_run=True)
        out = apply_bundle_text(s, json.dumps(_graph(["X"], 3)), first_run=False)
        assert out.status == "up_to_date" and not out.changed
        assert _labels(s) == {"A", "B"}            # untouched
        assert installed_content_version(s) == 5
        # equal version is also a no-op
        out2 = apply_bundle_text(s, json.dumps(_graph(["X"], 5)), first_run=False)
        assert out2.status == "up_to_date"
        assert _labels(s) == {"A", "B"}
    finally:
        s.close()


def test_tampered_update_rejected_graph_intact(tmp_path):
    s = Store.memory()
    try:
        apply_bundle_text(s, json.dumps(_graph(["A", "B"], 1)), first_run=True)
        bad = _graph(["A", "B", "C"], 2)
        bad["payload"]["nodes"][0]["label"] = "Corrupted"  # breaks the hash
        out = apply_bundle_text(s, json.dumps(bad), first_run=False)
        assert out.status == "error" and "rejected" in out.detail
        assert _labels(s) == {"A", "B"}            # original graph untouched
        assert installed_content_version(s) == 1   # version NOT advanced
    finally:
        s.close()


def test_replace_graph_rolls_back_on_failure():
    s = Store.memory()
    try:
        s.add_channel("C1", "@h", "https://x", "Host")
        s.add_video("v1", "C1", "ep", "2024-01-01", 600, "https://youtu.be/v1")
        s.add_podcast_node("person", "Keep", mentions=[Mention("v1", 1, "q")])
        # A payload whose edge references a node that isn't in the payload →
        # FK violation mid-import → the whole replace must roll back.
        bad_payload = {
            "channels": [], "videos": [], "bios": [], "mentions": [],
            "nodes": [{"id": "n1", "type": "person", "label": "New", "origin": "podcast"}],
            "edges": [{"id": "e1", "src_node_id": "n1", "dst_node_id": "GHOST",
                       "relation": "cites", "confidence": None}],
        }
        import sqlite3

        import pytest
        with pytest.raises(sqlite3.IntegrityError):
            s.replace_graph(bad_payload)
        # rolled back: the original graph is still there, nothing half-applied
        assert _labels(s) == {"Keep"}
    finally:
        s.close()
