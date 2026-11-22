"""Transcrição local com faster-whisper (CTranslate2). Sem custo por minuto; usa CPU/GPU do worker."""

import math
import threading
import wave
from pathlib import Path

import numpy as np

from app.core.config import Settings
from app.transcription.base import ProgressFn, Segment, TranscriptionError, TranscriptionResult, Word

_models: dict[tuple, object] = {}
_lock = threading.Lock()


def load_model(settings: Settings):
    key = (settings.whisper_model, settings.whisper_device, settings.whisper_compute_type,
           settings.whisper_cpu_threads, settings.whisper_model_dir)
    with _lock:
        if key not in _models:
            try:
                from faster_whisper import WhisperModel
            except ImportError as exc:
                raise TranscriptionError("transcription_misconfigured", "Serviço de transcrição não instalado.",
                                         detail="faster-whisper ausente") from exc
            try:
                _models[key] = WhisperModel(settings.whisper_model, device=settings.whisper_device,
                                            compute_type=settings.whisper_compute_type,
                                            cpu_threads=settings.whisper_cpu_threads,
                                            download_root=settings.whisper_model_dir)
            except Exception as exc:
                raise TranscriptionError("transcription_unavailable",
                                         "Serviço de transcrição indisponível no momento. Tente novamente.",
                                         detail=f"{type(exc).__name__}: {exc}") from exc
        return _models[key]


def read_pcm16_wav(path: Path) -> np.ndarray:
    """Lê WAV PCM 16 bits mono 16 kHz (gerado pelo FFmpeg) como float32. Evita depender do PyAV para decodificar."""
    with wave.open(str(path)) as w:
        if w.getsampwidth() != 2 or w.getnchannels() != 1 or w.getframerate() != 16000:
            raise TranscriptionError("transcription_failed", "Formato de áudio interno inesperado.",
                                     detail=f"{w.getsampwidth()=} {w.getnchannels()=} {w.getframerate()=}")
        return np.frombuffer(w.readframes(w.getnframes()), dtype=np.int16).astype(np.float32) / 32768.0


class FasterWhisperTranscriber:
    name = "faster_whisper"
    audio_format = "wav"

    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, audio_path: Path, duration: float, progress: ProgressFn) -> TranscriptionResult:
        model = load_model(self.settings)
        try:
            audio = read_pcm16_wav(audio_path)
            segments_iter, info = model.transcribe(
                audio,
                language=self.settings.transcription_language or None,
                beam_size=self.settings.whisper_beam_size,
                vad_filter=True,  # ignora silêncio/ruído; reduz "alucinações" em trechos sem fala
                word_timestamps=True,
                condition_on_previous_text=False,
            )
            segments: list[Segment] = []
            for s in segments_iter:  # gerador: a transcrição acontece aqui, segmento a segmento
                words = [Word(w.start, w.end, w.word, w.probability) for w in (s.words or [])]
                conf = math.exp(s.avg_logprob) if s.avg_logprob is not None else None
                segments.append(Segment(s.start, s.end, s.text, words, round(min(conf, 1.0), 4) if conf else None))
                progress(min(s.end / duration, 1.0) if duration else 0.0)
        except (TranscriptionError, MemoryError):
            raise
        except Exception as exc:
            from app.pipeline.context import Canceled, PipelineError

            if isinstance(exc, Canceled | PipelineError):
                raise
            raise TranscriptionError("transcription_failed", "Falha ao transcrever o áudio do vídeo.",
                                     detail=f"{type(exc).__name__}: {exc}") from exc
        return TranscriptionResult(segments=segments, language=info.language, provider=self.name,
                                   model=self.settings.whisper_model, audio_duration=info.duration)
