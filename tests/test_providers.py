"""Provider tests — the whisper audio path (requirements §1.3).

Tests the pure segment→result conversion directly, and the download→transcribe→
cleanup flow with yt-dlp and faster-whisper mocked (sys.modules injection), so
no network, no audio, and no model are needed.
"""

from __future__ import annotations

import sys
import types

from lessons.providers import RealYouTubeProvider, whisper_segments_to_result

# --- pure conversion -------------------------------------------------------

def test_segments_to_result_keeps_timestamps_and_drops_empties():
    segs = [
        {"start": 0.4, "text": " He kept plans secret. "},
        {"start": 12.9, "text": ""},             # dropped
        {"start": 20.0, "text": "Others were open."},
    ]
    tr = whisper_segments_to_result(segs)
    assert tr.source == "whisper"
    assert tr.lines == [
        {"start_s": 0, "text": "He kept plans secret."},
        {"start_s": 20, "text": "Others were open."},
    ]
    assert "He kept plans secret." in tr.text
    assert "Others were open." in tr.text


# --- download + transcribe + cleanup, fully mocked -------------------------

class _FakeSegment:
    def __init__(self, start, text):
        self.start = start
        self.text = text


class _FakeWhisperModel:
    def __init__(self, *a, **k):
        pass

    def transcribe(self, audio_path):
        return ([_FakeSegment(0.0, "hello"), _FakeSegment(5.0, "world")], {"lang": "en"})


def _install_fake_modules(monkeypatch, tmp_path, recorder):
    # Fake faster_whisper
    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = _FakeWhisperModel
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)

    # Fake yt_dlp that "downloads" by creating a file and returns its path.
    ytdlp = types.ModuleType("yt_dlp")

    class _FakeYDL:
        def __init__(self, opts):
            self.opts = opts

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def extract_info(self, url, download):
            path = tmp_path / "aud.webm"
            path.write_bytes(b"fake-audio")
            recorder["audio_path"] = str(path)
            return {"id": "v1", "ext": "webm"}

        def prepare_filename(self, info):
            return recorder["audio_path"]

    ytdlp.YoutubeDL = _FakeYDL
    monkeypatch.setitem(sys.modules, "yt_dlp", ytdlp)


def test_whisper_transcribe_downloads_transcribes_and_cleans_up(monkeypatch, tmp_path):
    from lessons.ingest import VideoMeta

    recorder: dict = {}
    _install_fake_modules(monkeypatch, tmp_path, recorder)

    prov = RealYouTubeProvider()
    v = VideoMeta("v1", "C1", "@x", "Title", "2024-01-01", 600, "https://youtu.be/v1")
    tr = prov.whisper_transcribe(v)

    assert tr.source == "whisper"
    assert [line["text"] for line in tr.lines] == ["hello", "world"]
    assert tr.lines[1]["start_s"] == 5
    # audio file was cleaned up after transcription
    import os
    assert not os.path.exists(recorder["audio_path"])
