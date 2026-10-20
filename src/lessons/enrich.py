"""Pass B2 — enrich (requirements §3, design §8).

Attaches an external biography to each `person` node from Wikipedia/Wikidata.
Kept strictly separate from podcast-sourced knowledge: bios store `origin`
context on the `bios` table (never as podcast nodes), carry source + url +
license, and never feed situation-first results as if a podcaster said them.

The network boundary is the `BioProvider` protocol, mocked in tests — the
orchestration (which persons, resumability, licensing enforcement) needs no
network. bio text is fetched into the LOCAL db and never committed (R§3.4).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from lessons.store import Store


@dataclass
class Bio:
    source: str                 # "Wikipedia" / "Wikidata"
    source_url: str
    license: str                # prose license, e.g. "CC BY-SA 4.0"
    title: str | None = None
    summary: str | None = None
    image_url: str | None = None
    image_license: str | None = None   # images have their OWN license


class BioProvider(Protocol):
    """Fetches a bio for a person label. Returns None when nothing is found."""

    def fetch_bio(self, person_label: str) -> Bio | None: ...


@dataclass
class EnrichResult:
    people_total: int = 0
    enriched: int = 0
    skipped_existing: int = 0
    not_found: int = 0


class Enricher:
    def __init__(self, store: Store, provider: BioProvider, refresh: bool = False):
        self.store = store
        self.provider = provider
        self.refresh = refresh

    def enrich(self) -> EnrichResult:
        result = EnrichResult()
        people = self.store.conn.execute(
            "SELECT id, label FROM nodes WHERE type='person'"
        ).fetchall()
        result.people_total = len(people)

        for p in people:
            if not self.refresh and self.store.bio_for(p["id"]) is not None:
                result.skipped_existing += 1
                continue
            bio = self.provider.fetch_bio(p["label"])
            if bio is None:
                result.not_found += 1
                continue
            if not bio.license:
                # licensing is non-negotiable; skip rather than store unlicensed
                result.not_found += 1
                continue
            self.store.add_bio(
                person_node_id=p["id"],
                source=bio.source,
                source_url=bio.source_url,
                license=bio.license,
                title=bio.title,
                summary=bio.summary,
                image_url=bio.image_url,
                image_license=bio.image_license,
                fetched_at=datetime.now(UTC).isoformat(),
            )
            result.enriched += 1

        return result
