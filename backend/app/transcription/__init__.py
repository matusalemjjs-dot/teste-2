"""Adaptadores de transcrição. O provedor é escolhido por TRANSCRIPTION_PROVIDER."""

from app.core.config import Settings
from app.transcription.base import Segment, Transcriber, TranscriptionError, TranscriptionResult

__all__ = ["Segment", "Transcriber", "TranscriptionError", "TranscriptionResult", "get_transcriber"]


def get_transcriber(settings: Settings) -> Transcriber:
    provider = settings.transcription_provider.lower()
    if provider == "faster_whisper":
        from app.transcription.faster_whisper_provider import FasterWhisperTranscriber

        return FasterWhisperTranscriber(settings)
    if provider == "openai":
        from app.transcription.openai_provider import OpenAITranscriber

        return OpenAITranscriber(settings)
    raise TranscriptionError("transcription_misconfigured", "Serviço de transcrição não configurado.",
                             detail=f"provider={provider}")
