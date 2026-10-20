"""First-run seeding (design §9a).

The repo ships the maintainer's curated graph — "their notes": short quotes +
timestamps + the connections they drew — as a diff-friendly JSON file under
`seed/`. On first launch, when the user's data dir has no graph yet, we rebuild
a working DB from that seed so a fresh clone is populated, not empty.

User data lives in the (git-ignored) data dir; the seed is only the starting
point. Re-importing is idempotent (INSERT OR IGNORE), so seeding a non-empty DB
is a no-op in practice.
"""

from __future__ import annotations

import json
from pathlib import Path

from lessons.store import Store

# seed/lessons-seed.json lives at the repo root: src/lessons/seed.py -> ../../..
_REPO_ROOT = Path(__file__).resolve().parents[2]
SEED_PATH = _REPO_ROOT / "seed" / "lessons-seed.json"


def load_seed(path: Path | None = None) -> dict | None:
    """Read the shipped seed JSON, or None when it isn't present."""
    p = path or SEED_PATH
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def seed_if_empty(store: Store, path: Path | None = None) -> dict | None:
    """If the store has no nodes, import the shipped seed. Returns the inserted
    counts when seeding happened, else None (already populated or no seed)."""
    if not store.is_empty():
        return None
    seed = load_seed(path)
    if not seed:
        return None
    return store.import_seed(seed)
