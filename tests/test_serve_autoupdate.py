"""Background update launcher wired into serve startup.

Proves: no update channel => nothing starts; URLs resolve from args and env;
and a configured channel actually fetches + applies a newer bundle over real
HTTP (a loopback http.server), non-blocking and in a daemon thread.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from lessons.bundle import build_bundle
from lessons.reader import (
    ENV_BUNDLE_URL,
    ENV_VERSION_URL,
    apply_bundle_text,
    installed_content_version,
    resolve_update_urls,
    start_background_update,
)
from lessons.store import Mention, Store


def _bundle(labels, cv):
    s = Store.memory()
    s.add_channel("C1", "@h", "https://x", "Host")
    s.add_video("v1", "C1", "ep", "2024-01-01", 600, "https://youtu.be/v1")
    for i, lbl in enumerate(labels):
        s.add_podcast_node("person", lbl, mentions=[Mention("v1", i + 1, f"q{i}")])
    p = s.export_seed()
    s.close()
    return build_bundle(p, content_version=cv)


# --- URL resolution --------------------------------------------------------

def test_no_channel_resolves_none(monkeypatch):
    monkeypatch.delenv(ENV_VERSION_URL, raising=False)
    monkeypatch.delenv(ENV_BUNDLE_URL, raising=False)
    assert resolve_update_urls() is None
    # one without the other is still None — both are required
    assert resolve_update_urls("http://v", None) is None


def test_env_resolves_urls(monkeypatch):
    monkeypatch.setenv(ENV_VERSION_URL, "http://host/version.json")
    monkeypatch.setenv(ENV_BUNDLE_URL, "http://host/graph-bundle.json")
    assert resolve_update_urls() == (
        "http://host/version.json",
        "http://host/graph-bundle.json",
    )
    # explicit args win over env
    assert resolve_update_urls("http://a", "http://b") == ("http://a", "http://b")


def test_no_channel_starts_no_thread(tmp_path, monkeypatch):
    monkeypatch.delenv(ENV_VERSION_URL, raising=False)
    monkeypatch.delenv(ENV_BUNDLE_URL, raising=False)
    db = tmp_path / "x.db"
    Store(db).close()
    assert start_background_update(db) is None  # nothing configured => nothing started


# --- real loopback HTTP update --------------------------------------------

class _Serve(BaseHTTPRequestHandler):
    files: dict = {}

    def do_GET(self):  # noqa: N802
        body = self.files.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        data = body.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):  # silence
        pass


def test_background_update_applies_newer_bundle(tmp_path):
    # Seed a reader DB at v1.
    db = tmp_path / "reader.db"
    s = Store(db)
    apply_bundle_text(s, json.dumps(_bundle(["A", "B"], 1)), first_run=True)
    assert installed_content_version(s) == 1
    s.close()

    # Serve v2 (B dropped, C added) over loopback.
    _Serve.files = {
        "/version.json": json.dumps({"content_version": 2}),
        "/graph-bundle.json": json.dumps(_bundle(["A", "C"], 2)),
    }
    httpd = HTTPServer(("127.0.0.1", 0), _Serve)
    port = httpd.server_address[1]
    srv = threading.Thread(target=httpd.serve_forever, daemon=True)
    srv.start()
    try:
        done = threading.Event()
        captured = {}

        def on_done(outcome):
            captured["outcome"] = outcome
            done.set()

        t = start_background_update(
            db,
            f"http://127.0.0.1:{port}/version.json",
            f"http://127.0.0.1:{port}/graph-bundle.json",
            on_done=on_done,
        )
        assert t is not None
        assert done.wait(timeout=10), "background update did not finish"
        out = captured["outcome"]
        assert out.status == "updated" and out.to_version == 2
    finally:
        httpd.shutdown()

    # Verify the swap actually landed on disk.
    s2 = Store(db)
    try:
        labels = {r["label"] for r in s2.conn.execute("SELECT label FROM nodes")}
        assert labels == {"A", "C"}             # B gone, C in
        assert installed_content_version(s2) == 2
    finally:
        s2.close()


def test_background_update_offline_is_nonfatal(tmp_path):
    # Point at a dead port — the check must fail quietly and leave v1 intact.
    db = tmp_path / "reader.db"
    s = Store(db)
    apply_bundle_text(s, json.dumps(_bundle(["A"], 1)), first_run=True)
    s.close()

    done = threading.Event()
    captured = {}
    start_background_update(
        db,
        "http://127.0.0.1:9/version.json",      # port 9 = discard, nothing listening
        "http://127.0.0.1:9/graph-bundle.json",
        on_done=lambda o: (captured.__setitem__("o", o), done.set()),
    )
    assert done.wait(timeout=10)
    assert captured["o"].status == "offline"
    s2 = Store(db)
    try:
        assert installed_content_version(s2) == 1  # untouched
    finally:
        s2.close()
