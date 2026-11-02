"""Versioned graph bundle: format, integrity, version guards, and round-trip.

Proves the content-delivery contract the downloadable reader app relies on —
a corrupted or tampered bundle is REFUSED, a too-new bundle is refused, and a
good bundle's payload still imports through the existing seed loader.
"""

from __future__ import annotations

import json

import pytest

from lessons.bundle import (
    BUNDLE_FORMAT,
    BundleError,
    build_bundle,
    load_bundle_text,
    payload_sha256,
    verify_bundle,
    version_info,
)
from lessons.schema import SCHEMA_VERSION
from lessons.store import Mention, Store


@pytest.fixture
def populated():
    """A tiny real graph via the DAL, so export_seed() produces a genuine payload."""
    s = Store.memory()
    s.add_channel("C1", "@DavidSenra", "https://youtube.com/@DavidSenra", "David Senra")
    s.add_video("v1", "C1", "Rockefeller", "2024-01-01", 600, "https://youtu.be/v1")
    s.add_podcast_node(
        "person", "John D. Rockefeller",
        mentions=[Mention("v1", 12, "valued secrecy")],
    )
    yield s
    s.close()


def test_build_bundle_has_manifest_and_payload(populated):
    payload = populated.export_seed()
    b = build_bundle(payload, content_version=7)
    assert set(b) == {"manifest", "payload"}
    m = b["manifest"]
    assert m["bundle_format"] == BUNDLE_FORMAT
    assert m["schema_version"] == SCHEMA_VERSION
    assert m["content_version"] == 7
    assert m["counts"]["nodes"] == 1
    assert m["payload_sha256"] == payload_sha256(payload)
    assert m["created_at"] and m["generator"].startswith("lessons/")


def test_verify_accepts_good_bundle(populated):
    b = build_bundle(populated.export_seed(), content_version=1)
    manifest = verify_bundle(b)
    assert manifest.content_version == 1


def test_tampered_payload_fails_integrity(populated):
    b = build_bundle(populated.export_seed(), content_version=1)
    # Mutate a value WITHOUT updating the hash — the exact corruption/tamper case.
    b["payload"]["nodes"][0]["label"] = "Tampered Name"
    with pytest.raises(BundleError, match="integrity"):
        verify_bundle(b)


def test_hash_is_order_independent(populated):
    # Reordering keys/lists must NOT change the hash (canonical form), so a
    # re-serialized-but-identical bundle still verifies.
    payload = populated.export_seed()
    h1 = payload_sha256(payload)
    reordered = {k: payload[k] for k in reversed(list(payload))}
    assert payload_sha256(reordered) == h1


def test_newer_bundle_format_refused(populated):
    b = build_bundle(populated.export_seed(), content_version=1)
    b["manifest"]["bundle_format"] = BUNDLE_FORMAT + 1
    # re-hash so it's ONLY the format that's wrong, not integrity
    with pytest.raises(BundleError, match="bundle_format"):
        verify_bundle(b)


def test_newer_schema_refused_unless_allowed(populated):
    b = build_bundle(populated.export_seed(), content_version=1)
    b["manifest"]["schema_version"] = SCHEMA_VERSION + 1
    with pytest.raises(BundleError, match="schema_version"):
        verify_bundle(b)
    # an explicit opt-in lets a forward-compatible reader proceed
    assert verify_bundle(b, allow_newer_schema=True).schema_version == SCHEMA_VERSION + 1


def test_malformed_bundle_refused():
    with pytest.raises(BundleError, match="not a bundle"):
        verify_bundle({"nope": 1})
    with pytest.raises(BundleError, match="not valid JSON"):
        load_bundle_text("{not json")


def test_load_bundle_text_roundtrip(populated):
    b = build_bundle(populated.export_seed(), content_version=3)
    manifest, payload = load_bundle_text(json.dumps(b))
    assert manifest.content_version == 3
    assert payload["nodes"][0]["label"] == "John D. Rockefeller"


def test_bundle_payload_imports_into_empty_store(populated):
    # The payload must still round-trip through the existing seed importer — the
    # reader app loads a bundle by importing its payload.
    b = build_bundle(populated.export_seed(), content_version=1)
    fresh = Store.memory()
    try:
        counts = fresh.import_seed(b["payload"])
        assert counts["nodes"] == 1
        assert counts["mentions"] == 1
        node = fresh.conn.execute("SELECT label FROM nodes").fetchone()
        assert node["label"] == "John D. Rockefeller"
    finally:
        fresh.close()


def test_version_info_is_small_and_sufficient(populated):
    b = build_bundle(populated.export_seed(), content_version=9)
    vi = version_info(verify_bundle(b))
    assert vi["content_version"] == 9
    assert vi["schema_version"] == SCHEMA_VERSION
    assert "payload" not in vi and "nodes" not in vi  # no heavy payload, just the decision data
    assert vi["counts"]["nodes"] == 1


def test_store_content_version_bumps():
    s = Store.memory()
    try:
        assert s.content_version() == 0
        assert s.bump_content_version() == 1
        assert s.bump_content_version() == 2
        assert s.content_version() == 2
    finally:
        s.close()
