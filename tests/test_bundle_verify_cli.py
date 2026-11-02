"""The `lessons bundle-verify` CLI command — the release gate used by the
release-bundle workflow. A valid bundle passes and emits a consistent
version.json; a tampered, missing, or malformed bundle exits non-zero so CI
refuses to publish it.
"""

from __future__ import annotations

import json

from typer.testing import CliRunner

from lessons.bundle import build_bundle
from lessons.cli import app
from lessons.store import Mention, Store

runner = CliRunner()


def _write_bundle(path, labels, cv):
    s = Store.memory()
    s.add_channel("C1", "@h", "https://x", "Host")
    s.add_video("v1", "C1", "ep", "2024-01-01", 600, "https://youtu.be/v1")
    for i, lbl in enumerate(labels):
        s.add_podcast_node("person", lbl, mentions=[Mention("v1", i + 1, f"q{i}")])
    bundle = build_bundle(s.export_seed(), content_version=cv)
    s.close()
    path.write_text(json.dumps(bundle), encoding="utf-8")
    return bundle


def test_verify_valid_bundle_passes_and_emits_version(tmp_path):
    b = tmp_path / "graph-bundle.json"
    _write_bundle(b, ["Rockefeller", "Carnegie"], cv=4)
    vout = tmp_path / "version.json"
    result = runner.invoke(app, ["bundle-verify", str(b), "--emit-version", str(vout)])
    assert result.exit_code == 0
    assert "bundle OK" in result.output and "content v4" in result.output
    vj = json.loads(vout.read_text())
    assert vj["content_version"] == 4
    assert vj["counts"]["nodes"] == 2
    assert "payload" not in vj  # version.json stays small


def test_verify_tampered_bundle_fails(tmp_path):
    b = tmp_path / "graph-bundle.json"
    bundle = _write_bundle(b, ["A"], cv=1)
    bundle["payload"]["nodes"][0]["label"] = "Tampered"  # breaks the hash
    b.write_text(json.dumps(bundle), encoding="utf-8")
    result = runner.invoke(app, ["bundle-verify", str(b)])
    assert result.exit_code == 1
    assert "REJECTED" in result.output


def test_verify_missing_bundle_fails(tmp_path):
    result = runner.invoke(app, ["bundle-verify", str(tmp_path / "nope.json")])
    assert result.exit_code == 2
    assert "not found" in result.output


def test_verify_malformed_json_fails(tmp_path):
    b = tmp_path / "graph-bundle.json"
    b.write_text("{not json", encoding="utf-8")
    result = runner.invoke(app, ["bundle-verify", str(b)])
    assert result.exit_code == 1
    assert "REJECTED" in result.output
