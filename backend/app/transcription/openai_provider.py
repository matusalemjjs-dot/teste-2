"""Transcrição por serviço externo compatível com `POST {base}/audio/transcriptions` (API da OpenAI).

O áudio é enviado em MP3 mono 32 kbps (60 min ≈ 14 MB, dentro do limite de 25 MB do serviço).
"""

from pathlib import Path

import httpx

from app.core.config import Settings
from app.transcription.base import ProgressFn, Segment, TranscriptionError, TranscriptionResult, Word


class OpenAITranscriber:
    name = "openai"
    audio_format = "mp3"

    def __init__(self, settings: Settings):
        self.settings = settings
        if not settings.openai_api_key:
            raise TranscriptionError("transcription_misconfigured", "Serviço de transcrição não configurado.",
                                     detail="OPENAI_API_KEY ausente")

    def transcribe(self, audio_path: Path, duration: float, progress: ProgressFn) -> TranscriptionResult:
        s = self.settings
        if audio_path.stat().st_size > s.openai_max_upload_mb * 1024 * 1024:
            raise TranscriptionError("audio_too_large", "O áudio é grande demais para o serviço de transcrição.")
        progress(0.0)
        data: dict[str, str | list[str]] = {"model": s.openai_transcription_model,
                                            "response_format": "verbose_json",
                                            "timestamp_granularities[]": ["segment", "word"]}
        if s.transcription_language:
            data["language"] = s.transcription_language
        try:
            with audio_path.open("rb") as fh:
                r = httpx.post(f"{s.openai_base_url.rstrip('/')}/audio/transcriptions",
                               headers={"Authorization": f"Bearer {s.openai_api_key.get_secret_value()}"},
                               data=data, files={"file": (audio_path.name, fh, "audio/mpeg")},
                               timeout=httpx.Timeout(s.transcription_timeout_seconds, connect=15))
        except httpx.HTTPError as exc:
            raise TranscriptionError("transcription_unavailable",
                                     "Serviço de transcrição indisponível no momento. Tente novamente.",
                                     detail=type(exc).__name__) from exc
        if r.status_code == 429 or r.status_code >= 500:
            raise TranscriptionError("transcription_unavailable",
                                     "Serviço de transcrição indisponível no momento. Tente novamente.",
                                     detail=f"HTTP {r.status_code}")
        if r.status_code != 200:
            raise TranscriptionError("transcription_failed", "O serviço de transcrição recusou o áudio.",
                                     detail=f"HTTP {r.status_code}: {r.text[:300]}")
        try:
            body = r.json()
            words = [Word(float(w["start"]), float(w["end"]), w["word"]) for w in body.get("words") or []]
            segments = []
            for seg in body.get("segments") or []:
                st, en = float(seg["start"]), float(seg["end"])
                segments.append(Segment(st, en, seg.get("text", ""),
                                        [w for w in words if w.start >= st - 0.01 and w.end <= en + 0.01]))
        except (ValueError, KeyError, TypeError) as exc:
            raise TranscriptionError("transcription_failed", "Resposta inválida do serviço de transcrição.",
                                     detail=type(exc).__name__) from exc
        progress(1.0)
        return TranscriptionResult(segments=segments, language=body.get("language"), provider=self.name,
                                   model=s.openai_transcription_model,
                                   audio_duration=float(body.get("duration") or duration))
