"""Solicitação de processamento, cancelamento e recuperação de jobs perdidos."""

import logging
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event
from app.models import ProcessingJob, Project
from app.models.enums import JobStatus, JobType
from app.models.enums import ProjectStatus as S
from app.services import audit, queue
from app.services.projects import transition

logger = logging.getLogger("autoclip.jobs")

# Falhas de infraestrutura não consomem as tentativas do usuário.
INFRA_ERROR_CODES = {"queue_unavailable", "worker_lost", "insufficient_disk", "stage_unavailable", "storage_error"}
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
    if project is not None and S(project.status) in (S.queued, S.processing):
        transition(project, S.failed, message)
        project.error_code = code
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
    """Marca como falhos os jobs 'running' cujo worker morreu (sem atualização além do timeout)."""
    now = now or datetime.now(UTC)
    limit = now - timedelta(seconds=settings.job_timeout_seconds + settings.stale_job_grace_seconds)
    stale = db.scalars(select(ProcessingJob.id).where(ProcessingJob.status == JobStatus.running.value,
                                                      ProcessingJob.updated_at < limit)).all()
    db.rollback()
    for job_id in stale:
        mark_failed(db, job_id, "worker_lost",
                    "O processamento foi interrompido inesperadamente. Tente novamente.", detail="stale sweep")
    return len(stale)
