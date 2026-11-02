"""Versioned graph bundles — the content-delivery format for the downloadable
reader app (product decision: users consume a pre-run graph; all processing
happens on the maintainer's machine).

A bundle wraps the SAME payload the seed mechanism already serializes
(channels / videos / nodes / edges / mentions / bios — short cited quotes, never
audio or transcripts) in a manifest that makes it trustworthy to ship and
update:

    {
      "manifest": {
        "bundle_format": 1,          # this envelope's version
        "schema_version": 4,         # the DB schema the payload was exported at
        "content_version": 12,       # monotonic; the app compares this to decide
                                     #   "a newer graph is available"
        "created_at": "2026-11-02T03:00:00+00:00",
        "generator": "lessons/0.0.1",
        "counts": {"nodes": 14, "edges": 6, ...},
        "payload_sha256": "<hex>"    # integrity over the canonical payload
      },
      "payload": { ...export_seed() shape... }
    }

The hash lets the reader REFUSE a corrupted or tampered download rather than
importing a broken graph. `content_version` is what turns "download once" into
"stay up to date".
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any

from lessons import __version__
from lessons.schema import SCHEMA_VERSION

# The envelope format version. Bump only when the manifest SHAPE changes, not
# when content changes (that's content_version) or the DB schema changes
# (schema_version).
BUNDLE_FORMAT = 1


class BundleError(Exception):
    """A bundle failed to verify (bad shape, version mismatch, or integrity)."""


def _canonical_payload_bytes(payload: dict[str, Any]) -> bytes:
    """Deterministic bytes for the payload, so the SHA-256 is stable across
    machines and Python runs: sorted keys, no insignificant whitespace, UTF-8.
    Any reordering of keys or dicts yields the SAME hash; any value change yields
    a different one — which is exactly the integrity property we want."""
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    ).encode("utf-8")


def payload_sha256(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_canonical_payload_bytes(payload)).hexdigest()


@dataclass
class BundleManifest:
    bundle_format: int
    schema_version: int
    content_version: int
    created_at: str
    generator: str
    counts: dict[str, int]
    payload_sha256: str


def build_bundle(payload: dict[str, Any], content_version: int) -> dict[str, Any]:
    """Wrap an export_seed()-shaped payload in a versioned, hashed manifest.

    `content_version` is the maintainer's monotonic publish counter — bump it on
    every publish so the reader app can tell a newer graph apart from the one it
    already has. The payload's own `seed_version` (if present) is left intact so
    the same file still round-trips through the seed importer."""
    counts = {k: len(v) for k, v in payload.items() if isinstance(v, list)}
    manifest = BundleManifest(
        bundle_format=BUNDLE_FORMAT,
        schema_version=SCHEMA_VERSION,
        content_version=int(content_version),
        created_at=datetime.now(UTC).isoformat(),
        generator=f"lessons/{__version__}",
        counts=counts,
        payload_sha256=payload_sha256(payload),
    )
    return {"manifest": asdict(manifest), "payload": payload}


def verify_bundle(bundle: dict[str, Any], *, allow_newer_schema: bool = False) -> BundleManifest:
    """Validate a loaded bundle and return its manifest, or raise BundleError.

    Checks, in order: envelope shape, a bundle_format this build understands, the
    schema_version (a bundle newer than this build's schema is refused unless
    `allow_newer_schema`, because the reader can't trust columns it doesn't know),
    and finally the payload integrity hash. Integrity is checked LAST so a
    corrupted file still gets the specific 'integrity' error, not a vague shape
    error."""
    if not isinstance(bundle, dict) or "manifest" not in bundle or "payload" not in bundle:
        raise BundleError("not a bundle: expected {'manifest', 'payload'}")
    m = bundle["manifest"]
    payload = bundle["payload"]
    if not isinstance(m, dict) or not isinstance(payload, dict):
        raise BundleError("malformed bundle: manifest/payload must be objects")

    try:
        manifest = BundleManifest(
            bundle_format=int(m["bundle_format"]),
            schema_version=int(m["schema_version"]),
            content_version=int(m["content_version"]),
            created_at=str(m["created_at"]),
            generator=str(m.get("generator", "unknown")),
            counts=dict(m.get("counts", {})),
            payload_sha256=str(m["payload_sha256"]),
        )
    except (KeyError, TypeError, ValueError) as e:
        raise BundleError(f"malformed manifest: {e}") from e

    if manifest.bundle_format > BUNDLE_FORMAT:
        raise BundleError(
            f"bundle_format {manifest.bundle_format} is newer than this build "
            f"supports ({BUNDLE_FORMAT}); update the app"
        )
    if manifest.schema_version > SCHEMA_VERSION and not allow_newer_schema:
        raise BundleError(
            f"bundle schema_version {manifest.schema_version} is newer than this "
            f"build's schema ({SCHEMA_VERSION}); update the app"
        )

    actual = payload_sha256(payload)
    if actual != manifest.payload_sha256:
        raise BundleError(
            "integrity check failed: payload_sha256 does not match "
            f"(manifest {manifest.payload_sha256[:12]}…, actual {actual[:12]}…)"
        )
    return manifest


def load_bundle_text(text: str, *, allow_newer_schema: bool = False) -> tuple[BundleManifest, dict]:
    """Parse + verify a bundle from its JSON text. Returns (manifest, payload).
    Raises BundleError on any problem so callers have one error type to handle."""
    try:
        bundle = json.loads(text)
    except json.JSONDecodeError as e:
        raise BundleError(f"not valid JSON: {e}") from e
    manifest = verify_bundle(bundle, allow_newer_schema=allow_newer_schema)
    return manifest, bundle["payload"]


def version_info(manifest: BundleManifest) -> dict[str, Any]:
    """The small record a publish step writes to a `version.json` the reader app
    polls: enough to decide 'is there a newer graph?' without downloading the
    whole bundle."""
    return {
        "content_version": manifest.content_version,
        "schema_version": manifest.schema_version,
        "bundle_format": manifest.bundle_format,
        "created_at": manifest.created_at,
        "counts": manifest.counts,
    }
