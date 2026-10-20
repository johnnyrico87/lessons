"""Phase 2 — ingest orchestration tests (requirements §1, design §6).

Drives the Ingester with an in-memory FakeProvider — no network, no yt-dlp,
no whisper. Proves resumability, de-dup, whisper fallback, retry/backoff, and
the probe report.
"""

from __future__ import annotations

import pytest

from lessons.ingest import (
    ChannelMeta,
    Ingester,
    ProbeReport,
    TranscriptResult,
    TransientIngestError,
    VideoMeta,
    _dup_key,
    _norm_title,
)
from lessons.store import Store


class FakeProvider:
    """Scriptable provider. `videos` maps channel handle -> [VideoMeta];
    `transcripts` maps video id -> TranscriptResult|None (None => whisper)."""

    def __init__(self, channels, videos, transcripts, fail_once=None):
        self._channels = channels            # handle -> ChannelMeta
        self._videos = videos                # handle -> [VideoMeta]
        self._transcripts = transcripts      # id -> TranscriptResult | None
        self._fail_once = set(fail_once or [])  # ids that raise TransientIngestError once
        self.fetch_calls = 0

    def channel_meta(self, handle, url):
        return self._channels[handle]

    def list_videos(self, channel):
        return self._videos[channel.handle]

    def fetch_transcript(self, video):
        self.fetch_calls += 1
        if video.id in self._fail_once:
            self._fail_once.discard(video.id)
            raise TransientIngestError("rate limited")
        return self._transcripts.get(video.id)

    def whisper_transcribe(self, video):
        return TranscriptResult(text=f"[whisper] {video.title}", source="whisper")


def _cap(text="caption text"):
    return TranscriptResult(text=text, source="captions", lines=[{"start_s": 0, "text": text}])


@pytest.fixture
def store():
    s = Store.memory()
    yield s
    s.close()


def _one_channel_setup():
    cm = ChannelMeta(id="C1", handle="@DavidSenra", url="u", name="David Senra")
    vids = [
        VideoMeta("v1", "C1", "@DavidSenra", "Rockefeller #1", "2024-01-01", 3600, "https://youtu.be/v1"),
        VideoMeta("v2", "C1", "@DavidSenra", "Carnegie #2", "2024-02-01", 3000, "https://youtu.be/v2"),
    ]
    return cm, vids


# --- basic ingest ----------------------------------------------------------

def test_ingest_writes_videos_and_transcripts(store):
    cm, vids = _one_channel_setup()
    prov = FakeProvider(
        channels={"@DavidSenra": cm},
        videos={"@DavidSenra": vids},
        transcripts={"v1": _cap(), "v2": _cap()},
    )
    r = Ingester(store, prov).ingest(channels=[{"handle": "@DavidSenra", "url": "u"}])
    assert r.videos_ingested == 2
    assert store.count("videos") == 2
    assert store.count("transcripts") == 2


# --- resumability (R§1.5) --------------------------------------------------

def test_rerun_skips_already_ingested(store):
    cm, vids = _one_channel_setup()
    prov = FakeProvider({"@DavidSenra": cm}, {"@DavidSenra": vids},
                        {"v1": _cap(), "v2": _cap()})
    ch = [{"handle": "@DavidSenra", "url": "u"}]
    Ingester(store, prov).ingest(channels=ch)
    r2 = Ingester(store, prov).ingest(channels=ch)
    assert r2.videos_ingested == 0
    assert r2.videos_skipped_existing == 2
    assert store.count("videos") == 2  # not doubled


# --- whisper fallback (R§1.3) ----------------------------------------------

def test_whisper_fallback_when_no_captions(store):
    cm, vids = _one_channel_setup()
    prov = FakeProvider({"@DavidSenra": cm}, {"@DavidSenra": vids},
                        {"v1": _cap(), "v2": None})  # v2 has no captions
    r = Ingester(store, prov).ingest(channels=[{"handle": "@DavidSenra", "url": "u"}])
    assert r.whisper_used == 1
    row = store.conn.execute("SELECT source FROM transcripts WHERE video_id='v2'").fetchone()
    assert row["source"] == "whisper"


# --- cross-channel de-dup (R§1.6) ------------------------------------------

def test_dedup_same_video_across_channels(store):
    c1 = ChannelMeta("C1", "@DavidSenra", "u1", "David Senra")
    c2 = ChannelMeta("C2", "@founderspodcast1", "u2", "Founders")
    # Same episode, different ids + episode-number formatting, same duration.
    v_a = VideoMeta(
        "a", "C1", "@DavidSenra", "John D. Rockefeller #1", "2024-01-01", 3600, "ua"
    )
    v_b = VideoMeta(
        "b", "C2", "@founderspodcast1", "John D. Rockefeller (Ep. 1)", "2024-01-01", 3604, "ub"
    )
    prov = FakeProvider(
        {"@DavidSenra": c1, "@founderspodcast1": c2},
        {"@DavidSenra": [v_a], "@founderspodcast1": [v_b]},
        {"a": _cap(), "b": _cap()},
    )
    r = Ingester(store, prov).ingest(
        channels=[
            {"handle": "@DavidSenra", "url": "u1"},
            {"handle": "@founderspodcast1", "url": "u2"},
        ]
    )
    assert r.videos_ingested == 1
    assert r.videos_deduped == 1


def test_norm_title_strips_episode_numbers():
    a = _norm_title("John D. Rockefeller #412")
    b = _norm_title("John D Rockefeller (Episode 412)")
    assert a == b


def test_dup_key_duration_bucket_tolerates_small_drift():
    a = VideoMeta("a", "C", "@x", "Same Title", "", 3600, "u")
    b = VideoMeta("b", "C", "@x", "Same Title", "", 3604, "u")
    assert _dup_key(a) == _dup_key(b)


# --- retry / backoff (R§1.7) -----------------------------------------------

def test_transient_error_is_retried_then_succeeds(store):
    cm, vids = _one_channel_setup()
    prov = FakeProvider({"@DavidSenra": cm}, {"@DavidSenra": vids},
                        {"v1": _cap(), "v2": _cap()}, fail_once=["v1"])
    r = Ingester(store, prov).ingest(channels=[{"handle": "@DavidSenra", "url": "u"}])
    assert r.videos_ingested == 2  # v1 recovered on retry


# --- probe (R§1.8) ---------------------------------------------------------

def test_probe_reports_coverage_and_does_not_persist(store):
    cm, vids = _one_channel_setup()
    prov = FakeProvider({"@DavidSenra": cm}, {"@DavidSenra": vids},
                        {"v1": _cap(), "v2": None})
    report = Ingester(store, prov).ingest(channels=[{"handle": "@DavidSenra", "url": "u"}], probe=5)
    assert isinstance(report, ProbeReport)
    assert report.sampled == 2
    assert report.with_captions == 1
    assert report.whisper_needed == 1
    assert report.caption_coverage_pct == 50.0
    # probe is read-only
    assert store.count("videos") == 0
    assert "coverage 50%" in report.render()


# --- line persistence (R§1.2 timestamps) -----------------------------------

def test_ingest_persists_transcript_lines(store):
    cm = ChannelMeta(id="C1", handle="@DavidSenra", url="u", name="David Senra")
    v = VideoMeta("v1", "C1", "@DavidSenra", "T", "2024-01-01", 600, "https://youtu.be/v1")
    tr = TranscriptResult(
        text="one\ntwo",
        source="captions",
        lines=[{"start_s": 10, "text": "one"}, {"start_s": 125, "text": "two"}],
    )
    prov = FakeProvider({"@DavidSenra": cm}, {"@DavidSenra": [v]}, {"v1": tr})
    Ingester(store, prov).ingest(channels=[{"handle": "@DavidSenra", "url": "u"}])
    assert store.lines_for("v1") == [
        {"start_s": 10, "text": "one"},
        {"start_s": 125, "text": "two"},
    ]
