"""Beta fechado — Lote 4: convites, limites, reserva de custo de IA e administração por linha de comando."""

import json
import threading
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from rq import SimpleWorker
from sqlalchemy import func, select, text

from app import admin
from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.models import AuditEvent, Clip, ProcessingJob, Subscription, UsageLedger
from app.pipeline import stages as stages_mod
from app.pipeline.stages import Stage, prepare_source
from app.services import invites
from app.services.queue import get_queue, get_redis
from tests.conftest import make_token
from tests.helpers import projeto_pronto
from tests.media import make_video


@pytest.fixture(autouse=True)
def limpar_cache_de_convites():
    invites.invalidate_cache()
    yield
    invites.invalidate_cache()


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("beta") / "v.mp4", 20)


@pytest.fixture
def beta(monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "beta_mode", True)
    monkeypatch.setattr(s, "beta_allowed_emails", "alice@example.com")
    return s


def headers(email: str, sub=None) -> dict:
    return {"Authorization": f"Bearer {make_token(sub or uuid.uuid4(), email=email)}"}


def cli(*args, capsys=None) -> str:
    if capsys:
        capsys.readouterr()  # descarta saídas anteriores
    assert admin.main(list(args)) == 0
    return capsys.readouterr().out if capsys else ""


def pipeline_ok(monkeypatch):
    monkeypatch.setattr(stages_mod, "get_pipeline", lambda: [Stage("prepare", "preparação", 5, prepare_source)])


def worker():
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)


def processar(client, user, pid):
    return client.post(f"/api/v1/projects/{pid}/process", headers=user.headers)


# =============================================================== convites

def test_beta_desligado_todos_entram(client, bob):
    assert client.get("/api/v1/me", headers=bob.headers).status_code == 200


def test_somente_convidados_usam_a_api(client, alice, bob, beta):
    assert client.get("/api/v1/me", headers=alice.headers).status_code == 200
    r = client.get("/api/v1/me", headers=bob.headers)
    assert r.status_code == 403 and "beta fechado" in r.json()["detail"]
    assert client.post("/api/v1/projects", json={"title": "x", "rights_confirmed": True},
                       headers=bob.headers).status_code == 403
    assert client.get("/api/v1/me", headers=headers("ALICE@Example.COM")).status_code == 200  # sem diferenciar caixa


def test_token_sem_email_e_recusado_no_beta(client, beta):
    tok = make_token(uuid.uuid4(), email=None)
    assert client.get("/api/v1/me", headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_dominio_inteiro(client, bob, beta, monkeypatch):
    monkeypatch.setattr(beta, "beta_allowed_emails", "@example.com")
    assert client.get("/api/v1/me", headers=bob.headers).status_code == 200
    assert client.get("/api/v1/me", headers=headers("x@outro.com")).status_code == 403


def test_convidar_e_revogar_pelo_comando(client, alice, bob, beta, capsys):
    assert client.get("/api/v1/me", headers=bob.headers).status_code == 403
    out = cli("convidar", "Bob@Example.com", capsys=capsys)
    assert "bob@example.com" in out
    assert client.get("/api/v1/me", headers=bob.headers).status_code == 200
    cli("revogar", "bob@example.com")
    assert client.get("/api/v1/me", headers=bob.headers).status_code == 403  # vale mesmo com perfil existente
    cli("revogar", "alice@example.com")  # revogar também vale para quem está na configuração
    assert client.get("/api/v1/me", headers=alice.headers).status_code == 403
    cli("convidar", "alice@example.com")
    assert client.get("/api/v1/me", headers=alice.headers).status_code == 200
    out = cli("convites", capsys=capsys)
    assert "ATIVO" in out and "alice@example.com" in out and "bob@example.com" in out and "REVOGADO" in out


class _Invite(BaseHTTPRequestHandler):
    seen: list = []

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Invite.seen.append((self.path, self.headers.get("apikey"), json.loads(body)))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def test_convite_por_email_do_supabase(monkeypatch, capsys):
    from pydantic import SecretStr

    s = get_settings()
    out = cli("convidar", "novo@example.com", "--enviar-email", capsys=capsys)
    assert "NÃO enviado" in out  # sem chave de serviço
    srv = HTTPServer(("127.0.0.1", 0), _Invite)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setattr(s, "supabase_admin_url", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(s, "supabase_service_role_key", SecretStr("srv"))
    monkeypatch.setattr(s, "frontend_url", "https://beta.exemplo.com")
    out = cli("convidar", "outro@example.com", "--enviar-email", capsys=capsys)
    srv.shutdown()
    assert "enviado pelo Supabase" in out
    path, key, body = _Invite.seen[-1]
    assert path.startswith("/auth/v1/invite?redirect_to=https%3A%2F%2Fbeta.exemplo.com%2Fredefinir-senha")
    assert key == "srv" and body == {"email": "outro@example.com"}


# =============================================================== limites

def test_periodo_do_beta_atravessa_a_virada_do_mes(client, alice, video, capsys):
    client.get("/api/v1/me", headers=alice.headers)
    inicio = (datetime.now(UTC) - timedelta(days=1)).date().isoformat()
    fim = (datetime.now(UTC) + timedelta(days=12)).date().isoformat()
    assert "1 assinatura" in cli("periodo", "alice@example.com", "--inicio", inicio, "--fim", fim, capsys=capsys)
    with get_sessionmaker()() as db:
        # consumo antes do período (não conta) e dentro do período (conta)
        antigo = datetime.now(UTC) - timedelta(days=3)
        db.add(UsageLedger(owner_id=alice.id, kind="processed_seconds", quantity=600, estimated_cost_usd=0,
                           idempotency_key=f"t:{uuid.uuid4()}", created_at=antigo))
        db.add(UsageLedger(owner_id=alice.id, kind="processed_seconds", quantity=120, estimated_cost_usd=0,
                           idempotency_key=f"t:{uuid.uuid4()}"))
        db.commit()
    u = client.get("/api/v1/me", headers=alice.headers).json()["usage"]
    assert u["period_start"].startswith(inicio) and u["used_minutes"] == 2.0
    cli("cota", "alice@example.com", "--minutos", "2")
    pid = projeto_pronto(client, alice, video)
    r = processar(client, alice, pid)
    assert r.status_code == 402 and "cota do período" in r.json()["detail"]


def test_um_processamento_por_vez(client, alice, video, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_active_jobs_per_user", 1)
    pipeline_ok(monkeypatch)
    p1, p2 = projeto_pronto(client, alice, video), projeto_pronto(client, alice, video)
    assert processar(client, alice, p1).status_code == 202
    r = processar(client, alice, p2)
    assert r.status_code == 429 and r.json()["code"] == "concurrent_limit"
    worker()
    assert processar(client, alice, p2).status_code == 202


def test_limite_de_regeneracoes_por_dia(client, alice, video, monkeypatch):
    monkeypatch.setattr(get_settings(), "max_regenerations_per_day", 2)
    pid = projeto_pronto(client, alice, video)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE projects SET status='completed' WHERE id=:p"), {"p": pid})
        clips = [Clip(project_id=uuid.UUID(pid), owner_id=alice.id, title=f"C{i}", score=50, start_seconds=0,
                      end_seconds=16, status="ready") for i in range(3)]
        db.add_all(clips)
        db.commit()
        ids = [c.id for c in clips]
    codes = [client.post(f"/api/v1/clips/{i}/regenerate", headers=alice.headers, json={}).status_code for i in ids]
    assert codes == [202, 202, 429]
    r = client.post(f"/api/v1/clips/{ids[2]}/regenerate", headers=alice.headers, json={})
    assert r.json()["code"] == "regeneration_limit"


def test_reserva_de_custo_por_usuario(client, alice, video, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_reserve_usd_per_minute", 0.015)  # 20 s => US$ 0,005 reservados
    monkeypatch.setattr(s, "llm_budget_per_user_usd", 0.009)
    pipeline_ok(monkeypatch)
    p1, p2, p3 = (projeto_pronto(client, alice, video) for _ in range(3))
    assert processar(client, alice, p1).status_code == 202
    r = processar(client, alice, p2)  # 0,005 reservado + 0,005 > 0,009
    assert r.status_code == 402 and r.json()["code"] == "cost_limit"
    u = client.get("/api/v1/me", headers=alice.headers).json()["usage"]
    assert u["llm_reserved_usd"] == pytest.approx(0.005, abs=1e-4) and u["llm_budget_usd"] == 0.009
    worker()  # terminou sem gasto real de IA (pipeline mínimo): a reserva é liberada
    assert processar(client, alice, p2).status_code == 202
    worker()
    with get_sessionmaker()() as db:  # custo real medido entra no lugar da reserva
        db.add(UsageLedger(owner_id=alice.id, kind="llm_tokens", quantity=1000, estimated_cost_usd=0.006,
                           idempotency_key=f"llm_tokens:{uuid.uuid4()}"))
        db.commit()
    assert processar(client, alice, p3).json()["code"] == "cost_limit"  # 0,006 + 0,005 > 0,009


def test_teto_global_do_beta(client, alice, bob, video, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "llm_reserve_usd_per_minute", 0.015)
    monkeypatch.setattr(s, "llm_budget_total_usd", 0.008)
    pa, pb = projeto_pronto(client, alice, video), projeto_pronto(client, bob, video)
    assert processar(client, alice, pa).status_code == 202
    r = processar(client, bob, pb)
    assert r.status_code == 402 and r.json()["code"] == "beta_budget_exhausted"


def test_teto_global_com_pedidos_simultaneos(client, alice, bob, video, monkeypatch):
    """Dois usuários diferentes disputam o último saldo do teto global: exatamente um passa."""
    s = get_settings()
    monkeypatch.setattr(s, "llm_reserve_usd_per_minute", 0.015)
    monkeypatch.setattr(s, "llm_budget_total_usd", 0.008)
    pa, pb = projeto_pronto(client, alice, video), projeto_pronto(client, bob, video)
    barreira = threading.Barrier(2)
    codes = []

    def pedir(user, pid):
        barreira.wait()
        codes.append(processar(client, user, pid).status_code)

    ts = [threading.Thread(target=pedir, args=a) for a in ((alice, pa), (bob, pb))]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sorted(codes) == [202, 402]


# =============================================================== administração

def test_comandos_de_administracao(client, alice, video, monkeypatch, capsys):
    pipeline_ok(monkeypatch)
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    worker()
    out = cli("usuarios", capsys=capsys)
    assert "alice@example.com" in out and "active" in out
    cli("suspender", "alice@example.com")
    assert client.post("/api/v1/projects", json={"title": "x", "rights_confirmed": True},
                       headers=alice.headers).status_code == 403
    cli("reativar", "alice@example.com")
    assert client.post("/api/v1/projects", json={"title": "x", "rights_confirmed": True},
                       headers=alice.headers).status_code == 201
    cli("cota", "alice@example.com", "--minutos", "180", "--projetos-dia", "5")
    u = client.get("/api/v1/me", headers=alice.headers).json()["usage"]
    assert u["quota_minutes"] == 180 and u["projects_per_day"] == 5
    rep = json.loads(cli("relatorio", "--json", capsys=capsys))
    assert rep["projetos"]["concluidos"] == 1 and rep["minutos_processados"] == pytest.approx(0.33, abs=0.05)
    assert rep["fila"]["no_banco_na_fila"] == 0 and isinstance(rep["fila"]["workers_vivos"], int)
    assert "Relatório AutoClip IA" in cli("relatorio", capsys=capsys)
    with get_sessionmaker()() as db:
        acoes = set(db.scalars(select(AuditEvent.action).where(AuditEvent.action.like("admin.%"))))
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "admin.suspender"))
    assert {"admin.suspender", "admin.reativar", "admin.cota"} <= acoes and ev.details["operador"]


def test_usuario_inexistente_no_comando(capsys):
    with pytest.raises(SystemExit) as e:
        admin.main(["suspender", "ninguem@example.com"])
    assert "não encontrado" in str(e.value)


def test_relatorio_conta_falhas_por_codigo(client, alice, video, monkeypatch, capsys):
    pid = projeto_pronto(client, alice, video)
    monkeypatch.setattr(stages_mod, "get_pipeline",
                        lambda: [Stage("x", "x", 5, lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))])
    processar(client, alice, pid)
    worker()
    rep = json.loads(cli("relatorio", "--json", capsys=capsys))
    assert rep["projetos"]["falhas_por_codigo"] == {"internal_error": 1}


def test_ajustes_ficam_restritos_ao_usuario(client, alice, bob, capsys):
    client.get("/api/v1/me", headers=alice.headers)
    client.get("/api/v1/me", headers=bob.headers)
    cli("cota", "alice@example.com", "--minutos", "1")
    with get_sessionmaker()() as db:
        quotas = dict(db.execute(select(Subscription.owner_id, Subscription.monthly_minutes_quota)).all())
    assert quotas[alice.id] == 1 and quotas[bob.id] == get_settings().plan_free_monthly_minutes
    with get_engine().begin() as conn:
        assert conn.execute(text("SELECT count(*) FROM processing_jobs")).scalar() == 0
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(ProcessingJob)) == 0
