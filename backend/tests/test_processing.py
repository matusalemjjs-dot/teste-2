import hashlib
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from rq import SimpleWorker
from sqlalchemy import select, text

from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.models import MediaAsset, ProcessingJob, Project
from app.pipeline import stages as stages_mod
from app.pipeline.context import PipelineError
from app.pipeline.stages import Stage, prepare_source
from app.services.queue import get_queue, get_redis
from tests.helpers import criar, projeto_pronto
from tests.media import make_video

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("proc") / "v.mp4", 20)


def processar(client, user, pid):
    return client.post(f"/api/v1/projects/{pid}/process", headers=user.headers)


def rodar_worker():
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)


def status(client, user, pid):
    return client.get(f"/api/v1/projects/{pid}/status", headers=user.headers).json()


def jobs(pid):
    with get_sessionmaker()() as db:
        return db.scalars(select(ProcessingJob).where(ProcessingJob.project_id == uuid.UUID(pid))
                          .order_by(ProcessingJob.created_at)).all()


def tmp_vazio() -> bool:
    root = Path(get_settings().worker_tmp_dir)
    return not root.exists() or not any(root.iterdir())


def usar_pipeline(monkeypatch, *extra: Stage, com_prepare=True):
    stages = ([Stage("prepare", "preparação", 5, prepare_source)] if com_prepare else []) + list(extra)
    monkeypatch.setattr(stages_mod, "get_pipeline", lambda: stages)


def ok_stage(ctx):
    ctx.data["ok"] = True


# ---------------------------------------------------------------- solicitação e idempotência

def test_processar_exige_upload(client, alice):
    pid = criar(client, alice)
    r = processar(client, alice, pid)
    assert r.status_code == 409 and r.json()["code"] == "upload_required"


def test_processar_enfileira_uma_unica_vez(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    r1 = processar(client, alice, pid)
    r2 = processar(client, alice, pid)
    assert r1.status_code == 202 and r1.json()["created"] is True
    assert r2.status_code == 200 and r2.json()["created"] is False
    assert r1.json()["job"]["id"] == r2.json()["job"]["id"]
    assert r1.json()["project"]["status"] == "queued"
    assert len(jobs(pid)) == 1
    assert get_queue().count == 1
    st = status(client, alice, pid)
    assert st["status"] == "queued" and st["job"]["status"] == "queued"


def test_isolamento_processamento(client, alice, bob, video):
    pid = projeto_pronto(client, alice, video)
    assert processar(client, bob, pid).status_code == 404
    assert get_queue().count == 0


# ---------------------------------------------------------------- execução

def test_pipeline_padrao_nao_finge_resultado(client, alice, video):
    """Sem transcrição/IA/renderização implementadas, o projeto NÃO pode ser concluído."""
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    rodar_worker()
    st = status(client, alice, pid)
    assert st["status"] == "failed"
    assert st["error_code"] == "stage_unavailable"
    assert "transcrição" in st["status_message"]
    assert st["progress"] < 100
    assert st["job"]["status"] == "failed" and st["job"]["stage"] == "transcribe"
    with get_sessionmaker()() as db:
        asset = db.scalar(select(MediaAsset).where(MediaAsset.project_id == uuid.UUID(pid)))
    assert asset.checksum_sha256 == hashlib.sha256(video.read_bytes()).hexdigest()
    assert tmp_vazio()


def test_sucesso_com_etapas_reais_e_reexecucao_ignorada(client, alice, video, monkeypatch):
    seen = {}

    def inspeciona(ctx):
        p = ctx.data["source_path"]
        seen["exists"] = p.exists()
        seen["size"] = p.stat().st_size
        seen["dir"] = ctx.workdir

    usar_pipeline(monkeypatch, Stage("check", "verificação", 5, inspeciona))
    pid = projeto_pronto(client, alice, video)
    job_id = processar(client, alice, pid).json()["job"]["id"]
    rodar_worker()
    st = status(client, alice, pid)
    assert st["status"] == "completed" and st["progress"] == 100 and st["job"]["status"] == "succeeded"
    assert seen["exists"] and seen["size"] == video.stat().st_size
    assert not seen["dir"].exists()  # temporários removidos
    # Repetir a requisição ou reexecutar a tarefa não processa de novo
    assert processar(client, alice, pid).status_code == 200
    from app.worker.tasks import process_project
    assert process_project(job_id) == "skipped"
    assert len(jobs(pid)) == 1 and jobs(pid)[0].attempts == 1


def test_erro_inesperado_nao_trava_a_fila(client, alice, video, monkeypatch):
    pid_falha = projeto_pronto(client, alice, video)
    pid_ok = projeto_pronto(client, alice, video)

    def talvez_falhe(ctx):
        if str(ctx.project_id) == pid_falha:
            raise RuntimeError("falha interna com Bearer eyJabc.eyJdef.ghi")

    usar_pipeline(monkeypatch, Stage("x", "teste", 5, talvez_falhe))
    processar(client, alice, pid_falha)
    processar(client, alice, pid_ok)
    rodar_worker()
    st = status(client, alice, pid_falha)
    assert st["status"] == "failed" and st["error_code"] == "internal_error"
    assert "inesperado" in st["status_message"] and "eyJ" not in st["status_message"]
    detail = jobs(pid_falha)[0].error_detail
    assert "RuntimeError" in detail and "eyJabc" not in detail  # detalhe interno, sem segredos
    assert status(client, alice, pid_ok)["status"] == "completed"
    assert tmp_vazio()


def test_nova_tentativa_apos_falha_e_limite(client, alice, video, monkeypatch):
    def falha(ctx):
        raise PipelineError("bad_video", "Vídeo problemático.")

    usar_pipeline(monkeypatch, Stage("x", "teste", 5, falha))
    pid = projeto_pronto(client, alice, video)
    for _ in range(3):
        r = processar(client, alice, pid)
        assert r.status_code == 202, r.text
        rodar_worker()
        assert status(client, alice, pid)["status"] == "failed"
    keys = [j.idempotency_key for j in jobs(pid)]
    assert keys == [f"process_project:{pid}:{i}" for i in (1, 2, 3)]
    r = processar(client, alice, pid)
    assert r.status_code == 409 and r.json()["code"] == "attempts_exhausted"


def test_falhas_de_infraestrutura_nao_consomem_tentativas(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    for _ in range(4):  # pipeline padrão -> stage_unavailable
        assert processar(client, alice, pid).status_code == 202
        rodar_worker()
    assert status(client, alice, pid)["error_code"] == "stage_unavailable"


def test_fila_indisponivel(client, alice, video, monkeypatch):
    import app.services.queue as q

    def quebrada():
        raise ConnectionError("redis fora do ar")

    pid = projeto_pronto(client, alice, video)
    monkeypatch.setattr(q, "get_queue", quebrada)
    r = processar(client, alice, pid)
    assert r.status_code == 503
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "queue_unavailable"
    monkeypatch.undo()
    assert processar(client, alice, pid).status_code == 202


# ---------------------------------------------------------------- cancelamento

def test_cancelar_na_fila(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    job_id = processar(client, alice, pid).json()["job"]["id"]
    r = client.post(f"/api/v1/projects/{pid}/cancel", headers=alice.headers)
    assert r.status_code == 200 and r.json()["status"] == "canceled"
    assert jobs(pid)[0].status == "canceled"
    from rq.job import Job
    assert Job.fetch(job_id, connection=get_redis()).get_status() == "canceled"
    rodar_worker()
    assert jobs(pid)[0].status == "canceled" and jobs(pid)[0].attempts == 0
    assert processar(client, alice, pid).json()["code"] == "canceled"
    assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 204


def _cancelar_por_fora(pid):
    with get_sessionmaker()() as db:
        from app.services.projects import cancel_project
        cancel_project(db, db.get(Project, uuid.UUID(pid)))


def test_cancelar_durante_processamento(client, alice, video, monkeypatch):
    seen = {}

    def cancela_e_continua(ctx):
        seen["dir"] = ctx.workdir
        _cancelar_por_fora(str(ctx.project_id))
        ctx.checkpoint()
        seen["passou"] = True

    usar_pipeline(monkeypatch, Stage("x", "teste", 5, cancela_e_continua), Stage("y", "nunca", 5, ok_stage))
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    rodar_worker()
    st = status(client, alice, pid)
    assert st["status"] == "canceled" and st["job"]["status"] == "canceled"
    assert "passou" not in seen and not seen["dir"].exists()


def test_cancelamento_interrompe_subprocesso(client, alice, video, monkeypatch):
    elapsed = {}

    def longo(ctx):
        threading.Timer(1.0, _cancelar_por_fora, args=(str(ctx.project_id),)).start()
        t0 = time.monotonic()
        try:
            ctx.run(["sleep", "30"], timeout=60, poll=0.2)
        finally:
            elapsed["s"] = time.monotonic() - t0

    usar_pipeline(monkeypatch, Stage("x", "teste", 5, longo), com_prepare=False)
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    rodar_worker()
    assert status(client, alice, pid)["status"] == "canceled"
    assert elapsed["s"] < 10


# ---------------------------------------------------------------- limites

def test_timeout_de_subprocesso(client, alice, video, monkeypatch):
    usar_pipeline(monkeypatch, Stage("x", "teste", 5, lambda ctx: ctx.run(["sleep", "30"], timeout=1, poll=0.2)),
                  com_prepare=False)
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    rodar_worker()
    assert status(client, alice, pid)["error_code"] == "stage_timeout"


def test_timeout_do_job(client, alice, video, monkeypatch):
    monkeypatch.setattr(get_settings(), "job_timeout_seconds", 1)
    usar_pipeline(monkeypatch, Stage("x", "teste", 5, lambda ctx: time.sleep(5)), com_prepare=False)
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    rodar_worker()
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "timeout"
    assert tmp_vazio()


def test_arquivo_alterado_apos_validacao(client, alice, video, tmp_path):
    from app.services.storage import get_storage
    pid = projeto_pronto(client, alice, video)
    with get_sessionmaker()() as db:
        key = db.scalar(select(MediaAsset.storage_key).where(MediaAsset.project_id == uuid.UUID(pid)))
    get_storage()._internal.put_object(Bucket=get_storage().bucket, Key=key, Body=b"trocado")
    processar(client, alice, pid)
    rodar_worker()
    assert status(client, alice, pid)["error_code"] == "source_changed"


def test_job_perdido_e_recuperado(client, alice, video):
    from app.services.jobs import sweep_stale_jobs
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    old = datetime.now(UTC) - timedelta(days=1)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE projects SET status='processing' WHERE id=:i"), {"i": pid})
        conn.execute(text("UPDATE processing_jobs SET status='running', updated_at=:t WHERE project_id=:i"),
                     {"i": pid, "t": old})
    with get_sessionmaker()() as db:
        assert sweep_stale_jobs(db, get_settings()) == 1
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "worker_lost"


def test_limpeza_de_temporarios_orfaos():
    from app.worker.tmp import cleanup_stale, tmp_root
    settings = get_settings()
    root = tmp_root(settings)
    velho, novo = root / "job-velho", root / "job-novo"
    velho.mkdir()
    novo.mkdir()
    (velho / "x.mp4").write_bytes(b"x")
    t = time.time() - 7 * 3600
    os.utime(velho, (t, t))
    assert cleanup_stale(settings) == 1
    assert not velho.exists() and novo.exists()


def test_limites_de_recursos_aplicados():
    code = ("import resource; from app.core.config import Settings; from app.worker.limits import apply_limits;"
            "a = apply_limits(Settings(worker_memory_limit_mb=1024, worker_cpu_limit_seconds=99, max_upload_mb=10));"
            "print(resource.getrlimit(resource.RLIMIT_AS)[0], resource.getrlimit(resource.RLIMIT_CPU)[0],"
            "resource.getrlimit(resource.RLIMIT_FSIZE)[0])")
    out = subprocess.run([sys.executable, "-c", code], cwd=BACKEND, capture_output=True, text=True, check=True)
    assert out.stdout.split() == [str(1024 * 1024 * 1024), "99", str(20 * 1024 * 1024)]


# ---------------------------------------------------------------- worker real (fork), ponta a ponta

def _worker_env(**extra) -> dict:
    env = {**os.environ, "WORKER_TMP_DIR": get_settings().worker_tmp_dir, **extra}
    return env


def test_worker_real_em_processo_separado(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    r = subprocess.run([sys.executable, "-m", "app.worker.run", "--burst"], cwd=BACKEND, env=_worker_env(),
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "stage_unavailable"
    assert jobs(pid)[0].attempts == 1
    assert tmp_vazio()


def test_worker_real_aplica_limites_no_processo_do_job(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    processar(client, alice, pid)
    r = subprocess.run([sys.executable, "-m", "app.worker.run", "--burst"], cwd=BACKEND,
                       env=_worker_env(WORKER_MEMORY_LIMIT_MB="3000", WORKER_CPU_LIMIT_SECONDS="600",
                                       LOG_LEVEL="INFO"), capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:]
    logs = r.stdout + r.stderr
    assert f'"memory_bytes": {3000 * 1024 * 1024}' in logs
    assert '"cpu_seconds": 600' in logs
    assert status(client, alice, pid)["job"]["attempts"] == 1


def test_processo_do_job_morto_marca_falha(client, alice, video):
    from app.worker.tasks import on_work_horse_killed
    pid = projeto_pronto(client, alice, video)
    job_id = processar(client, alice, pid).json()["job"]["id"]

    class FakeJob:
        id = job_id

    on_work_horse_killed(FakeJob(), 123, 9, None)
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "worker_lost"
