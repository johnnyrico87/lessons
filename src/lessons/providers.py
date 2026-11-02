"""The real YouTube provider — the only code that touches the network and the
heavy optional deps (yt-dlp, youtube-transcript-api, faster-whisper).

Imported lazily so `import lessons.ingest` stays cheap and CI/tests never need
these installed. Install with: pip install -e ".[ingest]".
"""

from __future__ import annotations

import os
import tempfile
import time

from lessons.ingest import (
    ChannelMeta,
    TranscriptResult,
    TransientIngestError,
    VideoMeta,
)


def whisper_segments_to_result(segments: list[dict]) -> TranscriptResult:
    """Convert faster-whisper segments [{start, text}, ...] into a
    TranscriptResult with per-line timestamps. Pure — unit-tested without the
    model."""
    lines = [
        {"start_s": int(s.get("start", 0)), "text": (s.get("text") or "").strip()}
        for s in segments
        if (s.get("text") or "").strip()
    ]
    text = "\n".join(line["text"] for line in lines)
    return TranscriptResult(text=text, source="whisper", lines=lines)


class RealYouTubeProvider:
    """Live provider. Construct inside `serve`/`ingest` command paths only."""

    def __init__(
        self,
        backoff_base_s: float = 1.0,
        whisper_model: str = "base",
        whisper_compute_type: str = "int8",
    ):
        self.backoff_base_s = backoff_base_s
        self.whisper_model = whisper_model
        self.whisper_compute_type = whisper_compute_type
        self._whisper = None  # lazily constructed WhisperModel

    def channel_meta(self, handle: str, url: str) -> ChannelMeta:
        info = self._ytdlp_extract(url, flat=True)
        cid = info.get("channel_id") or info.get("id") or handle
        name = info.get("channel") or info.get("uploader") or handle.lstrip("@")
        return ChannelMeta(id=cid, handle=handle, url=url, name=name)

    def list_videos(self, channel: ChannelMeta) -> list[VideoMeta]:
        info = self._ytdlp_extract(channel.url, flat=True)
        entries = info.get("entries") or []
        out: list[VideoMeta] = []
        for e in entries:
            vid = e.get("id")
            if not vid:
                continue
            out.append(
                VideoMeta(
                    id=vid,
                    channel_id=channel.id,
                    channel_handle=channel.handle,
                    title=e.get("title") or "",
                    published_at=_upload_date(e.get("upload_date")),
                    duration_s=int(e.get("duration") or 0),
                    url=e.get("url") or f"https://youtu.be/{vid}",
                )
            )
        return out

    def fetch_transcript(self, video: VideoMeta) -> TranscriptResult | None:
        """Return captions, or None when the video has none (caller falls back
        to whisper). Raises TransientIngestError on rate-limit/network blips.

        Uses youtube-transcript-api >= 1.0 (instance ``fetch``). The snippets are
        objects with ``.start`` / ``.text``.
        """
        try:
            from youtube_transcript_api import (  # type: ignore
                YouTubeTranscriptApi,
            )
            from youtube_transcript_api._errors import (  # type: ignore
                NoTranscriptFound,
                TranscriptsDisabled,
                VideoUnavailable,
            )
        except ImportError as e:
            raise RuntimeError(
                "youtube-transcript-api not installed; pip install -e '.[ingest]'"
            ) from e

        try:
            fetched = YouTubeTranscriptApi().fetch(video.id)
        except (NoTranscriptFound, TranscriptsDisabled, VideoUnavailable):
            return None  # no captions → caller falls back to whisper
        except Exception as e:  # network/rate-limit/blocked → retryable
            raise TransientIngestError(str(e)) from e

        lines = [{"start_s": int(s.start), "text": s.text} for s in fetched]
        text = "\n".join(s.text for s in fetched)
        if not lines:
            return None
        return TranscriptResult(text=text, source="captions", lines=lines)

    def whisper_transcribe(self, video: VideoMeta) -> TranscriptResult:
        """Download the video's audio and transcribe it locally with
        faster-whisper, returning timestamped segments (R§1.3).

        Audio goes to a temp dir and is deleted after transcription — it is raw
        material we never keep or commit.
        """
        audio_path = self._download_audio(video)
        try:
            segments = self._transcribe(audio_path)
        finally:
            try:
                os.remove(audio_path)
            except OSError:
                pass
        return whisper_segments_to_result(segments)

    def _download_audio(self, video: VideoMeta) -> str:
        """Download bestaudio to a temp file via yt-dlp; return its path."""
        try:
            import yt_dlp  # type: ignore
        except ImportError as e:
            raise RuntimeError("yt-dlp not installed; pip install -e '.[ingest]'") from e

        tmpdir = tempfile.mkdtemp(prefix="lessons-audio-")
        outtmpl = os.path.join(tmpdir, f"{video.id}.%(ext)s")
        opts = {
            "quiet": True,
            "format": "bestaudio/best",
            "outtmpl": outtmpl,
            "noprogress": True,
        }
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    info = ydl.extract_info(video.url, download=True)
                    return ydl.prepare_filename(info)
            except Exception as e:
                if attempt == 2:
                    raise TransientIngestError(str(e)) from e
                time.sleep(self.backoff_base_s * (2**attempt))
        raise TransientIngestError("audio download failed")

    def _transcribe(self, audio_path: str) -> list[dict]:
        """Run faster-whisper, return [{start, text}, ...]."""
        try:
            from faster_whisper import WhisperModel  # type: ignore
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper not installed; pip install -e '.[ingest]'"
            ) from e
        if self._whisper is None:
            self._whisper = WhisperModel(
                self.whisper_model, compute_type=self.whisper_compute_type
            )
        segments, _info = self._whisper.transcribe(audio_path)
        return [{"start": seg.start, "text": seg.text} for seg in segments]

    def _ytdlp_extract(self, url: str, flat: bool) -> dict:
        try:
            import yt_dlp  # type: ignore
        except ImportError as e:
            raise RuntimeError("yt-dlp not installed; pip install -e '.[ingest]'") from e
        opts = {"quiet": True, "skip_download": True, "extract_flat": flat}
        for attempt in range(3):
            try:
                with yt_dlp.YoutubeDL(opts) as ydl:
                    return ydl.extract_info(url, download=False)
            except Exception as e:
                if attempt == 2:
                    raise TransientIngestError(str(e)) from e
                time.sleep(self.backoff_base_s * (2**attempt))
        return {}


def _upload_date(yyyymmdd: str | None) -> str:
    if not yyyymmdd or len(yyyymmdd) != 8:
        return ""
    return f"{yyyymmdd[0:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:8]}"


class OllamaClient:
    """Local LLM client for extraction (Pass B). Lazy-imports the ollama client.

    Install with: pip install -e ".[extract]" and have Ollama running locally.
    """

    def __init__(self, model: str = "llama3.1"):
        try:
            import ollama  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "ollama client not installed; pip install -e '.[extract]' "
                "and run a local Ollama server"
            ) from e
        self.model_name = model
        # Sampling params the fingerprint records. Kept as an attribute (not a
        # literal in complete()) so the recorded provenance matches what ran.
        self.sampling: dict = {"temperature": 0}

    def model_digest(self) -> str:
        """Ollama's content digest for the loaded model tag, used in the run
        fingerprint so a silent model swap under the same tag forces re-extract.
        Best-effort: returns '' if the server can't be reached or the shape
        changes, so extraction never hard-fails on a telemetry lookup."""
        try:
            import ollama  # type: ignore

            info = ollama.show(self.model_name)
        except Exception:
            return ""
        # ollama.show() shape varies by client version; probe the usual spots.
        for key in ("digest", "model_digest"):
            val = info.get(key) if isinstance(info, dict) else getattr(info, key, None)
            if val:
                return str(val)
        details = info.get("details") if isinstance(info, dict) else getattr(info, "details", None)
        if isinstance(details, dict) and details.get("digest"):
            return str(details["digest"])
        return ""

    def complete(self, prompt: str, transcript_chunk: str) -> str:
        import ollama  # type: ignore

        from lessons.extract_schema import extraction_json_schema

        resp = ollama.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript_chunk},
            ],
            # Grammar-constrain output to our extraction schema (valid node types,
            # relations from our enum, required fields present) — not just "json".
            format=extraction_json_schema(),
            options=self.sampling,
        )
        return resp["message"]["content"]


class WikipediaBioProvider:
    """Fetches a short bio from the Wikipedia REST summary API, plus the lead
    image (Wikipedia/Wikimedia Commons). Lazy-imports httpx.

    Prose is CC BY-SA (Wikipedia's license); the lead image carries its own
    license on Commons, which we record separately. Only clearly-licensed
    content is returned; anything ambiguous is dropped by the caller.
    """

    SUMMARY_URL = "https://en.wikipedia.org/api/rest_v1/page/summary/{title}"

    def __init__(self, user_agent: str = "lessons/0.0 (https://github.com/johnnyrico87/lessons)"):
        self.user_agent = user_agent

    def fetch_bio(self, person_label: str):
        from lessons.enrich import Bio

        try:
            import httpx  # type: ignore
        except ImportError as e:
            raise RuntimeError("httpx not installed; pip install -e '.[dev]'") from e

        title = person_label.strip().replace(" ", "_")
        url = self.SUMMARY_URL.format(title=title)
        try:
            resp = httpx.get(
                url, headers={"User-Agent": self.user_agent}, timeout=15, follow_redirects=True
            )
        except Exception:
            return None
        if resp.status_code != 200:
            return None
        data = resp.json()
        if data.get("type") == "disambiguation" or not data.get("extract"):
            return None

        image_url = (data.get("thumbnail") or {}).get("source")
        return Bio(
            source="Wikipedia",
            source_url=(data.get("content_urls", {}).get("desktop", {}) or {}).get("page")
            or f"https://en.wikipedia.org/wiki/{title}",
            license="CC BY-SA 4.0",
            title=data.get("title"),
            summary=data.get("extract"),
            image_url=image_url,
            # Commons lead images are typically CC/PD but vary per file; record
            # provenance and let the UI link out rather than assert a license.
            image_license="see Wikimedia Commons" if image_url else None,
        )
