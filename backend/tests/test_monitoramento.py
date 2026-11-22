"""Beta fechado — Lote 5: prontidão, alertas, relatório diário, relatórios do navegador e avaliação dos cortes."""

import json
import logging
import threading
import uuid
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import SecretStr
from rq import SimpleWorker
from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.models import Clip, UsageLedger
from app.services import alerts
from app.services.queue import get_queue, get_redis
from tests.helpers import projeto_pronto
from tests.media import make_video


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("mon") / "v.mp4", 20)


class _Hook(BaseHTTPRequestHandler):
    recebidos: list = []

    def do_POST(self):
        _Hook.recebidos.append(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))
        self.send_response(204)
        self.end_headers()

    def log_message(self, *a):
        pass


@pytest.fixture
def webhook(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Hook)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Hook.recebidos = []
    monkeypatch.setattr(get_settings(), "alert_webhook_url", SecretStr(f"http://127.0.0.1:{srv.server_port}/hook"))
    yield _Hook.recebidos
    srv.shutdown()


@pytest.fixture
def worker_vivo():
    w = SimpleWorker([get_queue()], connection=get_redis())
    w.register_birth()
    yield w
    w.register_death()


def falhas_recentes(n: int, owner) -> None:
    with get_sessionmaker()() as db:
        from app.models import Profile, Project
        if db.get(Profile, owner) is None:
            db.add(Profile(id=owner, email="x@example.com"))
            db.flush()
        for _ in range(n):
            p = Project(owner_id=owner, title="p", status="failed")
            db.add(p)
            db.flush()
            db.execute(text("INSERT INTO processing_jobs (project_id, owner_id, job_type, status, idempotency_key, "
                            "error_code, finished_at) VALUES (:p, :o, 'process_project', 'failed', :k, "
                            "'internal_error', now())"), {"p": p.id, "o": owner, "k": str(uuid.uuid4())})
        db.commit()


# =============================================================== prontidão

def test_prontidao_com_tudo_no_ar(client, worker_vivo):
    r = client.get("/health/ready")
    assert r.status_code == 200, r.json()
    d = r.json()
    assert d["pronto"] and d["banco"] and d["redis"] and d["armazenamento"] and d["workers_vivos"] >= 1


def test_prontidao_sem_worker(client):
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["workers_vivos"] == 0


def test_prontidao_com_redis_ou_armazenamento_fora(client, worker_vivo, monkeypatch):
    import app.services.queue as q
    from app.services.storage import get_storage

    def quebrado(**kw):
        raise ConnectionError("fora")

    monkeypatch.setattr(get_storage()._internal, "head_bucket", quebrado)
    d = client.get("/health/ready").json()
    assert d["armazenamento"] is False and d["pronto"] is False
    monkeypatch.undo()
    monkeypatch.setattr(q, "get_redis", lambda: (_ for _ in ()).throw(ConnectionError("fora")))
    r = client.get("/health/ready")
    assert r.status_code == 503 and r.json()["redis"] is False


# =============================================================== alertas

def test_alertas_avaliados(client, alice, video, monkeypatch):
    s = get_settings()
    falhas_recentes(3, alice.id)
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET created_at = now() - interval '20 minutes' "
                          "WHERE status='queued'"))
    monkeypatch.setattr(s, "llm_budget_total_usd", 1.0)
    with get_sessionmaker()() as db:
        db.add(UsageLedger(owner_id=alice.id, kind="llm_tokens", quantity=1, estimated_cost_usd=0.85,
                           idempotency_key=f"t:{uuid.uuid4()}"))
        db.commit()
        chaves = {a.key for a in alerts.evaluate(db, s)}
    assert {"falhas", "fila", "workers", "custo"} <= chaves


def test_sem_problemas_sem_alertas(worker_vivo):
    with get_sessionmaker()() as db:
        assert alerts.evaluate(db, get_settings()) == []


@pytest.mark.parametrize("formato,campo", [("slack", "text"), ("discord", "content"), ("json", "alerts")])
def test_formatos_do_webhook_e_sem_repeticao(webhook, monkeypatch, formato, campo):
    monkeypatch.setattr(get_settings(), "alert_webhook_format", formato)
    a = [alerts.Alert(f"teste-{formato}", "Mensagem de teste")]
    assert alerts.send(get_settings(), a) == a
    assert alerts.send(get_settings(), a) == []  # repetido dentro da janela: não reenvia
    assert len(webhook) == 1 and campo in webhook[0]
    assert "Mensagem de teste" in json.dumps(webhook[0], ensure_ascii=False)


def test_sem_redis_controle_em_memoria(webhook, monkeypatch):
    import app.services.queue as q

    monkeypatch.setattr(q, "get_redis", lambda: (_ for _ in ()).throw(ConnectionError("fora")))
    a = [alerts.Alert("memoria", "x")]
    assert alerts.send(get_settings(), a) == a and alerts.send(get_settings(), a) == []


def test_ciclo_da_manutencao_envia_alertas_e_relatorio_diario(client, alice, video, webhook, worker_vivo):
    from app.worker.maintenance import ciclo

    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    with get_engine().begin() as conn:  # worker morto no meio do job
        conn.execute(text("UPDATE projects SET status='processing' WHERE id=:p"), {"p": pid})
        conn.execute(text("UPDATE processing_jobs SET status='running', updated_at = now() - interval '1 day' "
                          "WHERE project_id=:p"), {"p": pid})
    r = ciclo(get_settings())
    assert "worker_lost" in r["alertas_enviados"] and r["relatorio_enviado"] is True
    textos = [m["text"] for m in webhook]
    assert any("sem sinal de vida" in t for t in textos)
    assert any(t.startswith("📊 Relatório AutoClip IA") for t in textos)
    assert "@" not in next(t for t in textos if t.startswith("📊"))  # relatório sem e-mails
    r2 = ciclo(get_settings())
    assert r2["relatorio_enviado"] is False and r2["alertas_enviados"] == []


# =============================================================== relatórios do navegador

def test_erro_do_navegador_registrado_sem_dados_sensiveis(client, caplog):
    caplog.set_level(logging.INFO, logger="autoclip.client")
    r = client.post("/api/v1/client-errors", json={
        "message": "falhou com Bearer eyJabc.eyJdef.ghi", "url": "https://app/projetos/1?token=segredo#x",
        "stack": "Error: x\n at f (app.js:1)"})
    assert r.status_code == 204
    rec = next(x for x in caplog.records if x.name == "autoclip.client")
    f = rec.extra_fields
    assert f["tipo"] == "client_error" and f["pagina"] == "/projetos/1" and "eyJabc" not in f["mensagem"]
    assert "segredo" not in json.dumps(f)


@pytest.mark.parametrize("corpo", [
    {"csp-report": {"violated-directive": "img-src", "blocked-uri": "https://evil.example/x.png?t=1",
                    "document-uri": "https://app/painel?a=b"}},
    [{"type": "csp-violation", "body": {"effectiveDirective": "connect-src", "blockedURL": "https://evil.example/api",
                                        "documentURL": "https://app/painel"}}],
])
def test_relatorio_de_csp(client, caplog, corpo):
    caplog.set_level(logging.INFO, logger="autoclip.client")
    r = client.post("/api/v1/csp-report", content=json.dumps(corpo),
                    headers={"Content-Type": "application/csp-report"})
    assert r.status_code == 204
    f = next(x for x in caplog.records if x.name == "autoclip.client").extra_fields
    assert f["tipo"] == "csp_violation" and f["origem_bloqueada"] == "evil.example" and f["pagina"] == "/painel"


def test_limites_dos_relatorios_do_navegador(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "client_errors_per_minute", 3)
    codes = [client.post("/api/v1/client-errors", json={"message": "x"}).status_code for _ in range(4)]
    assert codes == [204, 204, 204, 429]
    get_redis().flushdb()
    assert client.post("/api/v1/client-errors", content=b"x" * 9000).status_code == 413


# =============================================================== avaliação dos cortes

def test_avaliacao_dos_cortes(client, alice, bob, video, capsys):
    from app import admin

    pid = projeto_pronto(client, alice, video)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE projects SET status='completed' WHERE id=:p"), {"p": pid})
        clips = [Clip(project_id=uuid.UUID(pid), owner_id=alice.id, title=f"C{i}", score=s, start_seconds=0,
                      end_seconds=16, status="ready") for i, s in enumerate((80, 40))]
        db.add_all(clips)
        db.commit()
        c1, c2 = (str(c.id) for c in clips)
    assert client.get(f"/api/v1/clips/{c1}", headers=alice.headers).json()["feedback"] is None
    assert client.post(f"/api/v1/clips/{c1}/feedback", headers=alice.headers, json={"util": False}).json()["feedback"] \
        is False
    assert client.post(f"/api/v1/clips/{c1}/feedback", headers=alice.headers, json={"util": True}).json()["feedback"]
    client.post(f"/api/v1/clips/{c2}/feedback", headers=alice.headers, json={"util": False})
    assert client.get(f"/api/v1/clips/{c1}", headers=alice.headers).json()["feedback"] is True  # a última vale
    assert client.post(f"/api/v1/clips/{c1}/feedback", headers=bob.headers, json={"util": True}).status_code == 404
    capsys.readouterr()
    admin.main(["relatorio", "--json"])
    rep = json.loads(capsys.readouterr().out)
    assert rep["avaliacoes_dos_cortes"] == {"util": 1, "nao_util": 1, "nota_media_util": 80, "nota_media_nao_util": 40}


# =============================================================== endurecimento

def test_documentacao_desligada_em_producao(monkeypatch):
    from app.main import create_app

    monkeypatch.setattr(get_settings(), "environment", "production")
    with TestClient(create_app()) as c:
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
        assert c.get("/health").status_code == 200


def test_cabecalhos_de_seguranca_na_api(client):
    r = client.get("/health")
    assert r.headers["X-Content-Type-Options"] == "nosniff" and r.headers["Cache-Control"] == "no-store"
