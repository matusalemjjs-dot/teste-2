"""Beta fechado — Lote 3: retenção automática e exclusão de conta (LGPD)."""

import json
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest
from rq import SimpleWorker
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.models import (
    AuditEvent,
    Clip,
    ClipCandidate,
    MediaAsset,
    ProcessingJob,
    Profile,
    Project,
    Subscription,
    TranscriptSegment,
    UsageLedger,
)
from app.pipeline import stages as stages_mod
from app.pipeline.stages import Stage, prepare_source
from app.services.queue import get_queue, get_redis
from app.services.storage import Storage, StorageError, get_storage, project_prefix
from app.worker.maintenance import run_retention_locked
from tests.helpers import criar, projeto_pronto
from tests.media import make_video


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("ret") / "v.mp4", 20)


def concluir(client, user, video, monkeypatch, *, dias_atras: float = 0) -> str:
    """Projeto concluído de verdade (pipeline mínimo) + transcrição/candidato/corte simulados no banco."""
    pid = projeto_pronto(client, user, video)
    with monkeypatch.context() as m:  # só durante o processamento (não desfaz outros ajustes do teste)
        m.setattr(stages_mod, "get_pipeline", lambda: [Stage("prepare", "preparação", 5, prepare_source)])
        assert client.post(f"/api/v1/projects/{pid}/process", headers=user.headers).status_code == 202
        SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)
    p = uuid.UUID(pid)
    storage = get_storage()
    with get_sessionmaker()() as db:
        db.add(TranscriptSegment(project_id=p, seq=0, start_seconds=0, end_seconds=5, text="olá"))
        cand = ClipCandidate(project_id=p, start_seconds=0, end_seconds=16, score=70, title="T", justification="J")
        db.add(cand)
        db.flush()
        key = f"{project_prefix(user.id, p)}clips/x/v1.mp4"
        storage._internal.put_object(Bucket=storage.bucket, Key=key, Body=b"corte")
        asset = MediaAsset(project_id=p, owner_id=user.id, kind="clip_video", status="ready", storage_key=key)
        db.add(asset)
        db.flush()
        db.add(Clip(project_id=p, owner_id=user.id, candidate_id=cand.id, title="T", score=70, start_seconds=0,
                    end_seconds=16, status="ready", video_asset_id=asset.id))
        db.commit()
    envelhecer(pid, dias_atras)
    return pid


def envelhecer(pid: str, dias: float):
    with get_engine().begin() as conn:
        t = {"s": dias * 86400, "p": pid}
        conn.execute(text("UPDATE processing_jobs SET finished_at = now() - make_interval(secs => :s), "
                          "updated_at = now() - make_interval(secs => :s) WHERE project_id = :p"), t)
        conn.execute(text("UPDATE projects SET updated_at = now() - make_interval(secs => :s) WHERE id = :p"), t)


def contar(model, pid) -> int:
    with get_sessionmaker()() as db:
        return db.scalar(select(func.count()).select_from(model).where(model.project_id == uuid.UUID(pid)))


def arquivos(user, pid) -> list[str]:
    return get_storage().list_keys(project_prefix(user.id, uuid.UUID(pid)))


def retencao():
    rep = run_retention_locked(get_settings())
    assert not rep["skipped"]
    return rep


# =============================================================== prazos

def test_projeto_abandonado_sem_video(client, alice):
    velho, novo = criar(client, alice), criar(client, alice)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE projects SET updated_at = now() - interval '25 hours' WHERE id = :p"), {"p": velho})
    assert retencao()["abandoned_deleted"] == 1
    assert client.get(f"/api/v1/projects/{velho}", headers=alice.headers).status_code == 404
    assert client.get(f"/api/v1/projects/{novo}", headers=alice.headers).status_code == 200


def test_original_expira_em_7_dias_e_cortes_continuam(client, alice, video, monkeypatch):
    pid = concluir(client, alice, video, monkeypatch, dias_atras=8)
    recente = concluir(client, alice, video, monkeypatch, dias_atras=6)
    rep = retencao()
    assert rep["sources_expired"] == 1
    # original, transcrição e candidatos apagados; corte e projeto mantidos
    assert not any(k.endswith(".validated.mp4") for k in arquivos(alice, pid))
    assert any("/clips/" in k for k in arquivos(alice, pid))
    assert contar(TranscriptSegment, pid) == 0 and contar(ClipCandidate, pid) == 0 and contar(Clip, pid) == 1
    st = client.get(f"/api/v1/projects/{pid}/status", headers=alice.headers).json()
    assert st["status"] == "completed" and st["retention"]["source_available"] is False
    # regenerar é recusado com mensagem clara
    with get_sessionmaker()() as db:
        clip_id = db.scalar(select(Clip.id).where(Clip.project_id == uuid.UUID(pid)))
    r = client.post(f"/api/v1/clips/{clip_id}/regenerate", headers=alice.headers, json={})
    assert r.status_code == 409 and r.json()["code"] == "source_expired"
    # o projeto concluído há 6 dias não foi tocado
    assert any(k.endswith(".validated.mp4") for k in arquivos(alice, recente))
    assert contar(TranscriptSegment, recente) == 1
    st = client.get(f"/api/v1/projects/{recente}/status", headers=alice.headers).json()["retention"]
    assert st["source_available"] is True
    exp = datetime.fromisoformat(st["source_expires_at"])
    assert timedelta(hours=23) < exp - datetime.now(UTC) < timedelta(hours=25)  # faltam ~1 dia


def test_projeto_inteiro_expira_em_30_dias(client, alice, video, monkeypatch):
    velho = concluir(client, alice, video, monkeypatch, dias_atras=31)
    quase = concluir(client, alice, video, monkeypatch, dias_atras=29)
    rep = retencao()
    assert rep["projects_deleted"] == 1
    assert arquivos(alice, velho) == [] and contar(Clip, velho) == 0
    with get_sessionmaker()() as db:
        assert db.get(Project, uuid.UUID(velho)) is None
        # consumo registrado é mantido sem vínculo com o projeto (registro de uso)
        assert db.scalar(select(func.count()).select_from(UsageLedger).where(
            UsageLedger.idempotency_key == f"processed:{velho}")) == 1
    assert contar(Clip, quase) == 1


def test_projeto_falho_ou_cancelado_expira_pela_ultima_atividade(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/cancel", headers=alice.headers)
    envelhecer(pid, 31)
    assert retencao()["projects_deleted"] == 1
    assert arquivos(alice, pid) == []


def test_status_mostra_datas_de_expiracao(client, alice, video, monkeypatch):
    pid = concluir(client, alice, video, monkeypatch)
    r = client.get(f"/api/v1/projects/{pid}/status", headers=alice.headers).json()["retention"]
    src, proj = datetime.fromisoformat(r["source_expires_at"]), datetime.fromisoformat(r["project_expires_at"])
    assert (proj - src).days == 23 and r["source_available"] is True


# =============================================================== segurança da exclusão

def test_projeto_com_job_ativo_e_pulado_e_excluido_depois(client, alice, video, monkeypatch):
    pid = concluir(client, alice, video, monkeypatch, dias_atras=31)
    with get_sessionmaker()() as db:
        clip_id = db.scalar(select(Clip.id).where(Clip.project_id == uuid.UUID(pid)))
        db.add(ProcessingJob(project_id=uuid.UUID(pid), owner_id=alice.id, clip_id=clip_id, job_type="render_clip",
                             status="running", idempotency_key=f"render_clip:{clip_id}:9"))
        db.commit()
    rep = retencao()
    assert rep["projects_deleted"] == 0 and rep["skipped_busy"] >= 1 and contar(Clip, pid) == 1
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET status='succeeded' WHERE job_type='render_clip' "
                          "AND project_id=:p"), {"p": pid})
    envelhecer(pid, 31)
    assert retencao()["projects_deleted"] == 1


def test_projeto_travado_por_outra_operacao_e_pulado(client, alice, video, monkeypatch):
    pid = concluir(client, alice, video, monkeypatch, dias_atras=31)
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1 FROM projects WHERE id=:p FOR UPDATE"), {"p": pid})
        rep = retencao()
        conn.rollback()
    assert rep["projects_deleted"] == 0 and rep["skipped_busy"] >= 1
    assert retencao()["projects_deleted"] == 1


def test_falha_no_storage_nao_apaga_nada_do_banco_e_e_retomada(client, alice, video, monkeypatch):
    pid = concluir(client, alice, video, monkeypatch, dias_atras=31)

    def falha(self, prefix):
        raise StorageError("fora do ar")

    monkeypatch.setattr(Storage, "delete_prefix", falha)
    rep = retencao()
    assert rep["projects_deleted"] == 0 and rep["failures"]
    assert contar(Clip, pid) == 1 and arquivos(alice, pid)
    monkeypatch.undo()
    assert retencao()["projects_deleted"] == 1  # a próxima rodada conclui
    assert arquivos(alice, pid) == []


def test_arquivos_orfaos(client, alice, video, monkeypatch):
    pid = concluir(client, alice, video, monkeypatch)
    s = get_storage()
    orfao = f"{project_prefix(alice.id, uuid.UUID(pid))}source/reenvio.mp4"
    s._internal.put_object(Bucket=s.bucket, Key=orfao, Body=b"x")
    antes = set(arquivos(alice, pid))
    assert retencao()["orphans_deleted"] == 0  # recente: mantido
    future = datetime.now(UTC) + timedelta(hours=25)
    from app.worker.maintenance import run_retention_locked as r

    rep = r(get_settings(), now=future)
    assert rep["orphans_deleted"] == 1
    assert set(arquivos(alice, pid)) == antes - {orfao}  # arquivos com registro no banco ficam


def test_detalhe_de_erro_e_auditoria_antiga(client, alice, video, monkeypatch):
    pid = projeto_pronto(client, alice, video)
    monkeypatch.setattr(stages_mod, "get_pipeline",
                        lambda: [Stage("x", "x", 5, lambda ctx: (_ for _ in ()).throw(RuntimeError("boom")))])
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET finished_at = now() - interval '31 days' WHERE project_id=:p"),
                     {"p": pid})
        conn.execute(text("UPDATE audit_events SET created_at = now() - interval '366 days' WHERE resource_id=:p"),
                     {"p": pid})
    rep = retencao()
    assert rep["error_details_cleared"] == 1 and rep["audit_deleted"] >= 1
    with get_sessionmaker()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.project_id == uuid.UUID(pid)))
    assert job.error_detail is None and job.error_code == "internal_error"


def test_retencao_e_idempotente_e_nunca_roda_duas_vezes_ao_mesmo_tempo(client, alice, video, monkeypatch):
    concluir(client, alice, video, monkeypatch, dias_atras=31)
    barreira = threading.Barrier(3)
    reps = []

    def rodar():
        barreira.wait()
        reps.append(run_retention_locked(get_settings()))

    ts = [threading.Thread(target=rodar) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert sum(r.get("projects_deleted", 0) for r in reps) == 1
    seg = retencao()
    assert seg["projects_deleted"] == 0 and seg["sources_expired"] == 0 and not seg["failures"]


@pytest.mark.parametrize("rodada", range(3))
def test_corrida_retencao_x_regeneracao(client, alice, video, monkeypatch, rodada):
    """Retenção do original ao mesmo tempo que um pedido de regeneração: ou a regeneração é aceita antes
    (e o original fica até o job terminar), ou é recusada com `source_expired`. Nunca um job sem original."""
    pid = concluir(client, alice, video, monkeypatch, dias_atras=8)
    with get_sessionmaker()() as db:
        clip_id = db.scalar(select(Clip.id).where(Clip.project_id == uuid.UUID(pid)))
    barreira = threading.Barrier(2)
    out = {}

    def regen():
        barreira.wait()
        if rodada == 1:
            time.sleep(0.01)
        out["regen"] = client.post(f"/api/v1/clips/{clip_id}/regenerate", headers=alice.headers, json={})

    def ret():
        barreira.wait()
        if rodada == 2:
            time.sleep(0.01)
        out["ret"] = run_retention_locked(get_settings())

    ts = [threading.Thread(target=regen), threading.Thread(target=ret)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    st = client.get(f"/api/v1/projects/{pid}/status", headers=alice.headers).json()["retention"]
    if out["regen"].status_code == 202:
        assert st["source_available"] is True  # original preservado para o job
    else:
        assert out["regen"].json()["code"] == "source_expired" and st["source_available"] is False


# =============================================================== exclusão de conta

class _Supabase(BaseHTTPRequestHandler):
    status = 200
    seen: list = []

    def do_DELETE(self):
        _Supabase.seen.append((self.path, self.headers.get("apikey"), self.headers.get("Authorization")))
        self.send_response(_Supabase.status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


@pytest.fixture
def supabase_admin(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Supabase)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    _Supabase.seen, _Supabase.status = [], 200
    s = get_settings()
    from pydantic import SecretStr
    monkeypatch.setattr(s, "supabase_admin_url", f"http://127.0.0.1:{srv.server_port}")
    monkeypatch.setattr(s, "supabase_service_role_key", SecretStr("service-role-teste"))
    yield _Supabase
    srv.shutdown()


def test_excluir_conta_apaga_tudo_e_preserva_outros(client, alice, bob, video, monkeypatch, supabase_admin):
    pa = concluir(client, alice, video, monkeypatch)
    criar(client, alice)
    pb = projeto_pronto(client, bob, video)
    r = client.delete("/api/v1/me", headers=alice.headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["projects_deleted"] == 2 and body["auth_account_deleted"] is True
    assert supabase_admin.seen == [(f"/auth/v1/admin/users/{alice.id}", "service-role-teste",
                                    "Bearer service-role-teste")]
    assert get_storage().list_keys(f"users/{alice.id}/") == []
    with get_sessionmaker()() as db:
        assert db.get(Profile, alice.id) is None
        assert db.scalar(select(func.count()).select_from(Subscription).where(Subscription.owner_id == alice.id)) == 0
        assert db.scalar(select(func.count()).select_from(Project).where(Project.owner_id == alice.id)) == 0
        assert db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.owner_id == alice.id)) == 0
        ledger = db.scalar(select(UsageLedger).where(UsageLedger.idempotency_key == f"processed:{pa}"))
        assert ledger.owner_id is None  # registro de uso mantido sem vínculo pessoal
        ev = db.scalar(select(AuditEvent).where(AuditEvent.action == "account.deleted"))
        assert ev.resource_id == alice.id and "alice" not in json.dumps(ev.details)
    # token antigo não recria a conta
    r = client.get("/api/v1/me", headers=alice.headers)
    assert r.status_code == 403 and "excluída" in r.json()["detail"]
    # dados de outro usuário intactos
    assert client.get(f"/api/v1/projects/{pb}", headers=bob.headers).status_code == 200
    assert get_storage().list_keys(f"users/{bob.id}/")


def test_excluir_conta_com_processamento_rodando_e_recusado(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET status='running' WHERE project_id=:p"), {"p": pid})
    r = client.delete("/api/v1/me", headers=alice.headers)
    assert r.status_code == 409
    assert client.get(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 200


def test_excluir_conta_com_job_na_fila_cancela(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    assert client.delete("/api/v1/me", headers=alice.headers).status_code == 200
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)  # nada a fazer: job não existe mais
    assert get_storage().list_keys(f"users/{alice.id}/") == []


def test_excluir_conta_storage_fora_do_ar_nao_apaga_nada(client, alice, video, monkeypatch):
    pid = projeto_pronto(client, alice, video)

    def falha(self, prefix):
        raise StorageError("fora do ar")

    monkeypatch.setattr(Storage, "delete_prefix", falha)
    assert client.delete("/api/v1/me", headers=alice.headers).status_code == 503
    monkeypatch.undo()
    assert client.get(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 200
    assert client.delete("/api/v1/me", headers=alice.headers).status_code == 200  # nova tentativa conclui


def test_excluir_conta_sem_chave_do_supabase_avisa(client, alice, monkeypatch):
    monkeypatch.setattr(get_settings(), "supabase_service_role_key", None)
    criar(client, alice)
    body = client.delete("/api/v1/me", headers=alice.headers).json()
    assert body["auth_account_deleted"] is False and "administrador" in body["message"]
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "account.auth_delete_pending")) == 1


def test_excluir_conta_supabase_com_erro_nao_desfaz_exclusao(client, alice, supabase_admin):
    supabase_admin.status = 500
    criar(client, alice)
    body = client.delete("/api/v1/me", headers=alice.headers).json()
    assert body["auth_account_deleted"] is False
    with get_sessionmaker()() as db:
        assert db.get(Profile, alice.id) is None


def test_politica_publica_reflete_a_configuracao(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "retention_source_days", 5)
    r = client.get("/api/v1/policy")
    assert r.status_code == 200
    d = r.json()
    assert d["retencao"]["video_original_dias"] == 5 and d["retencao"]["projeto_dias"] == 30
    assert any(s["nome"].startswith("Anthropic") for s in d["suboperadores"])
    assert d["texto_juridico_revisado"] is False
