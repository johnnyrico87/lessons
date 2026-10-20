"""RSS ingest adapter tests (requirements §1, design §6 alt source).

Feed parsing is pure; the provider is driven with an injected http_get so there
is no network and no Whisper model. Proves enumeration, the captions→whisper
fallthrough, and duration parsing.
"""

from __future__ import annotations

from lessons.ingest import Ingester
from lessons.rss import RssProvider, _parse_duration, parse_feed
from lessons.store import Store

SAMPLE_FEED = b"""<?xml version="1.0"?>
<rss xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd" version="2.0">
  <channel>
    <title>How to Take Over the World</title>
    <item>
      <title>Napoleon Part 1</title>
      <guid>ep-001</guid>
      <pubDate>Mon, 01 Jan 2024 00:00:00 +0000</pubDate>
      <itunes:duration>1:02:03</itunes:duration>
      <enclosure url="https://traffic.megaphone.fm/ep001.mp3" length="0" type="audio/mpeg"/>
    </item>
    <item>
      <title>Napoleon Part 2</title>
      <guid>ep-002</guid>
      <pubDate>Mon, 15 Jan 2024 00:00:00 +0000</pubDate>
      <itunes:duration>3600</itunes:duration>
      <enclosure url="https://traffic.megaphone.fm/ep002.mp3" length="0" type="audio/mpeg"/>
    </item>
    <item>
      <title>No audio item (skipped)</title>
      <guid>ep-003</guid>
    </item>
  </channel>
</rss>"""


def test_parse_feed_extracts_episodes_with_audio():
    title, eps = parse_feed(SAMPLE_FEED)
    assert title == "How to Take Over the World"
    assert len(eps) == 2  # the audioless item is skipped
    assert eps[0].title == "Napoleon Part 1"
    assert eps[0].audio_url.endswith("ep001.mp3")
    assert eps[0].duration_s == 3723          # 1:02:03
    assert eps[1].duration_s == 3600          # plain seconds


def test_duration_parsing():
    assert _parse_duration("3600") == 3600
    assert _parse_duration("1:02:03") == 3723
    assert _parse_duration("45:30") == 2730
    assert _parse_duration(None) == 0
    assert _parse_duration("garbage") == 0


def test_provider_lists_episodes_from_feed():
    prov = RssProvider("https://feeds.example.com/show", http_get=lambda url: SAMPLE_FEED)
    cm = prov.channel_meta("@show", "https://feeds.example.com/show")
    assert cm.name == "How to Take Over the World"
    vids = prov.list_videos(cm)
    assert len(vids) == 2
    assert vids[0].url.endswith("ep001.mp3")
    # stable namespaced ids
    assert vids[0].id.startswith("rss_")
    assert vids[0].id != vids[1].id


def test_fetch_transcript_always_none_falls_through_to_whisper():
    prov = RssProvider("https://feeds.example.com/show", http_get=lambda url: SAMPLE_FEED)
    cm = prov.channel_meta("@show", "u")
    v = prov.list_videos(cm)[0]
    assert prov.fetch_transcript(v) is None  # feeds carry audio, not captions


def test_ingest_through_rss_provider_uses_whisper(monkeypatch):
    """End-to-end orchestration with a fake provider: no captions -> whisper
    transcribe is called, transcript stored as source='whisper'."""
    from lessons.ingest import TranscriptResult

    prov = RssProvider("https://feeds.example.com/show", http_get=lambda url: SAMPLE_FEED)
    # stub the whisper transcription so no audio/model is needed
    monkeypatch.setattr(
        prov, "whisper_transcribe",
        lambda video: TranscriptResult(
            text="transcribed " + video.title, source="whisper",
            lines=[{"start_s": 0, "text": "transcribed " + video.title}],
        ),
    )
    s = Store.memory()
    try:
        r = Ingester(s, prov).ingest(
            channels=[{"handle": "show", "url": "https://feeds.example.com/show"}],
            limit=1,
        )
        assert r.videos_ingested == 1          # limit respected
        assert r.whisper_used == 1
        row = s.conn.execute("SELECT source FROM transcripts").fetchone()
        assert row["source"] == "whisper"
    finally:
        s.close()
