"""Reader-side bundle loading + auto-update (the downloadable app's consumer half).

The maintainer publishes a versioned graph bundle (see lessons.bundle) and a
tiny version.json. The reader app:

  1. ships WITH a baked-in bundle, so it is populated and works OFFLINE on first
     run (install_baked_bundle);
  2. remembers which content_version it has installed (in `meta`);
  3. when asked, checks a remote version.json and, if a newer content_version is
     available, downloads + verifies + swaps the bundle in (check_and_update).

Every network step is best-effort and NON-FATAL: an offline or failing update
leaves the installed graph exactly as it was. The app never ends up with a
broken or half-applied graph — replace_graph is transactional and the bundle's
integrity hash is checked before anything is written.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from pathlib import Path

from lessons.bundle import BundleError, load_bundle_text
from lessons.store import Store

_INSTALLED_KEY = "installed_content_version"

# Env seam for the reader's update source (mirrors LESSONS_DATA_DIR). Unset =>
# no background check runs, which is the correct default for a dev checkout.
ENV_VERSION_URL = "LESSONS_UPDATE_VERSION_URL"
ENV_BUNDLE_URL = "LESSONS_UPDATE_BUNDLE_URL"


def installed_content_version(store: Store) -> int:
    """The content_version currently loaded in this store (0 if none)."""
    row = store.conn.execute(
        "SELECT value FROM meta WHERE key=?", (_INSTALLED_KEY,)
    ).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def _record_installed(store: Store, content_version: int) -> None:
    store.conn.execute(
        "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
        (_INSTALLED_KEY, str(content_version)),
    )
    store.conn.commit()


@dataclass
class UpdateOutcome:
    status: str          # 'seeded' | 'updated' | 'up_to_date' | 'offline' | 'error'
    from_version: int
    to_version: int
    detail: str = ""

    @property
    def changed(self) -> bool:
        return self.status in ("seeded", "updated")


def apply_bundle_text(store: Store, text: str, *, first_run: bool) -> UpdateOutcome:
    """Verify a bundle's JSON text and load it. On first_run (empty store) this
    SEEDS (additive import); otherwise it REPLACES the graph transactionally.
    Records the installed content_version only after a successful load."""
    prev = installed_content_version(store)
    try:
        manifest, payload = load_bundle_text(text)
    except BundleError as e:
        return UpdateOutcome("error", prev, prev, f"bundle rejected: {e}")

    if first_run or store.is_empty():
        store.import_seed(payload)
        _record_installed(store, manifest.content_version)
        return UpdateOutcome("seeded", 0, manifest.content_version)

    if manifest.content_version <= prev:
        return UpdateOutcome("up_to_date", prev, prev)

    store.replace_graph(payload)
    _record_installed(store, manifest.content_version)
    return UpdateOutcome("updated", prev, manifest.content_version)


def install_baked_bundle(store: Store, bundle_path: Path) -> UpdateOutcome:
    """First-run seeding from the bundle shipped inside the app. No network.
    A missing or unreadable baked bundle is non-fatal (the app simply starts
    empty and can still update later)."""
    if not bundle_path.exists():
        return UpdateOutcome("error", 0, 0, f"baked bundle not found at {bundle_path}")
    try:
        text = bundle_path.read_text(encoding="utf-8")
    except OSError as e:
        return UpdateOutcome("error", 0, 0, f"could not read baked bundle: {e}")
    return apply_bundle_text(store, text, first_run=True)


def check_and_update(
    store: Store,
    version_url: str,
    bundle_url: str,
    *,
    timeout_s: float = 15.0,
) -> UpdateOutcome:
    """Poll version.json; if its content_version beats the installed one, fetch
    and apply the bundle. Returns 'up_to_date', 'updated', 'offline', or 'error'.

    Offline/network failure is NON-FATAL: the installed graph is untouched and
    the outcome is 'offline' so the caller can stay quiet. Integrity + version
    guards run before any write (via apply_bundle_text)."""
    current = installed_content_version(store)
    try:
        import httpx  # type: ignore
    except ImportError:
        return UpdateOutcome("error", current, current, "httpx not installed")

    try:
        with httpx.Client(timeout=timeout_s, follow_redirects=True) as client:
            vr = client.get(version_url)
            if vr.status_code != 200:
                return UpdateOutcome(
                    "offline", current, current, f"version.json HTTP {vr.status_code}"
                )
            remote_cv = int(vr.json().get("content_version", 0))
            if remote_cv <= current:
                return UpdateOutcome("up_to_date", current, current)
            br = client.get(bundle_url)
            if br.status_code != 200:
                return UpdateOutcome("offline", current, current, f"bundle HTTP {br.status_code}")
            text = br.text
    except Exception as e:  # DNS, timeout, refused, bad JSON — all non-fatal
        return UpdateOutcome("offline", current, current, f"update check failed: {e}")

    return apply_bundle_text(store, text, first_run=False)


def resolve_update_urls(
    version_url: str | None = None, bundle_url: str | None = None
) -> tuple[str, str] | None:
    """Resolve the (version_url, bundle_url) pair from explicit args first, then
    the env seam. Returns None when neither source provides BOTH — the signal to
    skip the update check entirely (the correct default for a dev checkout or an
    app shipped without an update channel)."""
    v = version_url or os.environ.get(ENV_VERSION_URL, "")
    b = bundle_url or os.environ.get(ENV_BUNDLE_URL, "")
    if v and b:
        return v, b
    return None


def start_background_update(
    db_path: str | Path,
    version_url: str | None = None,
    bundle_url: str | None = None,
    *,
    on_done=None,
) -> threading.Thread | None:
    """Kick off a best-effort update check in a daemon thread and return it
    (None when no update channel is configured, so nothing was started).

    Non-blocking: the server starts immediately regardless. Non-fatal: any
    failure is swallowed into an UpdateOutcome. The thread opens its OWN Store
    connection on `db_path` (never the server's shared connection) so a graph
    swap can't race in-flight request reads, and is a daemon so it never keeps
    the process alive. `on_done(outcome)` is an optional callback (used by tests;
    the CLI passes a logger)."""
    urls = resolve_update_urls(version_url, bundle_url)
    if urls is None:
        return None
    v_url, b_url = urls

    def _run() -> None:
        outcome = None
        try:
            s = Store(db_path)
            try:
                outcome = check_and_update(s, v_url, b_url)
            finally:
                s.close()
        except Exception as e:  # a background check must NEVER crash the server
            outcome = UpdateOutcome("error", 0, 0, f"background update crashed: {e}")
        if on_done is not None:
            try:
                on_done(outcome)
            except Exception:
                pass

    t = threading.Thread(target=_run, name="lessons-update", daemon=True)
    t.start()
    return t
