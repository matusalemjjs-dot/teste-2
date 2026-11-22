from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol


@dataclass
class Word:
    start: float
    end: float
    word: str
    probability: float | None = None


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word] = field(default_factory=list)
    confidence: float | None = None


@dataclass
class TranscriptionResult:
    segments: list[Segment]
    language: str | None
    provider: str
    model: str
    audio_duration: float


class TranscriptionError(Exception):
    def __init__(self, code: str, message: str, detail: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, detail


# progress(fração 0..1) — também é o ponto de cancelamento: pode levantar exceção para interromper.
ProgressFn = Callable[[float], None]


class Transcriber(Protocol):
    name: str
    audio_format: str  # "wav" (PCM 16 kHz mono) ou "mp3"

    def transcribe(self, audio_path: Path, duration: float, progress: ProgressFn) -> TranscriptionResult: ...


def normalize_segments(segments: list[Segment], duration: float, min_len: float = 0.05) -> list[Segment]:
    """Garante timestamps coerentes: dentro de [0, duração], em ordem, sem sobreposição e sem texto vazio."""
    out: list[Segment] = []
    for seg in sorted(segments, key=lambda s: (s.start, s.end)):
        text = " ".join(seg.text.split())
        if not text:
            continue
        start = max(0.0, min(seg.start, duration))
        end = max(0.0, min(seg.end, duration))
        if out and start < out[-1].end:
            start = out[-1].end
        if end - start < min_len:
            continue
        words = [Word(max(start, min(w.start, end)), max(start, min(w.end, end)), w.word.strip(), w.probability)
                 for w in seg.words if w.word.strip()]
        out.append(Segment(round(start, 3), round(end, 3), text, words, seg.confidence))
    return out
