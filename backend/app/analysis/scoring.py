"""Validação das avaliações do modelo, pontuação final e seleção sem sobreposição."""

from dataclasses import dataclass

from app.analysis.windows import Window

LLM_CRITERIA = ("hook", "context", "emotion", "relevance", "retention")
WEIGHTS = {"hook": 0.25, "retention": 0.20, "relevance": 0.20, "emotion": 0.15, "context": 0.10, "technical": 0.10}


@dataclass
class Evaluation:
    window_id: int
    criteria: dict[str, int]  # 0..10
    title: str
    justification: str


@dataclass
class Candidate:
    window: Window
    criteria: dict[str, float]
    score: int
    title: str
    justification: str
    selected: bool = False


class InvalidEvaluation(ValueError):
    pass


def technical_score(w: Window) -> float:
    """Qualidade técnica estimada a partir da transcrição: proporção de fala e confiança do reconhecimento.
    (Análise visual está fora do MVP.)"""
    conf = w.avg_confidence if w.avg_confidence is not None else 0.6
    return round(10 * (0.5 * min(1.0, w.speech_ratio / 0.8) + 0.5 * conf), 1)


def validate(raw: list[dict], windows: dict[int, Window]) -> list[Evaluation]:
    """Descarta itens inválidos (id inexistente/duplicado, critérios fora de 0–10, texto vazio)."""
    out: list[Evaluation] = []
    seen: set[int] = set()
    for item in raw:
        try:
            wid = int(item["window_id"])
            crit = {k: int(item[k]) for k in LLM_CRITERIA}
            title = " ".join(str(item["title"]).split())[:120]
            just = " ".join(str(item["justification"]).split())[:1000]
        except (KeyError, TypeError, ValueError):
            continue
        if wid not in windows or wid in seen or not title or not just:
            continue
        if any(not 0 <= v <= 10 for v in crit.values()):
            continue
        seen.add(wid)
        out.append(Evaluation(wid, crit, title, just))
    return out


def score(ev: Evaluation, w: Window) -> Candidate:
    criteria: dict[str, float] = {**ev.criteria, "technical": technical_score(w)}
    total = sum(WEIGHTS[k] * criteria[k] for k in WEIGHTS) * 10
    return Candidate(window=w, criteria=criteria, score=max(0, min(100, round(total))), title=ev.title,
                     justification=ev.justification)


def select(cands: list[Candidate], *, duration: float, min_s: float, max_s: float, limit: int) -> list[Candidate]:
    """Escolhe os melhores trechos válidos e distintos (sem sobreposição), até `limit`."""
    chosen: list[Candidate] = []
    for c in sorted(cands, key=lambda c: (-c.score, c.window.start)):
        w = c.window
        if not (0 <= w.start < w.end <= duration + 0.01) or not (min_s - 0.01 <= w.duration <= max_s + 0.01):
            continue
        if any(w.start < o.window.end and o.window.start < w.end for o in chosen):
            continue
        c.selected = True
        chosen.append(c)
        if len(chosen) >= limit:
            break
    return sorted(chosen, key=lambda c: -c.score)
