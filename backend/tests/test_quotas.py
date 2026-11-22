import uuid
from datetime import UTC, datetime, timedelta

import pytest
from rq import SimpleWorker
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.models import Subscription, UsageLedger
from app.pipeline import stages as stages_mod
from app.pipeline.context import PipelineError
from app.pipeline.stages import Stage, prepare_source
from app.services.queue import get_queue, get_redis
from app.services.quotas import month_bounds
from tests.helpers import criar, projeto_pronto
from tests.media import make_video


@pytest.fixture(scope="module")
def video20(tmp_path_factory):
    return make_video(tmp_path_factory.mktemp("q") / "v.mp4", 20)


def pipeline_ok(monkeypatch):
    monkeypatch.setattr(stages_mod, "get_pipeline",
                        lambda: [Stage("prepare", "preparação", 5, prepare_source)])


def pipeline_falha(monkeypatch):
    def falha(ctx):
        raise PipelineError("bad_video", "Falha simulada.")
    monkeypatch.setattr(stages_mod, "get_pipeline", lambda: [Stage("x", "teste", 5, falha)])


def worker():
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)


def me(client, user):
    r = client.get("/api/v1/me", headers=user.headers)
    assert r.status_code == 200, r.text
    return r.json()["usage"]


def set_sub(user, **values):
    with get_sessionmaker()() as db:
        sub = db.scalar(select(Subscription).where(Subscription.owner_id == user.id))
        for k, v in values.items():
            setattr(sub, k, v)
        db.commit()


def processed(user):
    with get_sessionmaker()() as db:
        return db.scalars(select(UsageLedger).where(UsageLedger.owner_id == user.id,
                                                    UsageLedger.kind == "processed_seconds")).all()


def test_me_mostra_consumo_inicial(client, alice):
    u = me(client, alice)
    s = get_settings()
    assert u["quota_minutes"] == s.plan_free_monthly_minutes and u["projects_per_day"] == s.plan_free_projects_per_day
    assert u["used_minutes"] == 0 and u["reserved_minutes"] == 0 and u["remaining_minutes"] == u["quota_minutes"]
    start, end = month_bounds(datetime.now(UTC))
    assert u["period_start"].startswith(start.date().isoformat())


def test_cobranca_somente_no_sucesso_e_uma_vez(client, alice, video20, monkeypatch):
    pid = projeto_pronto(client, alice, video20)
    pipeline_falha(monkeypatch)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    assert me(client, alice)["reserved_minutes"] == pytest.approx(20 / 60, abs=0.01)
    worker()
    assert processed(alice) == []  # falha não é cobrada
    assert me(client, alice)["reserved_minutes"] == 0
    pipeline_ok(monkeypatch)
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 202
    worker()
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 200  # repetição
    worker()
    from app.models import ProcessingJob
    from app.worker.tasks import process_project
    with get_sessionmaker()() as db:
        for j in db.scalars(select(ProcessingJob).where(ProcessingJob.project_id == uuid.UUID(pid))):
            assert process_project(str(j.id)) == "skipped"
    rows = processed(alice)
    assert len(rows) == 1 and float(rows[0].quantity) == pytest.approx(20, abs=0.1)
    u = me(client, alice)
    assert u["used_minutes"] == pytest.approx(0.33, abs=0.01)
    assert u["remaining_minutes"] == pytest.approx(u["quota_minutes"] - 0.33, abs=0.01)


def test_cota_mensal_excedida(client, alice, video20, monkeypatch):
    pipeline_ok(monkeypatch)
    me(client, alice)
    set_sub(alice, monthly_minutes_quota=1)  # 60 s: cabe 2 vídeos de 20 s, não 4
    p1, p2, p3 = (projeto_pronto(client, alice, video20) for _ in range(3))
    assert client.post(f"/api/v1/projects/{p1}/process", headers=alice.headers).status_code == 202
    worker()  # 20 s usados
    assert client.post(f"/api/v1/projects/{p2}/process", headers=alice.headers).status_code == 202  # reservado
    r = client.post(f"/api/v1/projects/{p3}/process", headers=alice.headers)  # 20+20+20 = 60: ainda cabe
    assert r.status_code == 202
    p4 = projeto_pronto(client, alice, video20)
    r = client.post(f"/api/v1/projects/{p4}/process", headers=alice.headers)
    assert r.status_code == 402 and r.json()["code"] == "quota_exceeded"
    assert "cota mensal" in r.json()["detail"]
    st = client.get(f"/api/v1/projects/{p4}/status", headers=alice.headers).json()
    assert st["status"] == "uploaded" and st["job"] is None  # nada enfileirado
    assert get_queue().count == 2


def test_cota_de_outro_usuario_nao_interfere(client, alice, bob, video20, monkeypatch):
    pipeline_ok(monkeypatch)
    me(client, alice)
    set_sub(alice, monthly_minutes_quota=0)
    pa = projeto_pronto(client, alice, video20)
    pb = projeto_pronto(client, bob, video20)
    assert client.post(f"/api/v1/projects/{pa}/process", headers=alice.headers).status_code == 402
    assert client.post(f"/api/v1/projects/{pb}/process", headers=bob.headers).status_code == 202


def test_novo_mes_libera_cota(client, alice, video20, monkeypatch):
    pipeline_ok(monkeypatch)
    me(client, alice)
    set_sub(alice, monthly_minutes_quota=1)
    with get_sessionmaker()() as db:
        db.add(UsageLedger(owner_id=alice.id, kind="processed_seconds", quantity=60, estimated_cost_usd=0,
                           idempotency_key=f"teste:{uuid.uuid4()}"))
        db.commit()
    pid = projeto_pronto(client, alice, video20)
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 402
    last_month = month_bounds(datetime.now(UTC))[0] - timedelta(days=1)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE usage_ledger SET created_at=:t WHERE owner_id=:o"), {"t": last_month, "o": alice.id})
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 202


def test_limite_de_projetos_por_dia_nao_burla_com_exclusao(client, alice):
    me(client, alice)
    set_sub(alice, max_projects_per_day=2)
    a = criar(client, alice)
    criar(client, alice)
    assert client.delete(f"/api/v1/projects/{a}", headers=alice.headers).status_code == 204
    r = client.post("/api/v1/projects", json={"title": "x", "rights_confirmed": True}, headers=alice.headers)
    assert r.status_code == 429 and r.json()["code"] == "project_limit"
    assert me(client, alice)["projects_last_24h"] == 2
    old = datetime.now(UTC) - timedelta(hours=25)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE audit_events SET created_at=:t WHERE actor_id=:o"), {"t": old, "o": alice.id})
    assert client.post("/api/v1/projects", json={"title": "x", "rights_confirmed": True},
                       headers=alice.headers).status_code == 201


def test_conta_suspensa(client, alice, video20):
    pid = projeto_pronto(client, alice, video20)
    set_sub(alice, status="suspended")
    r = client.post("/api/v1/projects", json={"title": "x", "rights_confirmed": True}, headers=alice.headers)
    assert r.status_code == 403 and r.json()["code"] == "subscription_inactive"
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 403


def test_cotas_desativadas(client, alice, video20, monkeypatch):
    monkeypatch.setattr(get_settings(), "quotas_enabled", False)
    me(client, alice)
    set_sub(alice, monthly_minutes_quota=0, max_projects_per_day=0)
    pid = projeto_pronto(client, alice, video20)
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 202


def test_custo_interno_estimado(client, alice, video20, monkeypatch):
    pipeline_ok(monkeypatch)
    monkeypatch.setattr(get_settings(), "cost_per_processed_minute_usd", 0.03)
    pid = projeto_pronto(client, alice, video20)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    worker()
    row = processed(alice)[0]
    assert float(row.estimated_cost_usd) == pytest.approx(20 / 60 * 0.03, abs=1e-4)
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(UsageLedger)
                         .where(UsageLedger.idempotency_key == f"processed:{pid}")) == 1


def test_pedidos_simultaneos_nao_estouram_a_cota(client, alice, video20):
    """A trava da assinatura serializa os pedidos: B espera A gravar a reserva e então é recusado."""
    import threading
    import time as _time

    from app.models import Project
    from app.services.quotas import QuotaExceeded, check_process

    me(client, alice)
    set_sub(alice, monthly_minutes_quota=1)  # 60 s
    big = make_video(video20.parent / "v40.mp4", 40)
    p1, p2 = projeto_pronto(client, alice, big), projeto_pronto(client, alice, big)
    settings = get_settings()
    result = {}

    def pedido_b():
        with get_sessionmaker()() as db:
            t0 = _time.monotonic()
            try:
                check_process(db, alice.id, db.get(Project, uuid.UUID(p2)), settings)
                result["b"] = "aceito"
            except QuotaExceeded as exc:
                result["b"] = exc.code
            result["espera"] = _time.monotonic() - t0
            db.rollback()

    with get_sessionmaker()() as db_a:
        proj = db_a.get(Project, uuid.UUID(p1))
        check_process(db_a, alice.id, proj, settings)  # trava a assinatura
        t = threading.Thread(target=pedido_b)
        t.start()
        _time.sleep(1.0)  # B está bloqueado aguardando a trava
        assert "b" not in result
        proj.status = "queued"  # reserva de A
        db_a.commit()
    t.join(10)
    assert result["b"] == "quota_exceeded" and result["espera"] >= 0.9
