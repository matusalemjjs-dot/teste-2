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
from app.worker.heartbeat import Heartbeat
from app.worker.tmp import job_dir

logger = logging.getLogger("autoclip.worker")


MEMORY_SIGNS = ("bad_alloc", "MemoryError", "Cannot allocate memory", "out of memory")
MEMORY_MESSAGE = "O processamento excedeu o limite de memória do servidor. Tente novamente mais tarde."


def classify(exc) -> tuple[str, str]:
    """Falhas de memória vindas de bibliotecas (Whisper/ONNX, FFmpeg) chegam como erros genéricos da etapa;
    são reclassificadas como `resource_limit` (infraestrutura: não consome tentativa do usuário)."""
    detail = exc.detail or ""
    if any(sign in detail for sign in MEMORY_SIGNS):
        return "resource_limit", MEMORY_MESSAGE
    return exc.code, exc.message


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


def _finish_success(job_id: uuid.UUID, message: str = "Processamento concluído") -> bool:
    with get_sessionmaker()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == job_id).with_for_update())
        project = db.scalar(select(Project).where(Project.id == job.project_id).with_for_update())
        if project is None or S(project.status) != S.processing:
            db.rollback()
            return False
        job.status = JobStatus.succeeded.value
        job.stage = "done"
        job.finished_at = datetime.now(UTC)
        transition(project, S.completed, message)
        project.progress = 100
        from app.services.quotas import charge_processed

        charge_processed(db, project, job.id, get_settings())  # mesma transação: cobra só se concluir
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
    heartbeat = Heartbeat(jid, settings.heartbeat_interval_seconds).__enter__()
    try:
        done = 0
        for stage in pipeline:
            ctx.checkpoint()
            ctx.stage_range = (done * 100 // total, (done + stage.weight) * 100 // total)
            ctx.progress(ctx.stage_range[0], f"Etapa: {stage.label}", stage=stage.name)
            stage.run(ctx)
            done += stage.weight
        ctx.checkpoint()
        result = "succeeded" if _finish_success(jid, ctx.data.get("final_message", "Processamento concluído")) \
            else "canceled"
        if result == "canceled":
            _finish_canceled(jid)
    except Canceled:
        _finish_canceled(jid)
        result = "canceled"
    except PipelineError as exc:
        code, message = classify(exc)
        with get_sessionmaker()() as db:
            mark_failed(db, jid, code, message, redact(exc.detail) if exc.detail else None)
        result = "failed"
    except BaseException as exc:  # inclui JobTimeoutException do RQ
        from rq.timeouts import JobTimeoutException

        if isinstance(exc, JobTimeoutException):
            code, msg = "timeout", "O processamento excedeu o tempo máximo permitido."
        elif isinstance(exc, MemoryError):
            code, msg = "resource_limit", MEMORY_MESSAGE
        else:
            code, msg = "internal_error", "Ocorreu um erro inesperado no processamento. Tente novamente."
        logger.exception("Falha no job", extra={"extra_fields": {"job_id": job_id, "code": code}})
        with get_sessionmaker()() as db:
            mark_failed(db, jid, code, msg, redact(traceback.format_exc()))
        if not isinstance(exc, Exception):
            raise  # KeyboardInterrupt/SystemExit: deixa o worker encerrar
        result = "failed"
    finally:
        heartbeat.__exit__()
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


def render_clip(job_id: str) -> str:
    """Regenera um corte (novo layout/legendas). Não altera o status do projeto."""
    from app.models import Clip
    from app.models.enums import ClipStatus
    from app.pipeline.stages import prepare_source
    from app.rendering.clips import render_one
    from app.rendering.render import RenderError

    settings = get_settings()
    jid = uuid.UUID(job_id)
    with get_sessionmaker()() as db:
        job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == jid).with_for_update())
        if job is None or job.status != JobStatus.queued.value:
            return "skipped"
        clip = db.scalar(select(Clip).where(Clip.id == job.clip_id).with_for_update())
        if clip is None:
            job.status, job.finished_at = JobStatus.canceled.value, datetime.now(UTC)
            db.commit()
            return "skipped"
        job.status, job.attempts, job.started_at, job.stage = JobStatus.running.value, job.attempts + 1, \
            datetime.now(UTC), "render"
        clip.status = ClipStatus.rendering.value
        project_id, owner_id, clip_id = job.project_id, job.owner_id, clip.id
        version = clip.render_version + 1
        opts = job.options or {}
        db.commit()

    workdir = job_dir(settings, job_id)
    ctx = JobContext(job_id=jid, project_id=project_id, owner_id=owner_id, workdir=workdir, settings=settings)
    heartbeat = Heartbeat(jid, settings.heartbeat_interval_seconds).__enter__()
    try:
        prepare_source(ctx)
        render_one(ctx, clip_id, layout=opts.get("layout", settings.render_default_layout),
                   subtitles=bool(opts.get("subtitles", True)), version=version)
        with get_sessionmaker()() as db:
            job = db.scalar(select(ProcessingJob).where(ProcessingJob.id == jid).with_for_update())
            if job is not None and job.status == JobStatus.running.value:  # não sobrescreve falha já registrada
                job.status, job.stage, job.finished_at = JobStatus.succeeded.value, "done", datetime.now(UTC)
                audit.record(db, owner_id, "clip.rendered", "clip", clip_id, {"version": version})
            db.commit()
        result = "succeeded"
    except Canceled:
        _finish_canceled(jid)
        result = "canceled"
    except (PipelineError, RenderError) as exc:
        code, message = classify(exc)
        with get_sessionmaker()() as db:
            mark_failed(db, jid, code, message, redact(exc.detail) if exc.detail else None)
        result = "failed"
    except BaseException as exc:
        with get_sessionmaker()() as db:
            mark_failed(db, jid, "internal_error", "Erro inesperado ao regenerar o corte.",
                        redact(traceback.format_exc()))
        if not isinstance(exc, Exception):
            raise
        result = "failed"
    finally:
        heartbeat.__exit__()
        shutil.rmtree(workdir, ignore_errors=True)
    log_event(logger, "Regeneração finalizada", job_id=job_id, result=result)
    return result
