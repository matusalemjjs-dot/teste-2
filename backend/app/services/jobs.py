"""Solicitação de processamento, cancelamento e recuperação de jobs perdidos."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event
from app.models import Clip, ProcessingJob, Project
from app.models.enums import ClipStatus, JobStatus, JobType
from app.models.enums import ProjectStatus as S
from app.services import audit, queue
from app.services.projects import transition

logger = logging.getLogger("autoclip.jobs")

# Falhas de infraestrutura não consomem as tentativas do usuário.
INFRA_ERROR_CODES = {"queue_unavailable", "worker_lost", "insufficient_disk", "stage_unavailable", "storage_error",
                     "transcription_unavailable", "transcription_misconfigured", "analysis_unavailable",
                     "analysis_misconfigured", "queue_lost", "resource_limit"}
ACTIVE = (JobStatus.queued.value, JobStatus.running.value)


class ProcessRejected(Exception):
    def __init__(self, code: str, message: str, http_status: int = 409):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


class Busy(Exception):
    pass


def latest_job(db: Session, project_id: uuid.UUID) -> ProcessingJob | None:
    return db.scalar(select(ProcessingJob).where(ProcessingJob.project_id == project_id,
                                                 ProcessingJob.job_type == JobType.process_project.value)
                     .order_by(ProcessingJob.created_at.desc()).limit(1))


def _counted_failures(db: Session, project_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.project_id == project_id, ProcessingJob.status == JobStatus.failed.value,
        ProcessingJob.error_code.not_in(INFRA_ERROR_CODES) | ProcessingJob.error_code.is_(None))) or 0


def _job_count(db: Session, project_id: uuid.UUID) -> int:
    return db.scalar(select(func.count()).select_from(ProcessingJob)
                     .where(ProcessingJob.project_id == project_id)) or 0


def request_processing(db: Session, settings: Settings, owner_id: uuid.UUID,
                       project_id: uuid.UUID) -> tuple[ProcessingJob, bool] | None:
    """Idempotente: se já existe processamento ativo ou concluído, devolve o job existente (created=False)."""
    try:
        project = db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
                            .with_for_update(nowait=True))
    except OperationalError as exc:
        db.rollback()
        raise Busy() from exc
    if project is None:
        return None

    status = S(project.status)
    if status in (S.queued, S.processing, S.completed):
        job = latest_job(db, project.id)
        db.rollback()
        if job is not None:
            return job, False
        raise ProcessRejected("invalid_state", "Estado do projeto inconsistente. Contate o suporte.")
    if status == S.pending:
        db.rollback()
        raise ProcessRejected("upload_required", "Envie e finalize o upload do vídeo antes de processar.")
    if status == S.canceled:
        db.rollback()
        raise ProcessRejected("canceled", "Este projeto foi cancelado. Crie um novo projeto para processar o vídeo.")
    if status == S.failed and _counted_failures(db, project.id) >= settings.max_process_attempts:
        db.rollback()
        raise ProcessRejected("attempts_exhausted",
                              f"Limite de {settings.max_process_attempts} tentativas atingido para este projeto.")

    from app.services.quotas import check_process

    try:
        check_process(db, owner_id, project, settings)
    except Exception:
        db.rollback()
        raise
    attempt = _job_count(db, project.id) + 1
    job = ProcessingJob(project_id=project.id, owner_id=owner_id, job_type=JobType.process_project.value,
                        status=JobStatus.queued.value, idempotency_key=f"process_project:{project.id}:{attempt}",
                        stage="queued")
    db.add(job)
    transition(project, S.queued, "Na fila para processamento")
    project.progress = 0
    project.error_code = None
    try:
        db.flush()
    except IntegrityError as exc:  # corrida improvável (o lock já serializa); trata como repetição
        db.rollback()
        raise Busy() from exc
    job.queue_job_id = str(job.id)
    audit.record(db, owner_id, "project.process", "project", project.id, {"job_id": str(job.id), "attempt": attempt})
    db.commit()

    try:
        queue.enqueue_process(str(job.id))
    except queue.QueueUnavailable:
        mark_failed(db, job.id, "queue_unavailable",
                    "Fila de processamento indisponível no momento. Tente novamente em instantes.", detail=None)
        raise
    log_event(logger, "Processamento enfileirado", project_id=str(project.id), job_id=str(job.id))
    db.refresh(job)
    return job, True


def mark_failed(db: Session, job_id: uuid.UUID, code: str, message: str, detail: str | None) -> None:
    """Marca job e projeto como falhos, sem sobrescrever cancelamento ou conclusão."""
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
    if job is None or job.status not in ACTIVE:
        db.rollback()
        return
    project = db.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
    job.status = JobStatus.failed.value
    job.error_code = code
    job.error_detail = detail[:4000] if detail else None
    job.finished_at = datetime.now(UTC)
    if job.job_type == JobType.process_project.value and project is not None \
            and S(project.status) in (S.queued, S.processing):
        transition(project, S.failed, message)
        project.error_code = code
    if job.clip_id is not None:  # regeneração: mantém a versão anterior do corte, se existir
        clip = db.scalar(select(Clip).where(Clip.id == job.clip_id).with_for_update())
        if clip is not None:
            clip.status = ClipStatus.ready.value if clip.video_asset_id else ClipStatus.failed.value
            clip.error_code = code
    audit.record(db, job.owner_id, "job.failed", "processing_job", job.id, {"code": code})
    db.commit()


def cancel_active_job(db: Session, project_id: uuid.UUID) -> None:
    """Chamado junto com o cancelamento do projeto (mesma transação; o commit fica com quem chama)."""
    job = db.scalar(select(ProcessingJob).where(ProcessingJob.project_id == project_id,
                                                ProcessingJob.status.in_(ACTIVE)).with_for_update())
    if job is None:
        return
    was_queued = job.status == JobStatus.queued.value
    job.status = JobStatus.canceled.value
    job.finished_at = datetime.now(UTC)
    if was_queued:
        queue.cancel_queued(str(job.id))
    # Se estiver rodando, o worker percebe o cancelamento no próximo ponto de verificação.


def sweep_stale_jobs(db: Session, settings: Settings, now: datetime | None = None) -> int:
    """Marca como falhos os jobs 'running' sem sinal de vida (worker morto: queda da máquina, SIGKILL,
    falta de memória). O worker vivo registra atividade a cada `heartbeat_interval_seconds`."""
    now = now or datetime.now(UTC)
    limit = now - timedelta(seconds=settings.stale_heartbeat_seconds)
    stale = db.scalars(select(ProcessingJob.id).where(ProcessingJob.status == JobStatus.running.value,
                                                      ProcessingJob.updated_at < limit)).all()
    db.rollback()
    for job_id in stale:
        mark_failed(db, job_id, "worker_lost",
                    "O processamento foi interrompido inesperadamente. Tente novamente.", detail="sem sinal de vida")
    return len(stale)


TASK_BY_TYPE = {JobType.process_project.value: queue.PROCESS_TASK, JobType.render_clip.value: queue.RENDER_CLIP_TASK}
RQ_ALIVE = {"queued", "started", "deferred", "scheduled"}


def reconcile_queued(db: Session, settings: Settings, now: datetime | None = None) -> dict:
    """Jobs 'queued' no banco que não estão vivos no Redis (Redis reiniciado, fila apagada) são devolvidos
    à fila com o MESMO id. É seguro repetir: o worker só executa jobs que ainda estão 'queued' no banco.
    Depois de `max_requeues` devoluções, o job falha com `queue_lost` (não consome tentativa do usuário)."""
    from rq.exceptions import NoSuchJobError
    from rq.job import Job

    now = now or datetime.now(UTC)
    limit = now - timedelta(seconds=settings.queue_reconcile_grace_seconds)
    ids = db.scalars(select(ProcessingJob.id).where(ProcessingJob.status == JobStatus.queued.value,
                                                    ProcessingJob.updated_at < limit)).all()
    db.rollback()
    result = {"checked": len(ids), "requeued": 0, "lost": 0}
    redis = queue.get_redis()
    for job_id in ids:
        try:
            alive = Job.fetch(str(job_id), connection=redis).get_status() in RQ_ALIVE
        except NoSuchJobError:
            alive = False
        if alive:
            continue
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id,
                                                    ProcessingJob.status == JobStatus.queued.value)
                        .with_for_update(skip_locked=True))
        if job is None:  # mudou de estado ou está travado por outra operação
            db.rollback()
            continue
        requeues = int((job.options or {}).get("requeues", 0))
        if requeues >= settings.max_requeues:
            db.rollback()
            mark_failed(db, job_id, "queue_lost", "O processamento se perdeu na fila. Tente novamente.",
                        detail=f"requeues={requeues}")
            result["lost"] += 1
            continue
        try:  # envia ANTES de gravar: se o Redis falhar, nada muda e a próxima rodada tenta de novo
            queue.enqueue_task(TASK_BY_TYPE[job.job_type], str(job_id))
        except queue.QueueUnavailable:
            db.rollback()
            raise
        job.options = {**(job.options or {}), "requeues": requeues + 1}
        audit.record(db, job.owner_id, "job.requeued", "processing_job", job.id, {"requeues": requeues + 1})
        db.commit()
        result["requeued"] += 1
        log_event(logger, "Job devolvido à fila", job_id=str(job_id), requeues=requeues + 1)
    return result


def queued_job_ids_for_delete(db: Session, project_id: uuid.UUID) -> list[uuid.UUID]:
    """Para excluir um projeto: trava os jobs ativos; recusa se algum estiver rodando; devolve os da fila
    (o chamador cancela no Redis depois do commit; no banco eles somem junto com o projeto)."""
    jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.project_id == project_id,
                                                  ProcessingJob.status.in_(ACTIVE)).with_for_update()).all()
    if any(j.status == JobStatus.running.value for j in jobs):
        raise JobRunning()
    return [j.id for j in jobs]


class JobRunning(Exception):
    pass


def active_job_exists(db: Session, project_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.project_id == project_id, ProcessingJob.status.in_(ACTIVE))) > 0


def request_regenerate(db: Session, owner_id: uuid.UUID, clip_id: uuid.UUID, layout: str,
                       subtitles: bool) -> tuple[ProcessingJob, bool] | None:
    """Idempotente: com uma regeneração do mesmo corte na fila/em andamento, devolve o job existente."""
    project_id = db.scalar(select(Clip.project_id).where(Clip.id == clip_id, Clip.owner_id == owner_id))
    if project_id is None:
        return None
    # Ordem fixa de travas: PROJETO antes do CORTE (a mesma da retenção e da exclusão), para não haver deadlock.
    # FOR SHARE: a retenção (FOR UPDATE NOWAIT) pula o projeto enquanto este pedido estiver aberto, ou este
    # pedido espera a retenção terminar e então vê que o original expirou.
    project = db.scalar(select(Project).where(Project.id == project_id).with_for_update(read=True))
    try:
        clip = db.scalar(select(Clip).where(Clip.id == clip_id, Clip.owner_id == owner_id).with_for_update(nowait=True))
    except OperationalError as exc:
        db.rollback()
        raise Busy() from exc
    if clip is None or project is None:
        db.rollback()
        return None
    active = db.scalar(select(ProcessingJob).where(ProcessingJob.clip_id == clip.id,
                                                   ProcessingJob.status.in_(ACTIVE)))
    if active is not None:
        db.rollback()
        return active, False
    if S(project.status) != S.completed:
        db.rollback()
        raise ProcessRejected("invalid_state", "Só é possível regenerar cortes de projetos concluídos.")
    from app.services.retention import source_available

    if not source_available(db, project.id):
        db.rollback()
        raise ProcessRejected("source_expired", "O vídeo original deste projeto já foi apagado pela política de "
                                                "retenção. Os cortes continuam disponíveis, mas não podem ser "
                                                "regenerados.")
    from app.core.config import get_settings
    from app.models import AuditEvent

    settings = get_settings()
    if settings.quotas_enabled and settings.max_regenerations_per_day > 0:
        today = db.scalar(select(func.count()).select_from(AuditEvent).where(
            AuditEvent.actor_id == owner_id, AuditEvent.action == "clip.regenerate",
            AuditEvent.created_at >= datetime.now(UTC) - timedelta(hours=24))) or 0
        if today >= settings.max_regenerations_per_day:
            db.rollback()
            raise ProcessRejected("regeneration_limit", f"Limite de {settings.max_regenerations_per_day} "
                                                        "regenerações por dia atingido. Tente amanhã.", 429)
    n = db.scalar(select(func.count()).select_from(ProcessingJob).where(ProcessingJob.clip_id == clip.id)) or 0
    job = ProcessingJob(project_id=project.id, owner_id=owner_id, clip_id=clip.id,
                        job_type=JobType.render_clip.value, status=JobStatus.queued.value,
                        idempotency_key=f"render_clip:{clip.id}:{n + 1}", stage="queued",
                        options={"layout": layout, "subtitles": subtitles})
    db.add(job)
    clip.status = ClipStatus.pending.value
    db.flush()
    job.queue_job_id = str(job.id)
    audit.record(db, owner_id, "clip.regenerate", "clip", clip.id, {"job_id": str(job.id), "layout": layout})
    db.commit()
    try:
        queue.enqueue_task(queue.RENDER_CLIP_TASK, str(job.id))
    except queue.QueueUnavailable:
        mark_failed(db, job.id, "queue_unavailable", "Fila indisponível no momento. Tente novamente.", None)
        raise
    db.refresh(job)
    return job, True
