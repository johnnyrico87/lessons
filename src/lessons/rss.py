"""RSS podcast ingest source (requirements §1; design §6 — alternative source).

The preferred, defensible source: a podcast's public RSS feed. Audio enclosures
are published for exactly this (podcast clients fetch them), so this path is
both robust (no YouTube IP-blocking) and cleaner than scraping YouTube. No
publisher transcripts exist for the target shows, so audio is transcribed
locally with Whisper and then DELETED (retention model — the graph is the
product, the audio is a disposable build input).

Feed parsing uses the stdlib (xml.etree) — no new dependency. Audio download +
Whisper transcription go through the same lazily-imported heavy deps as the
YouTube provider, and the transcription is delegated to a reusable transcriber
so the orchestration stays testable without the model.
"""

from __future__ import annotations

import os
import tempfile
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass

from lessons.ingest import ChannelMeta, TranscriptResult, TransientIngestError, VideoMeta

# Canonical RSS feeds for the three configured shows.
KNOWN_FEEDS = {
    "founders": "https://feeds.megaphone.fm/DSLLC6297708582",
    "david-senra": "https://feeds.megaphone.fm/david-senra",
    "how-to-take-over-the-world": "https://feeds.megaphone.fm/ATHLLC5883700320",
}

_ITUNES_NS = "{http://www.itunes.com/dtds/podcast-1.0.dtd}"


@dataclass
class RssEpisode:
    guid: str
    title: str
    published: str
    duration_s: int
    audio_url: str


def parse_feed(xml_bytes: bytes) -> tuple[str, list[RssEpisode]]:
    """Parse an RSS feed into (show_title, [RssEpisode]). Pure + stdlib-only."""
    root = ET.fromstring(xml_bytes)
    channel = root.find("channel")
    if channel is None:
        return "", []
    show_title = (channel.findtext("title") or "").strip()
    episodes: list[RssEpisode] = []
    for item in channel.findall("item"):
        enclosure = item.find("enclosure")
        audio_url = enclosure.get("url") if enclosure is not None else None
        if not audio_url:
            continue
        guid = (item.findtext("guid") or audio_url).strip()
        title = (item.findtext("title") or "").strip()
        pub = (item.findtext("pubDate") or "").strip()
        dur = _parse_duration(item.findtext(f"{_ITUNES_NS}duration"))
        episodes.append(
            RssEpisode(guid=guid, title=title, published=pub, duration_s=dur, audio_url=audio_url)
        )
    return show_title, episodes


def _parse_duration(raw: str | None) -> int:
    if not raw:
        return 0
    raw = raw.strip()
    if raw.isdigit():
        return int(raw)
    parts = raw.split(":")
    try:
        parts = [int(p) for p in parts]
    except ValueError:
        return 0
    s = 0
    for p in parts:
        s = s * 60 + p
    return s


def _slug_to_id(feed_slug: str, guid: str) -> str:
    # stable per-episode video-table id, namespaced by feed
    import hashlib

    h = hashlib.sha1(f"{feed_slug}:{guid}".encode()).hexdigest()[:16]
    return f"rss_{h}"


class RssProvider:
    """A YouTubeProvider-shaped source backed by a podcast RSS feed.

    Construct per feed. `channel_meta`/`list_videos` ignore the handle/url the
    Ingester passes and use this feed, so the existing ingest orchestration runs
    unchanged. Captions are never available (feeds carry audio) -> fetch_transcript
    returns None, so the Ingester falls through to whisper_transcribe.
    """

    def __init__(self, feed_url: str, http_get=None, backoff_base_s: float = 1.0,
                 whisper_model: str = "base", whisper_compute_type: str = "int8"):
        self.feed_url = feed_url
        self.feed_slug = feed_url.rstrip("/").rsplit("/", 1)[-1]
        self._http_get = http_get           # injectable for tests
        self.backoff_base_s = backoff_base_s
        self.whisper_model = whisper_model
        self.whisper_compute_type = whisper_compute_type
        self._whisper = None
        self._episodes: list[RssEpisode] | None = None
        self._title = ""

    # --- source protocol --------------------------------------------------
    def channel_meta(self, handle: str, url: str) -> ChannelMeta:
        self._load()
        return ChannelMeta(id=self.feed_slug, handle=handle or self.feed_slug,
                           url=self.feed_url, name=self._title or self.feed_slug)

    def list_videos(self, channel: ChannelMeta) -> list[VideoMeta]:
        self._load()
        out: list[VideoMeta] = []
        for ep in self._episodes or []:
            vid = _slug_to_id(self.feed_slug, ep.guid)
            out.append(
                VideoMeta(
                    id=vid,
                    channel_id=channel.id,
                    channel_handle=channel.handle,
                    title=ep.title,
                    published_at=ep.published,
                    duration_s=ep.duration_s,
                    # the enclosure URL; deep-link is the show, not a timestamped YT link
                    url=ep.audio_url,
                )
            )
        return out

    def fetch_transcript(self, video: VideoMeta) -> TranscriptResult | None:
        # RSS feeds carry audio, not captions -> always fall through to whisper.
        return None

    def whisper_transcribe(self, video: VideoMeta) -> TranscriptResult:
        audio_path = self._download_audio(video.url)
        try:
            segments = self._transcribe(audio_path)
        finally:
            try:
                os.remove(audio_path)  # retention: audio is a disposable input
            except OSError:
                pass
        from lessons.providers import whisper_segments_to_result

        return whisper_segments_to_result(segments)

    # --- internals --------------------------------------------------------
    def _load(self) -> None:
        if self._episodes is not None:
            return
        raw = self._get(self.feed_url)
        self._title, self._episodes = parse_feed(raw)

    def _get(self, url: str) -> bytes:
        if self._http_get is not None:
            return self._http_get(url)
        try:
            import httpx  # type: ignore
        except ImportError as e:
            raise RuntimeError("httpx not installed; pip install -e '.[dev]'") from e
        for attempt in range(3):
            try:
                r = httpx.get(url, timeout=30, follow_redirects=True,
                              headers={"User-Agent": "lessons/0.0"})
                r.raise_for_status()
                return r.content
            except Exception as e:
                if attempt == 2:
                    raise TransientIngestError(str(e)) from e
                time.sleep(self.backoff_base_s * (2**attempt))
        return b""

    def _download_audio(self, audio_url: str) -> str:
        try:
            import httpx  # type: ignore
        except ImportError as e:
            raise RuntimeError("httpx not installed; pip install -e '.[dev]'") from e
        tmpdir = tempfile.mkdtemp(prefix="lessons-rss-audio-")
        path = os.path.join(tmpdir, "episode.mp3")
        for attempt in range(3):
            try:
                with httpx.stream("GET", audio_url, timeout=120, follow_redirects=True,
                                  headers={"User-Agent": "lessons/0.0"}) as r:
                    r.raise_for_status()
                    with open(path, "wb") as f:
                        for chunk in r.iter_bytes(chunk_size=65536):
                            f.write(chunk)
                return path
            except Exception as e:
                if attempt == 2:
                    raise TransientIngestError(str(e)) from e
                time.sleep(self.backoff_base_s * (2**attempt))
        raise TransientIngestError("audio download failed")

    def _transcribe(self, audio_path: str) -> list[dict]:
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            raise RuntimeError("faster-whisper not installed; pip install -e '.[ingest]'") from e
        if self._whisper is None:
            self._whisper = WhisperModel(self.whisper_model, compute_type=self.whisper_compute_type)
        segments, _info = self._whisper.transcribe(audio_path)
        return [{"start": seg.start, "text": seg.text} for seg in segments]
