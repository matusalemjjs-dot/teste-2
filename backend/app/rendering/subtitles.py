"""Legendas sincronizadas a partir dos timestamps por palavra da transcrição (ASS para queimar, SRT para baixar)."""

from dataclasses import dataclass

PUNCT_BREAK = (".", "!", "?", ",", ";", ":", "…")


@dataclass(frozen=True)
class Cue:
    start: float  # relativo ao início do corte
    end: float
    text: str


def build_cues(segments: list[dict], clip_start: float, clip_end: float, *, max_words: int = 4,
               max_duration: float = 1.8) -> list[Cue]:
    """`segments`: [{start, end, text, words: [{start, end, word}] | None}] em tempo absoluto do vídeo."""
    words: list[tuple[float, float, str]] = []
    for seg in segments:
        ws = seg.get("words") or []
        if ws:
            words.extend((float(w["start"]), float(w["end"]), str(w["word"]).strip()) for w in ws)
        else:  # sem timestamps por palavra: distribui o tempo do segmento proporcionalmente ao tamanho
            toks = str(seg["text"]).split()
            total = sum(len(t) for t in toks) or 1
            t = float(seg["start"])
            span = float(seg["end"]) - t
            for tok in toks:
                d = span * len(tok) / total
                words.append((t, t + d, tok))
                t += d
    words = [(max(s, clip_start), min(e, clip_end), w) for s, e, w in words
             if w and e > clip_start and s < clip_end]

    cues: list[Cue] = []
    cur: list[tuple[float, float, str]] = []

    def flush():
        if cur:
            cues.append(Cue(round(cur[0][0] - clip_start, 3), round(cur[-1][1] - clip_start, 3),
                            " ".join(w for _, _, w in cur)))
            cur.clear()

    for s, e, w in words:
        if cur and (len(cur) >= max_words or e - cur[0][0] > max_duration or s - cur[-1][1] > 0.6):
            flush()
        cur.append((s, e, w))
        if w.endswith(PUNCT_BREAK):
            flush()
    flush()
    # sem sobreposição entre legendas consecutivas
    fixed: list[Cue] = []
    for c in cues:
        start = max(c.start, fixed[-1].end) if fixed else c.start
        if c.end > start:
            fixed.append(Cue(start, c.end, c.text))
    return fixed


def _ts_srt(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3_600_000)
    m, ms = divmod(ms, 60_000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _ts_ass(t: float) -> str:
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360_000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def to_srt(cues: list[Cue]) -> str:
    return "".join(f"{i}\n{_ts_srt(c.start)} --> {_ts_srt(c.end)}\n{c.text}\n\n" for i, c in enumerate(cues, 1))


def _ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def to_ass(cues: list[Cue], *, width: int, height: int, font: str, size: int) -> str:
    margin_v = int(height * 0.2)
    header = (
        "[Script Info]\nScriptType: v4.00+\nWrapStyle: 0\nScaledBorderAndShadow: yes\n"
        f"PlayResX: {width}\nPlayResY: {height}\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, "
        "Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, "
        "MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,"
        f"{max(2, size // 14)},2,2,60,60,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    lines = [f"Dialogue: 0,{_ts_ass(c.start)},{_ts_ass(c.end)},Default,,0,0,0,,{_ass_escape(c.text)}" for c in cues]
    return header + "\n".join(lines) + "\n"
