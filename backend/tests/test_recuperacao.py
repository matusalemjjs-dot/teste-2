"""Beta fechado — Lote 1: F1 (troca do vídeo após validação), F2 (jobs perdidos), F2b (sinal de vida) e
F3 (exclusão com job preso). Testes de concorrência e recuperação."""

import hashlib
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path

import httpx
import pytest
from rq import SimpleWorker
from sqlalchemy import func, select, text

from app.core.config import get_settings
from app.db.session import get_engine, get_sessionmaker
from app.models import Clip, MediaAsset, ProcessingJob, Project, UsageLedger
from app.pipeline import stages as stages_mod
from app.pipeline.stages import Stage, prepare_source
from app.services.queue import get_queue, get_redis
from app.services.storage import Storage, StorageError, get_storage, iter_object, project_prefix
from app.worker.maintenance import run_maintenance
from tests.helpers import criar, projeto_pronto
from tests.media import make_video

BACKEND = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def video(tmp_path_factory) -> Path:
    return make_video(tmp_path_factory.mktemp("rec") / "a.mp4", 20)


@pytest.fixture(scope="module")
def video_trocado(video) -> Path:
    """Mesmo tamanho do original, conteúdo diferente (miolo corrompido): o código antigo não detectava."""
    data = bytearray(video.read_bytes())
    meio = len(data) // 2
    data[meio:meio + 4096] = os.urandom(4096)
    p = video.parent / "trocado.mp4"
    p.write_bytes(bytes(data))
    assert p.stat().st_size == video.stat().st_size
    return p


def sha(path_or_key) -> str:
    h = hashlib.sha256()
    if isinstance(path_or_key, Path):
        h.update(path_or_key.read_bytes())
    else:
        for chunk in iter_object(get_storage(), path_or_key):
            h.update(chunk)
    return h.hexdigest()


def ticket(client, user, pid, path):
    r = client.post(f"/api/v1/projects/{pid}/upload-url", headers=user.headers,
                    json={"filename": "v.mp4", "content_type": "video/mp4", "size_bytes": path.stat().st_size})
    assert r.status_code == 200, r.text
    return r.json()


def enviar(t, path):
    with path.open("rb") as fh:
        r = httpx.post(t["url"], data=t["fields"], files={"file": ("v.mp4", fh, "video/mp4")}, trust_env=False)
    assert r.status_code in (200, 201, 204)


def finalizar(client, user, pid):
    return client.post(f"/api/v1/projects/{pid}/finalize-upload", headers=user.headers)


def asset_fonte(pid) -> MediaAsset:
    with get_sessionmaker()() as db:
        return db.scalar(select(MediaAsset).where(MediaAsset.project_id == uuid.UUID(pid),
                                                  MediaAsset.kind == "source_video"))


def pipeline_prepare(monkeypatch, *extra):
    monkeypatch.setattr(stages_mod, "get_pipeline",
                        lambda: [Stage("prepare", "preparação", 5, prepare_source), *extra])


def worker():
    SimpleWorker([get_queue()], connection=get_redis()).work(burst=True)


def status(client, user, pid):
    return client.get(f"/api/v1/projects/{pid}/status", headers=user.headers).json()


def envelhecer_jobs(pid, segundos=3600):
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET updated_at = now() - make_interval(secs => :s) "
                          "WHERE project_id = :p"), {"s": segundos, "p": pid})


# =============================================================== F1 — vídeo trocado após a validação

def test_f1_troca_depois_da_validacao_nao_chega_ao_processamento(client, alice, video, video_trocado, monkeypatch):
    pid = criar(client, alice)
    t = ticket(client, alice, pid, video)
    enviar(t, video)
    assert finalizar(client, alice, pid).status_code == 200
    asset = asset_fonte(pid)
    assert asset.storage_key.endswith(".validated.mp4")
    enviar(t, video_trocado)  # URL de upload ainda válida: grava na chave ORIGINAL, não na validada
    pipeline_prepare(monkeypatch)
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 202
    worker()
    assert status(client, alice, pid)["status"] == "completed"
    assert asset_fonte(pid).checksum_sha256 == sha(video)  # processou o arquivo validado
    assert sha(asset.storage_key) == sha(video)


def test_f1_troca_durante_a_validacao_nao_afeta_a_copia(client, alice, video, video_trocado, monkeypatch):
    from app.services import media_probe

    pid = criar(client, alice)
    t = ticket(client, alice, pid, video)
    enviar(t, video)
    real = media_probe.analyze

    def analyze_com_troca(*a, **k):
        enviar(t, video_trocado)  # troca no meio da validação
        return real(*a, **k)

    monkeypatch.setattr(media_probe, "analyze", analyze_com_troca)
    assert finalizar(client, alice, pid).status_code == 200
    assert sha(asset_fonte(pid).storage_key) == sha(video)


def test_f1_validacoes_simultaneas_geram_uma_unica_copia(client, alice, video):
    pid = criar(client, alice)
    enviar(ticket(client, alice, pid, video), video)
    barreira = threading.Barrier(2)
    codes = []

    def fin():
        barreira.wait()
        codes.append(finalizar(client, alice, pid).status_code)

    ts = [threading.Thread(target=fin) for _ in range(2)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert 200 in codes and set(codes) <= {200, 409}
    assert finalizar(client, alice, pid).json()["already_finalized"] is True
    keys = get_storage().list_keys(project_prefix(alice.id, uuid.UUID(pid)))
    assert len(keys) == 1 and keys[0].endswith(".validated.mp4")  # original apagado, uma única cópia
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(MediaAsset).where(
            MediaAsset.project_id == uuid.UUID(pid), MediaAsset.status == "ready")) == 1


def test_f1_falha_na_copia_nao_deixa_estado_invalido(client, alice, video, monkeypatch):
    pid = criar(client, alice)
    enviar(ticket(client, alice, pid, video), video)

    def falha(self, src, dst):
        raise StorageError("fora do ar")

    monkeypatch.setattr(Storage, "copy", falha)
    assert finalizar(client, alice, pid).status_code == 503
    assert status(client, alice, pid)["status"] == "pending"
    assert asset_fonte(pid).status == "pending"
    monkeypatch.undo()
    assert finalizar(client, alice, pid).status_code == 200


def test_f1_upload_invalido_remove_original_e_copia(client, alice, tmp_path):
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"nao e video " * 1000)
    pid = criar(client, alice)
    enviar(ticket(client, alice, pid, fake), fake)
    assert finalizar(client, alice, pid).status_code == 422
    assert get_storage().list_keys(project_prefix(alice.id, uuid.UUID(pid))) == []


# =============================================================== F2 — jobs perdidos na fila

def test_f2_redis_zerado_job_e_devolvido_e_processado_uma_vez(client, alice, video, monkeypatch):
    pipeline_prepare(monkeypatch)
    pid = projeto_pronto(client, alice, video)
    job_id = client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).json()["job"]["id"]
    get_redis().flushdb()  # Redis reiniciado sem persistência
    envelhecer_jobs(pid)
    rep = run_maintenance()
    assert rep["queue"]["requeued"] == 1 and get_queue().count == 1
    worker()
    st = status(client, alice, pid)
    assert st["status"] == "completed" and st["job"]["id"] == job_id and st["job"]["attempts"] == 1
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(UsageLedger).where(
            UsageLedger.idempotency_key == f"processed:{pid}")) == 1
    assert run_maintenance()["queue"]["requeued"] == 0  # nada mais a fazer


def test_f2_manutencoes_simultaneas_nao_duplicam(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    get_redis().flushdb()
    envelhecer_jobs(pid)
    barreira = threading.Barrier(3)
    reports = []

    def rodar():
        barreira.wait()
        reports.append(run_maintenance())

    ts = [threading.Thread(target=rodar) for _ in range(3)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    requeued = sum(r["queue"]["requeued"] for r in reports if not r["skipped"])
    assert requeued == 1 and get_queue().count == 1
    with get_sessionmaker()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.project_id == uuid.UUID(pid)))
    assert job.options["requeues"] == 1


def test_f2_job_vivo_na_fila_nao_e_tocado(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    envelhecer_jobs(pid)
    assert run_maintenance()["queue"]["requeued"] == 0
    assert get_queue().count == 1


def test_f2_carencia_para_jobs_recem_criados(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    get_redis().flushdb()  # entre o commit e o envio à fila, por exemplo
    assert run_maintenance()["queue"]["requeued"] == 0


def test_f2_limite_de_devolucoes_falha_sem_consumir_tentativa(client, alice, video, monkeypatch):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET options = '{\"requeues\": 3}' WHERE project_id = :p"),
                     {"p": pid})
    get_redis().flushdb()
    envelhecer_jobs(pid)
    assert run_maintenance()["queue"]["lost"] == 1
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "queue_lost"
    pipeline_prepare(monkeypatch)
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 202
    worker()
    assert status(client, alice, pid)["status"] == "completed"


def test_f2_redis_fora_do_ar_nao_quebra_a_manutencao(client, alice, video, monkeypatch):
    import app.services.queue as q

    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    get_redis().flushdb()
    envelhecer_jobs(pid)

    def quebrado(*a, **k):
        raise q.QueueUnavailable("fora")

    monkeypatch.setattr(q, "enqueue_task", quebrado)
    assert run_maintenance()["queue"] == "redis_indisponivel"
    monkeypatch.undo()
    assert run_maintenance()["queue"]["requeued"] == 1


# =============================================================== F2b — sinal de vida

def test_f2b_job_longo_com_sinal_de_vida_nao_e_marcado_como_perdido(client, alice, video, monkeypatch):
    s = get_settings()
    monkeypatch.setattr(s, "heartbeat_interval_seconds", 0.2)
    monkeypatch.setattr(s, "stale_heartbeat_seconds", 1)
    pipeline_prepare(monkeypatch, Stage("longo", "etapa longa", 5, lambda ctx: time.sleep(3)))
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    reports = []

    def manutencao_durante():
        for _ in range(4):
            time.sleep(0.7)
            reports.append(run_maintenance())

    t = threading.Thread(target=manutencao_durante)
    t.start()
    worker()
    t.join()
    assert all(r.get("stale_jobs_failed", 0) == 0 for r in reports if not r["skipped"])
    assert status(client, alice, pid)["status"] == "completed"


def test_f2b_job_sem_sinal_de_vida_e_detectado(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE projects SET status='processing' WHERE id=:p"), {"p": pid})
        conn.execute(text("UPDATE processing_jobs SET status='running' WHERE project_id=:p"), {"p": pid})
    envelhecer_jobs(pid, get_settings().stale_heartbeat_seconds + 5)
    assert run_maintenance()["stale_jobs_failed"] == 1
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "worker_lost"


def test_f2b_worker_real_morto_com_sigkill_e_recuperado(client, alice, long_speech_video, whisper_ready, monkeypatch):
    """Worker real (fork) morto no meio de um job: o sinal de vida para, a manutenção detecta e o usuário
    consegue processar de novo, com uma única cobrança."""
    pid = projeto_pronto(client, alice, long_speech_video)
    client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers)
    env = {**os.environ, "WORKER_TMP_DIR": get_settings().worker_tmp_dir, "HEARTBEAT_INTERVAL_SECONDS": "0.3"}
    proc = subprocess.Popen([sys.executable, "-m", "app.worker.run"], cwd=BACKEND, env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline and status(client, alice, pid)["status"] != "processing":
            time.sleep(0.05)
        assert status(client, alice, pid)["status"] == "processing", "o job não chegou a rodar"
        time.sleep(1.0)  # pelo menos um sinal de vida registrado
        # Queda da máquina: o RQ põe o processo do job em outra sessão (setsid), então matamos o worker
        # principal primeiro (ele não pode mais avisar) e depois o processo do job.
        filhos = _filhos(proc.pid)  # via /proc (a imagem Debian enxuta não tem pgrep)
        os.killpg(proc.pid, signal.SIGKILL)
        for pid_filho in filhos:
            try:
                os.kill(pid_filho, signal.SIGKILL)
            except ProcessLookupError:
                pass
    finally:
        # nunca deixar um worker real vivo: ele pegaria os jobs dos testes seguintes
        for pid_filho in _filhos(proc.pid):
            try:
                os.kill(pid_filho, signal.SIGKILL)
            except ProcessLookupError:
                pass
        if proc.poll() is None:
            os.killpg(proc.pid, signal.SIGKILL)
        proc.wait(10)
    assert status(client, alice, pid)["job"]["status"] == "running"  # ninguém avisou: ficou "rodando"
    monkeypatch.setattr(get_settings(), "stale_heartbeat_seconds", 2)
    time.sleep(2.5)
    assert run_maintenance()["stale_jobs_failed"] == 1
    st = status(client, alice, pid)
    assert st["status"] == "failed" and st["error_code"] == "worker_lost"
    pipeline_prepare(monkeypatch)
    assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 202
    worker()
    assert status(client, alice, pid)["status"] == "completed"
    with get_sessionmaker()() as db:
        assert db.scalar(select(func.count()).select_from(UsageLedger).where(
            UsageLedger.kind == "processed_seconds", UsageLedger.project_id == uuid.UUID(pid))) == 1


# =============================================================== F3 — exclusão com job preso ou rodando

def _filhos(pid: int) -> list[int]:
    out = []
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if int(Path(f"/proc/{d}/stat").read_text().rsplit(")", 1)[1].split()[1]) == pid:
                    out.append(int(d))
            except (OSError, IndexError, ValueError):
                pass
    return out


def projeto_com_regeneracao(client, user, video) -> tuple[str, uuid.UUID, uuid.UUID]:
    """Projeto concluído com um corte e uma regeneração na fila (linhas criadas direto no banco)."""
    pid = projeto_pronto(client, user, video)
    with get_sessionmaker()() as db:
        db.execute(text("UPDATE projects SET status='completed', progress=100 WHERE id=:p"), {"p": pid})
        clip = Clip(project_id=uuid.UUID(pid), owner_id=user.id, title="Corte", score=50, start_seconds=0,
                    end_seconds=16, status="pending", layout="center_crop", render_version=1)
        db.add(clip)
        db.flush()
        job = ProcessingJob(project_id=uuid.UUID(pid), owner_id=user.id, clip_id=clip.id, job_type="render_clip",
                            status="queued", idempotency_key=f"render_clip:{clip.id}:1",
                            options={"layout": "center_crop"})
        db.add(job)
        db.commit()
        return pid, clip.id, job.id


def test_f3_regeneracao_presa_na_fila_nao_bloqueia_exclusao(client, alice, video):
    from app.worker.tasks import render_clip

    pid, _, job_id = projeto_com_regeneracao(client, alice, video)  # nunca foi para o Redis: presa
    assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 204
    assert get_storage().list_keys(project_prefix(alice.id, uuid.UUID(pid))) == []
    with get_sessionmaker()() as db:
        assert db.get(Project, uuid.UUID(pid)) is None and db.get(ProcessingJob, job_id) is None
    assert render_clip(str(job_id)) == "skipped"  # se o job ainda estivesse na fila, seria ignorado


def test_f3_job_rodando_bloqueia_ate_terminar(client, alice, video):
    pid, _, job_id = projeto_com_regeneracao(client, alice, video)
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET status='running' WHERE id=:j"), {"j": job_id})
    r = client.delete(f"/api/v1/projects/{pid}", headers=alice.headers)
    assert r.status_code == 409 and "aguarde" in r.json()["detail"].lower()
    with get_engine().begin() as conn:
        conn.execute(text("UPDATE processing_jobs SET status='succeeded' WHERE id=:j"), {"j": job_id})
    assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 204


def test_f3_projeto_travado_por_outra_operacao_retorna_409(client, alice, video):
    pid = projeto_pronto(client, alice, video)
    with get_engine().connect() as conn:
        conn.execute(text("SELECT 1 FROM projects WHERE id=:p FOR UPDATE"), {"p": pid})
        assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 409
        conn.rollback()
    assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 204


@pytest.mark.parametrize("rodada", range(4))
def test_f3_corrida_exclusao_x_inicio_do_worker(client, alice, video, monkeypatch, rodada):
    """Exclusão e início da regeneração ao mesmo tempo: ou exclui (e o worker ignora o job), ou recusa com
    409 (e o job termina normalmente). Nunca exceção, job órfão rodando ou arquivo esquecido."""
    import app.pipeline.stages as st
    import app.rendering.clips as rc
    from app.worker.tasks import render_clip

    monkeypatch.setattr(st, "prepare_source", lambda ctx: None)
    monkeypatch.setattr(rc, "render_one", lambda ctx, clip_id, **kw: time.sleep(0.3))
    pid, _, job_id = projeto_com_regeneracao(client, alice, video)
    barreira = threading.Barrier(2)
    out = {}

    def worker_thread():
        barreira.wait()
        if rodada % 2:
            time.sleep(0.01)
        out["worker"] = render_clip(str(job_id))

    def delete_thread():
        barreira.wait()
        if not rodada % 2:
            time.sleep(0.01)
        out["delete"] = client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code

    ts = [threading.Thread(target=worker_thread), threading.Thread(target=delete_thread)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    with get_sessionmaker()() as db:
        job = db.get(ProcessingJob, job_id)
        projeto = db.get(Project, uuid.UUID(pid))
    if out["delete"] == 204:
        assert projeto is None and job is None and out["worker"] in ("skipped", "canceled")
        assert get_storage().list_keys(project_prefix(alice.id, uuid.UUID(pid))) == []
    else:
        assert out["delete"] == 409 and out["worker"] == "succeeded" and job.status == "succeeded"
        assert client.delete(f"/api/v1/projects/{pid}", headers=alice.headers).status_code == 204


# =============================================================== Falha de memória (achado do teste de capacidade)

def test_falha_de_memoria_vira_resource_limit_sem_consumir_tentativa(client, alice, video, monkeypatch):
    """Erro real observado no teste de capacidade: o ONNX Runtime (filtro de voz) estoura o limite de memória
    virtual e a transcrição devolve um erro genérico. Deve virar `resource_limit` e não gastar tentativas."""
    import app.transcription as tr
    from app.transcription import TranscriptionError

    class SemMemoria:
        name, audio_format = "teste", "wav"

        def transcribe(self, audio_path, duration, progress):
            raise TranscriptionError("transcription_failed", "Falha ao transcrever o áudio do vídeo.",
                                     detail="Fail: [ONNXRuntimeError] : 1 : FAIL : std::bad_alloc")

    monkeypatch.setattr(tr, "get_transcriber", lambda s: SemMemoria())
    pid = projeto_pronto(client, alice, video)
    for _ in range(get_settings().max_process_attempts + 1):  # não esgota as tentativas
        assert client.post(f"/api/v1/projects/{pid}/process", headers=alice.headers).status_code == 202
        worker()
        st = status(client, alice, pid)
        assert st["status"] == "failed" and st["error_code"] == "resource_limit"
        assert "limite de memória" in st["status_message"]
