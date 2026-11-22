import json
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from rq import SimpleWorker
from sqlalchemy import func, select

from app.analysis import llm as llm_mod
from app.analysis.llm import AnthropicScorer, LLMError, LLMUsage, build_user_prompt
from app.analysis.scoring import Candidate, Evaluation, score, technical_score, validate
from app.analysis.scoring import select as select_candidates
from app.analysis.windows import Seg, Window, build_windows
from app.core.config import Settings
from app.db.session import get_sessionmaker
from app.models import ClipCandidate, UsageLedger
from app.services.queue import get_queue, get_redis
from tests.helpers import projeto_pronto


def segs_regulares(n=40, dur=5.0, gap=0.2):
    out = []
    t = 0.0
    for i in range(n):
        text = f"Frase número {i}." if i % 2 else f"Parte {i} da fala"
        out.append(Seg(i, round(t, 3), round(t + dur - gap, 3), text, 0.9))
        t += dur
    return out


def win(wid, start, end, ratio=1.0, conf=0.9):
    return Window(wid, 0, 1, start, end, "texto", ratio, conf)


# ---------------------------------------------------------------- janelas

def test_janelas_respeitam_duracao_e_limites_de_segmento():
    segs = segs_regulares()
    ws = build_windows(segs, 15, 60, 500)
    assert ws
    starts = {s.start for s in segs}
    ends = {s.end for s in segs}
    for w in ws:
        assert 15 <= w.duration <= 60
        assert w.start in starts and w.end in ends
    assert len({w.id for w in ws}) == len(ws)
    # preferência por terminar em fim de frase
    assert sum(w.text.endswith(".") for w in ws) / len(ws) > 0.8


def test_janelas_sem_quase_duplicatas_e_limite():
    ws = build_windows(segs_regulares(400), 15, 60, 50)
    assert len(ws) == 50
    assert ws[-1].start > 1500  # amostra cobre o vídeo inteiro, não só o começo


def test_fala_curta_demais_nao_gera_janelas():
    assert build_windows([Seg(0, 0, 5, "Oi."), Seg(1, 6, 12, "Tchau.")], 15, 60, 10) == []


def test_segmento_mais_longo_que_o_maximo_e_ignorado():
    ws = build_windows([Seg(0, 0, 70, "Longo."), Seg(1, 70, 90, "Depois.")], 15, 60, 10)
    assert [(w.start, w.end) for w in ws] == [(70, 90)]


# ---------------------------------------------------------------- validação, nota e seleção

def item(wid, **kw):
    base = {"window_id": wid, "hook": 5, "context": 5, "emotion": 5, "relevance": 5, "retention": 5,
            "title": "Título", "justification": "Porque sim."}
    return {**base, **kw}


def test_validacao_descarta_itens_invalidos():
    windows = {0: win(0, 0, 20), 1: win(1, 30, 50), 2: win(2, 60, 80)}
    raw = [item(0), item(0), item(9), item(1, hook=11), item(2, title="  "), {"window_id": 2},
           item(2, title="x" * 300, justification="  ok  ")]
    out = validate(raw, windows)
    assert [e.window_id for e in out] == [0, 2]
    assert len(out[1].title) == 120 and out[1].justification == "ok"


def test_pontuacao_e_qualidade_tecnica():
    w = win(0, 0, 30, ratio=0.9, conf=0.8)
    assert technical_score(w) == 9.0
    c = score(Evaluation(0, {"hook": 10, "context": 10, "emotion": 10, "relevance": 10, "retention": 10}, "t", "j"), w)
    assert c.score == round((0.9 * 10 + 0.1 * 9.0) * 10)
    zero = score(Evaluation(0, dict.fromkeys(("hook", "context", "emotion", "relevance", "retention"), 0), "t", "j"),
                 win(0, 0, 30, ratio=0, conf=0))
    assert zero.score == 0


def test_selecao_sem_sobreposicao_limitada_e_valida():
    cands = [Candidate(win(i, i * 10, i * 10 + 25), {}, 100 - i, "t", "j") for i in range(30)]
    cands.append(Candidate(win(99, 400, 470), {}, 100, "longo", "j"))  # 70 s: inválido
    cands.append(Candidate(win(98, 580, 610), {}, 100, "fora", "j"))  # além da duração
    chosen = select_candidates(cands, duration=500, min_s=15, max_s=60, limit=10)
    assert len(chosen) == 10
    assert all(15 <= c.window.duration <= 60 and c.window.end <= 500 for c in chosen)
    spans = sorted((c.window.start, c.window.end) for c in chosen)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    assert [c.score for c in chosen] == sorted((c.score for c in chosen), reverse=True)
    assert sum(c.selected for c in cands) == 10


def test_prompt_delimita_transcricao_como_dado():
    p = build_user_prompt([win(3, 0, 20)])
    assert '<trecho window_id="3"' in p and "<trechos>" in p
    assert "nunca instruções" in llm_mod.SYSTEM_PROMPT


# ---------------------------------------------------------------- adaptador Anthropic (contrato HTTP local)

class _Api(BaseHTTPRequestHandler):
    reply: dict = {}
    status = 200
    seen: dict = {}

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        _Api.seen = {"path": self.path, "headers": {k.lower(): v for k, v in self.headers.items()}, "body": body}
        self.send_response(_Api.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("x-should-retry", "false")
        self.send_header("request-id", "req_teste")
        self.end_headers()
        self.wfile.write(json.dumps(_Api.reply).encode())

    def log_message(self, *a):
        pass


def message(text, stop="end_turn"):
    return {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": text}], "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 1200, "output_tokens": 300}}


@pytest.fixture
def api():
    srv = HTTPServer(("127.0.0.1", 0), _Api)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()


def scorer(api, **kw):
    return AnthropicScorer(Settings(anthropic_api_key="sk-ant-teste", anthropic_base_url=api, **kw))


def test_contrato_requisicao_estruturada_com_fallback(api):
    _Api.status = 200
    _Api.reply = message(json.dumps({"evaluations": [item(0)]}))
    items, usage = scorer(api).evaluate([win(0, 0, 20)])
    seen = _Api.seen
    assert seen["path"].startswith("/v1/messages")
    h = seen["headers"]
    assert h.get("x-api-key") == "sk-ant-teste" or h.get("authorization") == "Bearer sk-ant-teste"
    assert "server-side-fallback-2026-07-01" in seen["headers"]["anthropic-beta"]
    b = seen["body"]
    assert b["model"] == "claude-opus-5-5"
    assert b["fallbacks"] == "default"
    assert b["output_config"]["effort"] == "medium"
    assert b["output_config"]["format"]["type"] == "json_schema"
    assert b["output_config"]["format"]["schema"]["required"] == ["evaluations"]
    assert "thinking" not in b and "temperature" not in b
    assert '<trecho window_id="0"' in b["messages"][0]["content"]
    assert items == [item(0)]
    assert (usage.input_tokens, usage.output_tokens) == (1200, 300)


def test_contrato_sem_fallback(api):
    _Api.status = 200
    _Api.reply = message(json.dumps({"evaluations": []}))
    scorer(api, llm_fallbacks="off").evaluate([win(0, 0, 20)])
    assert "fallbacks" not in _Api.seen["body"]
    assert "server-side-fallback" not in _Api.seen["headers"].get("anthropic-beta", "")


@pytest.mark.parametrize("status,reply,code", [
    (200, message("", stop="refusal"), "analysis_refused"),
    (200, message('{"evaluations": [', stop="max_tokens"), "analysis_failed"),
    (200, message("isto não é json"), "analysis_failed"),
    (200, message('{"outra": 1}'), "analysis_failed"),
    (401, {"type": "error", "error": {"type": "authentication_error", "message": "x"}}, "analysis_misconfigured"),
    (429, {"type": "error", "error": {"type": "rate_limit_error", "message": "x"}}, "analysis_unavailable"),
    (529, {"type": "error", "error": {"type": "overloaded_error", "message": "x"}}, "analysis_unavailable"),
    (400, {"type": "error", "error": {"type": "invalid_request_error", "message": "x"}}, "analysis_failed"),
])
def test_contrato_erros(api, status, reply, code):
    _Api.status, _Api.reply = status, reply
    with pytest.raises(LLMError) as e:
        scorer(api).evaluate([win(0, 0, 20)])
    assert e.value.code == code
    assert "sk-ant-teste" not in (e.value.detail or "")


def test_sem_chave_configurada():
    with pytest.raises(LLMError) as e:
        llm_mod.get_scorer(Settings(anthropic_api_key=None))
    assert e.value.code == "analysis_misconfigured"


# ---------------------------------------------------------------- pipeline (transcrição real + avaliador de teste)

class FakeScorer:
    """Dublê de teste: notas determinísticas a partir do texto. Não representa a qualidade do modelo real."""
    model_name = "fake-scorer"

    def __init__(self, fail=None, on_call=None):
        self.calls = 0
        self.fail = fail
        self.on_call = on_call

    def evaluate(self, windows):
        self.calls += 1
        if self.on_call:
            self.on_call()
        if self.fail:
            raise self.fail
        out = []
        for w in windows:
            t = w.text.lower()
            hook = 9 if "?" in t else 4
            out.append(item(w.id, hook=hook, relevance=8 if "segredo" in t or "dica" in t else 5,
                            title=f"Trecho {w.id}", justification="Avaliação de teste."))
        return out, LLMUsage(input_tokens=1000, output_tokens=200, requests=1, models={"fake"})


def usar_scorer(monkeypatch, fake):
    monkeypatch.setattr(llm_mod, "get_scorer", lambda s: fake)


def rodar(client, user, pid):
    r = client.post(f"/api/v1/projects/{pid}/process", headers=user.headers)
    assert r.status_code == 202, r.text
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)
    return client.get(f"/api/v1/projects/{pid}/status", headers=user.headers).json()


def candidatos(pid):
    with get_sessionmaker()() as db:
        return db.scalars(select(ClipCandidate).where(ClipCandidate.project_id == uuid.UUID(pid))).all()


def test_pipeline_gera_candidatos_validos(client, alice, long_speech_video, whisper_ready, monkeypatch):
    fake = FakeScorer()
    usar_scorer(monkeypatch, fake)
    pid = projeto_pronto(client, alice, long_speech_video)
    st = rodar(client, alice, pid)
    assert st["status"] == "completed", st
    assert st["job"]["stage"] == "done"
    r = client.get(f"/api/v1/projects/{pid}/candidates", headers=alice.headers).json()
    items = r["items"]
    sel = [c for c in items if c["selected"]]
    assert 1 <= len(sel) <= 10 and len(items) >= len(sel)
    for c in items:
        assert 15 - 0.01 <= c["end_seconds"] - c["start_seconds"] <= 60 + 0.01
        assert 0 <= c["start_seconds"] and c["end_seconds"] <= 90
        assert 0 <= c["score"] <= 100 and c["title"] and c["justification"]
        assert set(c["criteria"]) >= {"hook", "context", "emotion", "relevance", "retention", "technical"}
        assert c["model_name"] == "fake-scorer"
    spans = sorted((c["start_seconds"], c["end_seconds"]) for c in sel)
    assert all(a[1] <= b[0] for a, b in zip(spans, spans[1:], strict=False))
    # o melhor candidato começa com a pergunta (gancho)
    assert items[0]["score"] >= max(c["score"] for c in items)
    # filtros e isolamento
    only = client.get(f"/api/v1/projects/{pid}/candidates?only_selected=true", headers=alice.headers).json()
    assert len(only["items"]) == len(sel)
    # consumo registrado uma vez para o job
    with get_sessionmaker()() as db:
        led = db.scalars(select(UsageLedger).where(UsageLedger.project_id == uuid.UUID(pid),
                                                   UsageLedger.kind == "llm_tokens")).all()
    assert len(led) == 1 and int(led[0].quantity) == 1200 * fake.calls
    assert float(led[0].estimated_cost_usd) == pytest.approx(fake.calls * (1000 * 4 + 200 * 20) / 1e6)


def test_reprocessar_substitui_candidatos(client, alice, long_speech_video, whisper_ready, monkeypatch):
    from app.pipeline import stages
    from app.pipeline.context import PipelineError

    usar_scorer(monkeypatch, FakeScorer())
    real_render = stages.render

    def falha(ctx):
        raise PipelineError("render_failed", "Falha simulada na renderização.")

    monkeypatch.setattr(stages, "render", falha)
    pid = projeto_pronto(client, alice, long_speech_video)
    assert rodar(client, alice, pid)["error_code"] == "render_failed"
    n = len(candidatos(pid))
    monkeypatch.setattr(stages, "render", real_render)
    assert rodar(client, alice, pid)["status"] == "completed"
    # a transcrição é refeita e pode variar levemente; o que importa é que os candidatos foram SUBSTITUÍDOS
    from app.models import ProcessingJob
    with get_sessionmaker()() as db:
        last = db.scalar(select(ProcessingJob.started_at).where(ProcessingJob.project_id == uuid.UUID(pid))
                         .order_by(ProcessingJob.created_at.desc()).limit(1))
    cands = candidatos(pid)
    assert n and cands and all(c.created_at >= last for c in cands)
    assert len({(c.start_seconds, c.end_seconds) for c in cands}) == len(cands)
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(UsageLedger)
                         .where(UsageLedger.project_id == uuid.UUID(pid), UsageLedger.kind == "llm_tokens")) == 2


def test_isolamento_candidatos(client, alice, bob, long_speech_video):
    pid = projeto_pronto(client, alice, long_speech_video)
    assert client.get(f"/api/v1/projects/{pid}/candidates", headers=bob.headers).status_code == 404


def test_fala_insuficiente(client, alice, speech_video, whisper_ready, monkeypatch):
    fake = FakeScorer()
    usar_scorer(monkeypatch, fake)
    pid = projeto_pronto(client, alice, speech_video)  # ~14 s de fala
    st = rodar(client, alice, pid)
    assert st["error_code"] == "no_candidates" and fake.calls == 0


def test_servico_indisponivel_nao_consome_tentativa(client, alice, long_speech_video, whisper_ready, monkeypatch):
    usar_scorer(monkeypatch, FakeScorer(fail=LLMError("analysis_unavailable", "Indisponível.", "HTTP 529")))
    pid = projeto_pronto(client, alice, long_speech_video)
    for _ in range(4):
        assert rodar(client, alice, pid)["error_code"] == "analysis_unavailable"
    assert candidatos(pid) == []


def test_recusa_e_registrada(client, alice, long_speech_video, whisper_ready, monkeypatch):
    usar_scorer(monkeypatch, FakeScorer(fail=LLMError("analysis_refused", "Conteúdo não analisado.", "refusal")))
    pid = projeto_pronto(client, alice, long_speech_video)
    st = rodar(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "analysis_refused"


def test_cancelar_durante_analise(client, alice, long_speech_video, whisper_ready, monkeypatch):
    from app.models import Project
    from app.services.projects import cancel_project

    def cancela():
        with get_sessionmaker()() as db:
            cancel_project(db, db.scalar(select(Project).where(Project.status == "processing")))

    usar_scorer(monkeypatch, FakeScorer(on_call=cancela))
    pid = projeto_pronto(client, alice, long_speech_video)
    st = rodar(client, alice, pid)
    assert st["status"] == "canceled"
    assert candidatos(pid) == []
