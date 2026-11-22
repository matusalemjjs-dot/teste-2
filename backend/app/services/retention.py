"""Retenção e exclusão automática de dados (LGPD: minimização e prazos transparentes).

Regras (configuráveis em RETENTION_*):
- projeto sem vídeo enviado (status `pending`) há mais de RETENTION_ABANDONED_HOURS → excluído;
- projeto concluído há mais de RETENTION_SOURCE_DAYS → vídeo original, transcrição e trechos candidatos
  apagados; os cortes continuam disponíveis, mas não podem mais ser regenerados;
- projeto concluído (ou parado, pela última atividade) há mais de RETENTION_PROJECT_DAYS → excluído inteiro;
- arquivo no storage sem registro no banco há mais de RETENTION_ORPHAN_HOURS → apagado;
- detalhe técnico de erros de jobs com mais de RETENTION_ERROR_DETAIL_DAYS → esvaziado (o código fica);
- eventos de auditoria com mais de RETENTION_AUDIT_DAYS → apagados.

Segurança: apaga primeiro do storage e depois do banco; se o storage falhar, o banco não muda e a próxima
rodada tenta de novo. Projetos com job ativo ou travados por outra operação são pulados nesta rodada.
"""

import logging
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, func, select, update
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event
from app.models import (
    AuditEvent,
    ClipCandidate,
    MediaAsset,
    ProcessingJob,
    Project,
    TranscriptSegment,
)
from app.models.enums import AssetKind, AssetStatus, JobStatus, JobType
from app.models.enums import ProjectStatus as S
from app.services import audit
from app.services.projects import InvalidTransition, purge_project
from app.services.storage import Storage, StorageError

logger = logging.getLogger("autoclip.retention")
ACTIVE_JOB = (JobStatus.queued.value, JobStatus.running.value)


@dataclass
class RetentionReport:
    abandoned_deleted: int = 0
    sources_expired: int = 0
    projects_deleted: int = 0
    orphans_deleted: int = 0
    error_details_cleared: int = 0
    audit_deleted: int = 0
    skipped_busy: int = 0
    failures: list[str] = field(default_factory=list)


def completed_at(db: Session, project_id: uuid.UUID) -> datetime | None:
    return db.scalar(select(func.max(ProcessingJob.finished_at)).where(
        ProcessingJob.project_id == project_id, ProcessingJob.job_type == JobType.process_project.value,
        ProcessingJob.status == JobStatus.succeeded.value))


def source_available(db: Session, project_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count()).select_from(MediaAsset).where(
        MediaAsset.project_id == project_id, MediaAsset.kind == AssetKind.source_video.value,
        MediaAsset.status == AssetStatus.ready.value)) > 0


def retention_dates(db: Session, project: Project, settings: Settings) -> dict:
    """Datas exibidas ao usuário (transparência). None quando ainda não se aplica."""
    done = completed_at(db, project.id) if S(project.status) == S.completed else None
    ref = done or project.updated_at
    return {
        "source_available": source_available(db, project.id),
        "source_expires_at": done + timedelta(days=settings.retention_source_days) if done else None,
        "project_expires_at": ref + timedelta(days=settings.retention_project_days)
        if S(project.status) not in (S.pending, S.queued, S.processing) else None,
    }


def _has_active_job(db: Session, project_id: uuid.UUID) -> bool:
    return db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.project_id == project_id, ProcessingJob.status.in_(ACTIVE_JOB))) > 0


def _purge(db: Session, storage: Storage, project: Project, action: str, rep: RetentionReport) -> bool:
    try:
        purge_project(db, storage, project, actor_id=None, action=action)
        return True
    except InvalidTransition:
        rep.skipped_busy += 1
    except StorageError as exc:
        db.rollback()
        rep.failures.append(f"{action}:{project.id}:{exc}")
    return False


def expire_source(db: Session, storage: Storage, project_id: uuid.UUID, rep: RetentionReport) -> None:
    try:
        project = db.scalar(select(Project).where(Project.id == project_id).with_for_update(nowait=True))
    except OperationalError:
        db.rollback()
        rep.skipped_busy += 1
        return
    if project is None or _has_active_job(db, project_id):
        db.rollback()
        rep.skipped_busy += 1 if project is not None else 0
        return
    assets = db.scalars(select(MediaAsset).where(
        MediaAsset.project_id == project_id, MediaAsset.kind.in_((AssetKind.source_video.value, AssetKind.audio.value)),
        MediaAsset.status == AssetStatus.ready.value)).all()
    try:
        for a in assets:
            storage.delete(a.storage_key)
    except StorageError as exc:
        db.rollback()
        rep.failures.append(f"source:{project_id}:{exc}")
        return
    for a in assets:
        a.status = AssetStatus.deleted.value
    db.execute(delete(TranscriptSegment).where(TranscriptSegment.project_id == project_id))
    db.execute(delete(ClipCandidate).where(ClipCandidate.project_id == project_id))
    audit.record(db, None, "retention.source_expired", "project", project_id, {"files": len(assets)})
    db.commit()
    rep.sources_expired += 1


def run_retention(db: Session, storage: Storage, settings: Settings, now: datetime | None = None) -> RetentionReport:
    now = now or datetime.now(UTC)
    rep = RetentionReport()
    if not settings.retention_enabled:
        return rep

    # 1. projetos abandonados sem vídeo
    for p in db.scalars(select(Project).where(
            Project.status == S.pending.value,
            Project.updated_at < now - timedelta(hours=settings.retention_abandoned_hours))).all():
        if _purge(db, storage, p, "retention.abandoned_deleted", rep):
            rep.abandoned_deleted += 1

    # 2. projetos inteiros vencidos (concluídos: pela conclusão; demais: pela última atividade)
    project_limit = now - timedelta(days=settings.retention_project_days)
    done_sub = (select(ProcessingJob.project_id, func.max(ProcessingJob.finished_at).label("done"))
                .where(ProcessingJob.job_type == JobType.process_project.value,
                       ProcessingJob.status == JobStatus.succeeded.value)
                .group_by(ProcessingJob.project_id).subquery())
    expired = db.scalars(
        select(Project).outerjoin(done_sub, done_sub.c.project_id == Project.id).where(
            Project.status.in_((S.uploaded.value, S.completed.value, S.failed.value, S.canceled.value)),
            func.coalesce(done_sub.c.done, Project.updated_at) < project_limit)).all()
    for p in expired:
        if _purge(db, storage, p, "retention.project_deleted", rep):
            rep.projects_deleted += 1

    # 3. vídeo original (e dados derivados só dele) vencido
    source_limit = now - timedelta(days=settings.retention_source_days)
    ids = db.scalars(
        select(Project.id).join(done_sub, done_sub.c.project_id == Project.id).where(
            Project.status == S.completed.value, done_sub.c.done < source_limit,
            Project.id.in_(select(MediaAsset.project_id).where(
                MediaAsset.kind == AssetKind.source_video.value, MediaAsset.status == AssetStatus.ready.value)))).all()
    db.rollback()
    for pid in ids:
        expire_source(db, storage, pid, rep)

    # 4. arquivos órfãos (sem registro no banco), ex.: reenvio pela URL antiga após a validação
    orphan_limit = now - timedelta(hours=settings.retention_orphan_hours)
    try:
        for key, modified in storage.list_objects("users/"):
            if modified >= orphan_limit:
                continue
            if db.scalar(select(func.count()).select_from(MediaAsset).where(MediaAsset.storage_key == key)):
                continue
            storage.delete(key)
            rep.orphans_deleted += 1
    except StorageError as exc:
        rep.failures.append(f"orphans:{exc}")
    db.rollback()

    # 5. detalhe técnico de erros e auditoria antiga
    r = db.execute(update(ProcessingJob).where(
        ProcessingJob.error_detail.is_not(None),
        ProcessingJob.finished_at < now - timedelta(days=settings.retention_error_detail_days),
    ).values(error_detail=None))
    rep.error_details_cleared = r.rowcount or 0
    audit_limit = now - timedelta(days=settings.retention_audit_days)
    r = db.execute(delete(AuditEvent).where(AuditEvent.created_at < audit_limit))
    rep.audit_deleted = r.rowcount or 0
    db.commit()

    log_event(logger, "Retenção executada", **{k: v for k, v in rep.__dict__.items() if k != "failures"},
              failures=len(rep.failures))
    return rep
