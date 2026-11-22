"""Tarefas executadas pelo worker RQ."""

import logging
import shutil
import traceback
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from app.core.config import get_settings
from app.core.logging import log_event, redact
from app.db.session import get_sessionmaker
from app.models import ProcessingJob, Project
from app.models.enums import JobStatus
from app.models.enums import ProjectStatus as S
from app.pipeline import stages as stages_mod
from app.pipeline.context import Canceled, JobContext, PipelineError
from app.services import audit
from app.services.jobs import mark_failed
from app.services.projects import transition
from app.worker.tmp import job_dir

logger = logging.getLogger("autoclip.worker")


def _start(job_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID] | None:
    """Passa job->running e projeto->processing. Retorna None se não houver nada a fazer (idempotência)."""
    with get_sessionmaker()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is None or job.status != JobStatus.queued.value:
            return None  # já executado, cancelado ou inexistente: não processa de novo
        project = db.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
        if project is None or S(project.status) != S.queued:
            job.status = JobStatus.canceled.value
            job.finished_at = datetime.now(UTC)
            db.commit()
            return None
        job.status = JobStatus.running.value
        job.attempts += 1
        job.started_at = datetime.now(UTC)
        job.stage = "starting"
        transition(project, S.processing, "Processamento iniciado")
        db.commit()
        return project.id, project.owner_id


def _finish_canceled(job_id: uuid.UUID) -> None:
    with get_sessionmaker()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        if job is not None and job.status in (JobStatus.running.value, JobStatus.queued.value):
            job.status = JobStatus.canceled.value
            job.finished_at = datetime.now(UTC)
            db.commit()


def _finish_success(job_id: uuid.UUID) -> bool:
    with get_sessionmaker()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        project = db.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
        if project is None or S(project.status) != S.processing:
            db.rollback()
            return False
        job.status = JobStatus.succeeded.value
        job.stage = "done"
        job.finished_at = datetime.now(UTC)
        transition(project, S.completed, "Processamento concluído")
        project.progress = 100
        audit.record(db, project.owner_id, "job.succeeded", "processing_job", job.id)
        db.commit()
        return True


def process_project(job_id: str) -> str:
    """Executa o pipeline de um projeto. Seguro para reexecução: um job só roda a partir de 'queued'."""
    settings = get_settings()
    jid = uuid.UUID(job_id)
    started = _start(jid)
    if started is None:
        log_event(logger, "Job ignorado (já tratado ou cancelado)", job_id=job_id)
        return "skipped"
    project_id, owner_id = started
    workdir = job_dir(settings, job_id)
    ctx = JobContext(job_id=jid, project_id=project_id, owner_id=owner_id, workdir=workdir, settings=settings)
    pipeline = stages_mod.get_pipeline()
    total = sum(s.weight for s in pipeline) or 1
    log_event(logger, "Job iniciado", job_id=job_id, project_id=str(project_id))
    try:
        done = 0
        for stage in pipeline:
            ctx.checkpoint()
            ctx.progress(done * 100 // total, f"Etapa: {stage.label}", stage=stage.name)
            stage.run(ctx)
            done += stage.weight
        ctx.checkpoint()
        result = "succeeded" if _finish_success(jid) else "canceled"
        if result == "canceled":
            _finish_canceled(jid)
    except Canceled:
        _finish_canceled(jid)
        result = "canceled"
    except PipelineError as exc:
        with get_sessionmaker()() as db:
            mark_failed(db, jid, exc.code, exc.message, redact(exc.detail) if exc.detail else None)
        result = "failed"
    except BaseException as exc:  # inclui JobTimeoutException do RQ
        from rq.timeouts import JobTimeoutException

        if isinstance(exc, JobTimeoutException):
            code, msg = "timeout", "O processamento excedeu o tempo máximo permitido."
        elif isinstance(exc, MemoryError):
            code, msg = "resource_limit", "O processamento excedeu o limite de memória."
        else:
            code, msg = "internal_error", "Ocorreu um erro inesperado no processamento. Tente novamente."
        logger.exception("Falha no job", extra={"extra_fields": {"job_id": job_id, "code": code}})
        with get_sessionmaker()() as db:
            mark_failed(db, jid, code, msg, redact(traceback.format_exc()))
        if not isinstance(exc, Exception):
            raise  # KeyboardInterrupt/SystemExit: deixa o worker encerrar
        result = "failed"
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    log_event(logger, "Job finalizado", job_id=job_id, result=result)
    return result


def on_work_horse_killed(job, retpid, ret_val, rusage) -> None:
    """Chamado pelo worker quando o processo do job morre (ex.: limite de memória, SIGKILL)."""
    try:
        jid = uuid.UUID(job.id)
    except (ValueError, TypeError):
        return
    with get_sessionmaker()() as db:
        mark_failed(db, jid, "worker_lost", "O processamento foi interrompido (limite de recursos). Tente novamente.",
                    detail=f"work horse killed ret={ret_val}")
    shutil.rmtree(job_dir_path(job.id), ignore_errors=True)


def job_dir_path(job_id: str):
    from app.worker.tmp import tmp_root
    return tmp_root(get_settings()) / f"job-{job_id}"
