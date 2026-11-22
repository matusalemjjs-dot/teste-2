"""Modelo de dados (SQLAlchemy 2.0).

Regras de isolamento: toda tabela de dados de usuário carrega owner_id (direto ou via project)
e a API sempre filtra por owner_id. Exclusão de um perfil remove seus projetos em cascata
(LGPD: direito de exclusão). Registros de uso e auditoria mantêm-se com referência anulada.
"""

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.models.enums import (
    AssetKind,
    AssetStatus,
    ClipLayout,
    ClipStatus,
    JobStatus,
    JobType,
    ProjectStatus,
    SubscriptionStatus,
    UsageKind,
    check_in,
)


class Base(DeclarativeBase):
    pass


def _uuid_pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4,
                         server_default=func.gen_random_uuid())


def _created() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())


def _updated() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now())


class Profile(Base):
    __tablename__ = "profiles"
    # id == auth.users.id do Supabase (claim "sub")
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True)
    email: Mapped[str | None] = mapped_column(String(320))
    display_name: Mapped[str | None] = mapped_column(String(120))
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(check_in("status", ProjectStatus), name="ck_projects_status"),
        CheckConstraint("progress BETWEEN 0 AND 100", name="ck_projects_progress"),
        Index("ix_projects_owner_created", "owner_id", "created_at"),
        Index("ix_projects_status", "status"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ProjectStatus.pending,
                                        server_default=ProjectStatus.pending.value)
    progress: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    status_message: Mapped[str | None] = mapped_column(String(500))
    error_code: Mapped[str | None] = mapped_column(String(64))
    rights_confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    source_duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class MediaAsset(Base):
    __tablename__ = "media_assets"
    __table_args__ = (
        CheckConstraint(check_in("kind", AssetKind), name="ck_media_assets_kind"),
        CheckConstraint(check_in("status", AssetStatus), name="ck_media_assets_status"),
        Index("ix_media_assets_project_kind", "project_id", "kind"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=AssetStatus.pending,
                                        server_default=AssetStatus.pending.value)
    storage_key: Mapped[str] = mapped_column(String(1024), nullable=False, unique=True)
    content_type: Mapped[str | None] = mapped_column(String(100))
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    duration_seconds: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    checksum_sha256: Mapped[str | None] = mapped_column(String(64))
    probe: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    __table_args__ = (
        UniqueConstraint("project_id", "seq", name="uq_transcript_segments_project_seq"),
        CheckConstraint("end_seconds > start_seconds AND start_seconds >= 0", name="ck_transcript_segments_time"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    end_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    words: Mapped[list | None] = mapped_column(JSONB)
    confidence: Mapped[float | None] = mapped_column(Numeric(5, 4))
    created_at: Mapped[datetime] = _created()


class ClipCandidate(Base):
    __tablename__ = "clip_candidates"
    __table_args__ = (
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_clip_candidates_score"),
        CheckConstraint("end_seconds > start_seconds AND start_seconds >= 0", name="ck_clip_candidates_time"),
        Index("ix_clip_candidates_project_score", "project_id", "score"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    start_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    end_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    justification: Mapped[str] = mapped_column(Text, nullable=False)
    criteria: Mapped[dict | None] = mapped_column(JSONB)  # hook, context, emotion, relevance, retention, technical
    selected: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False, server_default="false")
    model_name: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = _created()


class Clip(Base):
    __tablename__ = "clips"
    __table_args__ = (
        CheckConstraint(check_in("status", ClipStatus), name="ck_clips_status"),
        CheckConstraint(check_in("layout", ClipLayout), name="ck_clips_layout"),
        CheckConstraint("end_seconds > start_seconds AND start_seconds >= 0", name="ck_clips_time"),
        CheckConstraint("score BETWEEN 0 AND 100", name="ck_clips_score"),
        Index("ix_clips_project_created", "project_id", "created_at"),
        Index("ix_clips_status", "status"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("clip_candidates.id", ondelete="SET NULL")
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    justification: Mapped[str | None] = mapped_column(Text)
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    start_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    end_seconds: Mapped[Decimal] = mapped_column(Numeric(10, 3), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=ClipStatus.pending,
                                        server_default=ClipStatus.pending.value)
    layout: Mapped[str] = mapped_column(String(20), nullable=False, default=ClipLayout.center_crop,
                                        server_default=ClipLayout.center_crop.value)
    render_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1, server_default="1")
    video_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    thumbnail_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    subtitle_asset_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("media_assets.id", ondelete="SET NULL")
    )
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class ProcessingJob(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint(check_in("job_type", JobType), name="ck_processing_jobs_type"),
        CheckConstraint(check_in("status", JobStatus), name="ck_processing_jobs_status"),
        Index("ix_processing_jobs_project", "project_id"),
        Index("ix_processing_jobs_status_created", "status", "created_at"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, index=True
    )
    clip_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True), ForeignKey("clips.id", ondelete="CASCADE"))
    job_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=JobStatus.queued,
                                        server_default=JobStatus.queued.value)
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    queue_job_id: Mapped[str | None] = mapped_column(String(100))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0, server_default="0")
    stage: Mapped[str | None] = mapped_column(String(50))
    options: Mapped[dict | None] = mapped_column(JSONB)  # ex.: layout/legendas ao regenerar um corte
    error_code: Mapped[str | None] = mapped_column(String(64))
    error_detail: Mapped[str | None] = mapped_column(Text)  # interno; nunca exposto ao usuário final
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class UsageLedger(Base):
    __tablename__ = "usage_ledger"
    __table_args__ = (
        CheckConstraint(check_in("kind", UsageKind), name="ck_usage_ledger_kind"),
        CheckConstraint("quantity >= 0", name="ck_usage_ledger_quantity"),
        Index("ix_usage_ledger_owner_created", "owner_id", "created_at"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    project_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("projects.id", ondelete="SET NULL"), index=True
    )
    job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("processing_jobs.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 3), nullable=False)
    estimated_cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), nullable=False, default=0,
                                                        server_default="0")
    # Garante que repetir uma operação não gera cobrança duplicada.
    idempotency_key: Mapped[str] = mapped_column(String(200), nullable=False, unique=True)
    created_at: Mapped[datetime] = _created()


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (CheckConstraint(check_in("status", SubscriptionStatus), name="ck_subscriptions_status"),)
    id: Mapped[uuid.UUID] = _uuid_pk()
    owner_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free", server_default="free")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default=SubscriptionStatus.active,
                                        server_default=SubscriptionStatus.active.value)
    monthly_minutes_quota: Mapped[int] = mapped_column(Integer, nullable=False, default=60, server_default="60")
    max_projects_per_day: Mapped[int] = mapped_column(Integer, nullable=False, default=10, server_default="10")
    current_period_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    current_period_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _created()
    updated_at: Mapped[datetime] = _updated()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    __table_args__ = (
        Index("ix_audit_events_actor_created", "actor_id", "created_at"),
        Index("ix_audit_events_resource", "resource_type", "resource_id"),
    )
    id: Mapped[uuid.UUID] = _uuid_pk()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("profiles.id", ondelete="SET NULL")
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    resource_type: Mapped[str] = mapped_column(String(32), nullable=False)
    resource_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    details: Mapped[dict | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = _created()
