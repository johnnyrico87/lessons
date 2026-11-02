"""PyInstaller launcher for the read-only reader app.

Double-clicking the packaged app runs THIS: it resolves the bundled resources
(static UI + baked graph bundle) whether running from source or from inside a
PyInstaller onefile/onedir build, seeds the user's data dir from the baked
bundle on first run, starts uvicorn on a free loopback port, and opens the
browser at it.

Reader-only: no ingest/extract/Ollama/Whisper. The graph arrives as the baked
bundle and (if an update channel is configured) via the background auto-update
already wired into `serve` startup.
"""

from __future__ import annotations

import os
import socket
import sys
import threading
import time
import webbrowser
from pathlib import Path


def _resource_root() -> Path:
    """Where bundled data lives. Under PyInstaller that's sys._MEIPASS; from
    source it's this file's directory's parent (the repo)."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
    return Path(__file__).resolve().parents[1]


def _free_port() -> int:
    """Bind :0 on loopback to let the OS pick a free port, then release it."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _baked_bundle_path() -> Path | None:
    """The graph bundle shipped inside the app (packaging/graph-bundle.json),
    or None when absent (the app still runs, just empty until an update)."""
    p = _resource_root() / "graph-bundle.json"
    return p if p.exists() else None


def main() -> None:
    from lessons.cli import _db_path
    from lessons.reader import install_baked_bundle
    from lessons.seed import seed_if_empty
    from lessons.store import Store
    from lessons.web import build_app

    db_path = _db_path()
    store = Store(db_path)

    # First-run population: prefer the baked BUNDLE (versioned, integrity-checked);
    # fall back to the repo seed when running from source without a baked bundle.
    if store.is_empty():
        baked = _baked_bundle_path()
        if baked is not None:
            install_baked_bundle(store, baked)
        else:
            seed_if_empty(store)

    app = build_app(store)

    import uvicorn

    port = _free_port()
    url = f"http://127.0.0.1:{port}"

    def _open_when_up() -> None:
        # Poll the health endpoint, then open the browser once it answers.
        import urllib.request

        for _ in range(100):
            try:
                with urllib.request.urlopen(f"{url}/api/health", timeout=0.5) as r:
                    if r.status == 200:
                        break
            except Exception:
                time.sleep(0.1)
        webbrowser.open(url)

    threading.Thread(target=_open_when_up, daemon=True).start()
    print(f"lessons reader — opening {url}")  # noqa: T201
    # Bind loopback only, never 0.0.0.0.
    uvicorn.run(app, host="127.0.0.1", port=port, log_level="warning")


if __name__ == "__main__":
    # Allow `LESSONS_DATA_DIR` override to still work in the packaged app.
    os.environ.setdefault("LESSONS_APP", "1")
    main()
