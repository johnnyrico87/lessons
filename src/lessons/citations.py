"""Structured citations (requirements §4a, design §7a).

Two shapes, assembled in the DAL so the UI never improvises attribution:

  * PodcastCitation  — what a podcaster said, with a timestamped video deep link.
                       `host` (the channel artist, e.g. David Senra) and `leaders`
                       (the subjects discussed, e.g. Rockefeller) are SEPARATE
                       fields and must never be conflated (§4a.6).
  * ExternalCitation — an external bio fact, carrying source + url + license.

The DAL refuses to emit a podcast unit without a resolvable PodcastCitation
(§4a.5), which ties to the schema provenance invariant.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


def youtube_video_id(video_url: str) -> str | None:
    """Extract a YouTube video id from a youtu.be/<id> or watch?v=<id> url.

    Returns None when the url is NOT a YouTube video (e.g. a podcast MP3
    tracking url). The YouTube HOST is the discriminator — we only parse an id
    out of a youtu.be / youtube.com url, never an arbitrary trailing path
    segment (that fabricated broken `youtu.be/<mp3-filename>` links). The id is
    sanity-checked as url-safe to reject obviously-wrong captures.
    """
    u = (video_url or "").strip()
    cand: str | None = None
    if "youtu.be/" in u:
        cand = u.split("youtu.be/", 1)[1].split("?", 1)[0].split("&", 1)[0].split("/", 1)[0]
    elif "youtube.com/embed/" in u:
        cand = u.split("/embed/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    elif "youtube.com" in u and "v=" in u:
        cand = u.split("v=", 1)[1].split("&", 1)[0]
    if cand and re.fullmatch(r"[A-Za-z0-9_-]{1,30}", cand):
        return cand
    return None


def youtube_deep_link(video_url: str, timestamp_s: int) -> str | None:
    """Timestamped YouTube link, or None when the url is not a YouTube video.

    Previously this fabricated `youtu.be/<trailing-segment>` for ANY url, which
    produced dead links for podcast MP3 feeds. Now it only returns a link when a
    real 11-char YouTube id is present.
    """
    vid = youtube_video_id(video_url)
    if not vid:
        return None
    return f"https://youtu.be/{vid}?t={int(timestamp_s)}"


def media_embed(video_url: str, timestamp_s: int) -> dict | None:
    """Describe how to embed this source at ``timestamp_s`` for the UI.

    Returns a format-aware descriptor the front end uses to pick a player:
      * {"type": "youtube", "src": "https://www.youtube.com/embed/<id>?start=<s>",
         "video_url": ..., "start_s": ...}
      * {"type": "audio",   "src": <direct media url>, "start_s": ...}
      * None when we cannot resolve any playable media.

    A YouTube source embeds via the privacy-friendly youtube-nocookie iframe,
    seeked with ?start=<seconds>. A non-YouTube http(s) url is treated as a
    direct audio source (podcast MP3 feeds) and seeked client-side with
    media.currentTime; the UI falls back to a plain link if playback fails.
    """
    start = int(timestamp_s or 0)
    vid = youtube_video_id(video_url)
    if vid:
        return {
            "type": "youtube",
            "video_id": vid,
            "src": f"https://www.youtube-nocookie.com/embed/{vid}?start={start}&rel=0",
            "video_url": video_url,
            "start_s": start,
        }
    u = (video_url or "").strip()
    if u.startswith("http://") or u.startswith("https://"):
        return {"type": "audio", "src": u, "video_url": u, "start_s": start}
    return None


@dataclass(frozen=True)
class PodcastCitation:
    """Attribution for a podcast-origin unit."""

    channel_name: str          # the show, e.g. "Founders"
    channel_url: str
    episode_title: str
    published_at: str
    video_url: str
    timestamp_s: int
    host: str                  # the artist/host, e.g. "David Senra"
    leaders: list[str] = field(default_factory=list)  # subjects discussed

    @property
    def deep_link(self) -> str | None:
        """Timestamped YouTube link, or None for non-YouTube sources."""
        return youtube_deep_link(self.video_url, self.timestamp_s)

    @property
    def embed(self) -> dict | None:
        """Format-aware player descriptor (youtube iframe / audio), or None."""
        return media_embed(self.video_url, self.timestamp_s)

    @property
    def host_label(self) -> str:
        """Short source label for the UI, e.g. 'youtu.be · 42:15'."""
        emb = self.embed
        prefix = "youtu.be" if emb and emb["type"] == "youtube" else "audio"
        return f"{prefix} · {_fmt_ts(self.timestamp_s)}"

    def to_dict(self) -> dict:
        return {
            "kind": "podcast",
            "channel_name": self.channel_name,
            "channel_url": self.channel_url,
            "episode_title": self.episode_title,
            "published_at": self.published_at,
            "video_url": self.video_url,
            "timestamp_s": self.timestamp_s,
            "deep_link": self.deep_link,
            "embed": self.embed,
            "host": self.host,
            "leaders": list(self.leaders),
            "label": self.host_label,
        }


@dataclass(frozen=True)
class ExternalCitation:
    """Attribution for an external (bio) unit."""

    source_name: str           # e.g. "Wikipedia"
    source_url: str
    license: str               # e.g. "CC BY-SA"

    @property
    def label(self) -> str:
        return f"{self.source_name} · {self.license}"

    def to_dict(self) -> dict:
        return {
            "kind": "external",
            "source_name": self.source_name,
            "source_url": self.source_url,
            "license": self.license,
            "label": self.label,
        }


def _fmt_ts(seconds: int) -> str:
    seconds = int(seconds)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


class UncitableNodeError(ValueError):
    """Raised when a podcast-origin node cannot produce a resolvable citation (§4a.5)."""
