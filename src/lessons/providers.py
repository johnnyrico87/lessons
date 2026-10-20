"""The real YouTube provider — the only code that touches the network and the
heavy optional deps (yt-dlp, youtube-transcript-api, faster-whisper).

Imported lazily so `import lessons.ingest` stays cheap and CI/tests never need
these installed. Install with: pip install -e ".[ingest]".
"""

from __future__ import annotations

import time

from lessons.ingest import (
    ChannelMeta,
    TranscriptResult,
    TransientIngestError,
    VideoMeta,
)


class RealYouTubeProvider:
    """Live provider. Construct inside `serve`/`ingest` command paths only."""

    def __init__(self, backoff_base_s: float = 1.0):
        self.backoff_base_s = backoff_base_s

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
        to whisper). Raises TransientIngestError on rate-limit/network blips."""
        try:
            from youtube_transcript_api import (  # type: ignore
                YouTubeTranscriptApi,
            )
            from youtube_transcript_api._errors import (  # type: ignore
                NoTranscriptFound,
                TranscriptsDisabled,
            )
        except ImportError as e:
            raise RuntimeError(
                "youtube-transcript-api not installed; pip install -e '.[ingest]'"
            ) from e

        try:
            segs = YouTubeTranscriptApi.get_transcript(video.id)
        except (NoTranscriptFound, TranscriptsDisabled):
            return None
        except Exception as e:  # network/rate-limit → retryable
            raise TransientIngestError(str(e)) from e

        lines = [{"start_s": int(s["start"]), "text": s["text"]} for s in segs]
        text = "\n".join(s["text"] for s in segs)
        return TranscriptResult(text=text, source="captions", lines=lines)

    def whisper_transcribe(self, video: VideoMeta) -> TranscriptResult:
        try:
            from faster_whisper import WhisperModel  # type: ignore  # noqa: F401
        except ImportError as e:
            raise RuntimeError(
                "faster-whisper not installed; pip install -e '.[ingest]'"
            ) from e
        # Audio download + transcription is wired in Phase 2 hardening; the seam
        # exists here so the orchestration and tests are complete now.
        raise NotImplementedError(
            "whisper audio path lands in Phase 2 hardening; captions cover v1 thin-slice"
        )

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

    def complete(self, prompt: str, transcript_chunk: str) -> str:
        import ollama  # type: ignore

        resp = ollama.chat(
            model=self.model_name,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": transcript_chunk},
            ],
            format="json",
            options={"temperature": 0},
        )
        return resp["message"]["content"]
