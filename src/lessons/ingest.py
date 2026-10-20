"""Pass A — ingest (requirements §1, design §6).

Downloads transcripts + metadata for the configured channels into the SQLite
store. No LLM. Resumable, de-duplicating, rate-limit aware, with a `--probe`
dry-run that measures caption coverage + per-video latency so the full-backfill
time estimate is MEASURED, not guessed.

Network boundary: all YouTube access goes through the small provider protocol
(`YouTubeProvider`). The orchestration here is pure and testable against a fake
provider — tests never hit the network, and the heavy deps (yt-dlp,
youtube-transcript-api, faster-whisper) are optional extras imported lazily
only inside the real provider.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol

from lessons.store import Mention, Store  # noqa: F401  (Mention re-exported for callers)

# The three configured channels (requirements: sources v1).
DEFAULT_CHANNELS: list[dict[str, str]] = [
    {"handle": "@DavidSenra", "url": "https://www.youtube.com/@DavidSenra/videos"},
    {"handle": "@founderspodcast1", "url": "https://www.youtube.com/@founderspodcast1/videos"},
    {"handle": "@TakeOverPod", "url": "https://www.youtube.com/@TakeOverPod/videos"},
]


@dataclass
class VideoMeta:
    id: str
    channel_id: str
    channel_handle: str
    title: str
    published_at: str
    duration_s: int
    url: str


@dataclass
class TranscriptResult:
    text: str
    source: str  # 'captions' | 'whisper'
    lines: list[dict] = field(default_factory=list)  # [{start_s, text}, ...]


@dataclass
class ChannelMeta:
    id: str
    handle: str
    url: str
    name: str


class YouTubeProvider(Protocol):
    """The only surface that touches the network. Mocked in tests."""

    def channel_meta(self, handle: str, url: str) -> ChannelMeta: ...
    def list_videos(self, channel: ChannelMeta) -> list[VideoMeta]: ...
    def fetch_transcript(self, video: VideoMeta) -> TranscriptResult | None: ...
    def whisper_transcribe(self, video: VideoMeta) -> TranscriptResult: ...


# --- de-dup helpers --------------------------------------------------------

def _norm_title(title: str) -> str:
    """Normalize a title for cross-channel de-dup: lowercase, strip episode
    numbers / punctuation / whitespace runs."""
    t = title.lower()
    t = re.sub(r"#\s*\d+", " ", t)          # drop "#412"
    t = re.sub(r"\bep(isode)?\.?\s*\d+", " ", t)
    t = re.sub(r"[^a-z0-9 ]+", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _dup_key(v: VideoMeta) -> tuple[str, int]:
    """Key for near-duplicate detection: normalized title + a duration bucket
    (10s granularity tolerates minor re-encodes)."""
    return (_norm_title(v.title), round((v.duration_s or 0) / 10))


# --- ingest orchestration --------------------------------------------------

@dataclass
class ProbeReport:
    sampled: int
    with_captions: int
    whisper_needed: int
    median_latency_s: float

    @property
    def caption_coverage_pct(self) -> float:
        return 100.0 * self.with_captions / self.sampled if self.sampled else 0.0

    def render(self) -> str:
        return (
            f"probe: sampled {self.sampled} video(s) | "
            f"caption coverage {self.caption_coverage_pct:.0f}% "
            f"({self.with_captions} captioned, {self.whisper_needed} need whisper) | "
            f"median per-video latency {self.median_latency_s:.2f}s"
        )


@dataclass
class IngestResult:
    channels: int = 0
    videos_seen: int = 0
    videos_ingested: int = 0
    videos_skipped_existing: int = 0
    videos_deduped: int = 0
    whisper_used: int = 0


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


class Ingester:
    def __init__(self, store: Store, provider: YouTubeProvider):
        self.store = store
        self.provider = provider

    def _already_ingested(self, video_id: str) -> bool:
        row = self.store.conn.execute(
            "SELECT ingested_at FROM videos WHERE id=?", (video_id,)
        ).fetchone()
        return bool(row and row["ingested_at"])

    def _resolve_transcript(self, v: VideoMeta) -> TranscriptResult:
        """Captions first; fall back to whisper when absent (R§1.2–1.3)."""
        tr = self.provider.fetch_transcript(v)
        if tr is not None:
            return tr
        return self.provider.whisper_transcribe(v)

    def ingest(
        self,
        channels: list[dict[str, str]] | None = None,
        probe: int = 0,
        max_retries: int = 3,
    ) -> IngestResult | ProbeReport:
        channels = channels or DEFAULT_CHANNELS
        if probe > 0:
            return self._probe(channels, probe)

        result = IngestResult()
        seen_dupes: set[tuple[str, int]] = set()

        for ch in channels:
            cm = self.provider.channel_meta(ch["handle"], ch["url"])
            self.store.add_channel(cm.id, cm.handle, cm.url, cm.name)
            result.channels += 1

            for v in self.provider.list_videos(cm):
                result.videos_seen += 1

                # de-dup across channels (R§1.6): by id first, then title+duration
                if self._already_ingested(v.id):
                    result.videos_skipped_existing += 1
                    continue
                key = _dup_key(v)
                if key in seen_dupes:
                    result.videos_deduped += 1
                    continue
                seen_dupes.add(key)

                tr = self._with_retries(
                    lambda v=v: self._resolve_transcript(v), max_retries
                )
                self.store.add_video(
                    v.id, v.channel_id, v.title, v.published_at, v.duration_s, v.url,
                    transcript_path=None, ingested_at=_now_iso(),
                )
                self.store.add_transcript(v.id, tr.text, tr.source, lines=tr.lines)
                if tr.source == "whisper":
                    result.whisper_used += 1
                result.videos_ingested += 1

        return result

    def _probe(self, channels: list[dict[str, str]], n: int) -> ProbeReport:
        """Sample up to n videos/channel; measure caption coverage + latency.
        Read-only: does not persist (R§1.8, D§6)."""
        latencies: list[float] = []
        with_captions = 0
        whisper_needed = 0
        sampled = 0

        for ch in channels:
            cm = self.provider.channel_meta(ch["handle"], ch["url"])
            for v in self.provider.list_videos(cm)[:n]:
                sampled += 1
                t0 = time.perf_counter()
                tr = self.provider.fetch_transcript(v)
                latencies.append(time.perf_counter() - t0)
                if tr is not None and tr.source == "captions":
                    with_captions += 1
                else:
                    whisper_needed += 1

        latencies.sort()
        median = latencies[len(latencies) // 2] if latencies else 0.0
        return ProbeReport(sampled, with_captions, whisper_needed, median)

    @staticmethod
    def _with_retries(fn, max_retries: int, base_delay: float = 0.0):
        """Exponential backoff + jitter on transient failures (R§1.7).

        base_delay=0.0 keeps tests instant; the real provider sets a real delay.
        """
        last: Exception | None = None
        for attempt in range(max_retries):
            try:
                return fn()
            except TransientIngestError as e:  # only retry transient errors
                last = e
                if base_delay:
                    time.sleep(base_delay * (2**attempt))
        raise last if last else RuntimeError("retry loop exhausted")


class TransientIngestError(Exception):
    """A retryable ingest failure (e.g. rate-limit / network blip)."""
