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

from dataclasses import dataclass, field


def youtube_deep_link(video_url: str, timestamp_s: int) -> str:
    """Build a YouTube deep link to a timestamp.

    Normalizes to the short youtu.be form with ?t=<seconds>. Accepts either a
    youtu.be/<id> or youtube.com/watch?v=<id> url.
    """
    vid = _video_id_from_url(video_url)
    return f"https://youtu.be/{vid}?t={int(timestamp_s)}"


def _video_id_from_url(video_url: str) -> str:
    u = video_url.strip()
    if "youtu.be/" in u:
        return u.split("youtu.be/", 1)[1].split("?", 1)[0].split("/", 1)[0]
    if "v=" in u:
        return u.split("v=", 1)[1].split("&", 1)[0]
    # Fallback: assume the trailing path segment is the id.
    return u.rstrip("/").rsplit("/", 1)[-1]


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
    def deep_link(self) -> str:
        return youtube_deep_link(self.video_url, self.timestamp_s)

    @property
    def host_label(self) -> str:
        """Short source label for the UI, e.g. 'youtu.be · 42:15'."""
        return f"youtu.be · {_fmt_ts(self.timestamp_s)}"

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
