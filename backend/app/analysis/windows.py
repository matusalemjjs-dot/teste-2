"""Geração de janelas candidatas (15–60 s) a partir dos segmentos da transcrição.

As janelas começam e terminam em limites de segmento, preferindo terminar em fim de frase, para que o
corte não comece ou termine no meio de uma fala.
"""

from dataclasses import dataclass

SENTENCE_END = (".", "!", "?", "…")
TARGETS = (22.0, 35.0, 50.0)  # durações-alvo aproximadas


@dataclass(frozen=True)
class Seg:
    seq: int
    start: float
    end: float
    text: str
    confidence: float | None = None


@dataclass(frozen=True)
class Window:
    id: int
    first_seq: int
    last_seq: int
    start: float
    end: float
    text: str
    speech_ratio: float  # fração da janela com fala
    avg_confidence: float | None

    @property
    def duration(self) -> float:
        return self.end - self.start


def _overlap_ratio(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    return inter / min(a[1] - a[0], b[1] - b[0])


def build_windows(segs: list[Seg], min_s: float, max_s: float, max_windows: int) -> list[Window]:
    raw: list[tuple[int, int]] = []
    n = len(segs)
    for i in range(n):
        if i > 0 and not segs[i - 1].text.rstrip().endswith(SENTENCE_END) and i % 3:
            continue  # começa preferencialmente após fim de frase (mas não só, para não perder trechos)
        ends: list[int] = []
        for j in range(i, n):
            dur = segs[j].end - segs[i].start
            if dur > max_s:
                break
            if dur >= min_s:
                ends.append(j)
        if not ends:
            continue
        for target in TARGETS:
            def cost(j: int, target: float = target, i: int = i) -> float:
                dur = segs[j].end - segs[i].start
                penalty = 0 if segs[j].text.rstrip().endswith(SENTENCE_END) else 8
                return abs(dur - target) + penalty
            best = min(ends, key=cost)
            if (i, best) not in raw:
                raw.append((i, best))

    # Remove quase-duplicatas (sobreposição > 85%), preservando a ordem temporal.
    kept: list[tuple[int, int]] = []
    for i, j in sorted(raw, key=lambda r: (segs[r[0]].start, segs[r[1]].end)):
        span = (segs[i].start, segs[j].end)
        if any(_overlap_ratio(span, (segs[a].start, segs[b].end)) > 0.85 for a, b in kept[-6:]):
            continue
        kept.append((i, j))

    if len(kept) > max_windows:  # amostra uniforme ao longo do vídeo
        step = len(kept) / max_windows
        kept = [kept[int(k * step)] for k in range(max_windows)]

    windows = []
    for wid, (i, j) in enumerate(kept):
        part = segs[i:j + 1]
        start, end = segs[i].start, segs[j].end
        speech = sum(s.end - s.start for s in part)
        confs = [s.confidence for s in part if s.confidence is not None]
        windows.append(Window(
            id=wid, first_seq=segs[i].seq, last_seq=segs[j].seq, start=round(start, 3), end=round(end, 3),
            text=" ".join(s.text for s in part), speech_ratio=min(1.0, speech / (end - start)),
            avg_confidence=sum(confs) / len(confs) if confs else None,
        ))
    return windows
