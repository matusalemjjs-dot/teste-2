"""Cotas por usuário: minutos processados por mês e projetos criados por dia.

- Os minutos são debitados em `usage_ledger` (kind=processed_seconds) somente quando o projeto conclui, com
  chave idempotente por projeto: falhas não são cobradas e repetições não cobram duas vezes.
- Ao solicitar processamento, a cota considera: consumo do mês + projetos do usuário em fila/processando
  (reservados) + o vídeo atual. A linha da assinatura é travada para serializar pedidos simultâneos.
- Projetos por dia são contados pela auditoria (`project.create`), então excluir projetos não libera a cota.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AuditEvent, Project, Subscription, UsageLedger
from app.models.enums import ProjectStatus, SubscriptionStatus


class QuotaExceeded(Exception):
    def __init__(self, code: str, message: str, http_status: int = 402):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


@dataclass
class UsageSummary:
    plan: str
    status: str
    period_start: datetime
    period_end: datetime
    quota_minutes: int
    used_minutes: float
    reserved_minutes: float
    remaining_minutes: float
    projects_last_24h: int
    projects_per_day: int
    quotas_enabled: bool


def month_bounds(now: datetime) -> tuple[datetime, datetime]:
    start = now.astimezone(UTC).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    end = (start + timedelta(days=32)).replace(day=1)
    return start, end


def _subscription(db: Session, owner_id: uuid.UUID, settings: Settings, lock: bool) -> Subscription:
    q = select(Subscription).where(Subscription.owner_id == owner_id)
    sub = db.scalar(q.with_for_update() if lock else q)
    if sub is None:  # perfis antigos sem assinatura
        db.execute(insert(Subscription).values(
            owner_id=owner_id, monthly_minutes_quota=settings.plan_free_monthly_minutes,
            max_projects_per_day=settings.plan_free_projects_per_day).on_conflict_do_nothing())
        sub = db.scalar(q.with_for_update() if lock else q)
    return sub


def _used_seconds(db: Session, owner_id: uuid.UUID, start: datetime, end: datetime) -> float:
    return float(db.scalar(select(func.coalesce(func.sum(UsageLedger.quantity), 0)).where(
        UsageLedger.owner_id == owner_id, UsageLedger.kind == "processed_seconds",
        UsageLedger.created_at >= start, UsageLedger.created_at < end)) or 0)


def _reserved_seconds(db: Session, owner_id: uuid.UUID, exclude: uuid.UUID | None = None) -> float:
    q = select(func.coalesce(func.sum(Project.source_duration_seconds), 0)).where(
        Project.owner_id == owner_id,
        Project.status.in_((ProjectStatus.queued.value, ProjectStatus.processing.value)))
    if exclude:
        q = q.where(Project.id != exclude)
    return float(db.scalar(q) or 0)


def _projects_last_24h(db: Session, owner_id: uuid.UUID, now: datetime) -> int:
    return db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.actor_id == owner_id, AuditEvent.action == "project.create",
        AuditEvent.created_at >= now - timedelta(hours=24))) or 0


def summary(db: Session, owner_id: uuid.UUID, settings: Settings, now: datetime | None = None) -> UsageSummary:
    now = now or datetime.now(UTC)
    sub = _subscription(db, owner_id, settings, lock=False)
    start, end = month_bounds(now)
    used = _used_seconds(db, owner_id, start, end) / 60
    reserved = _reserved_seconds(db, owner_id) / 60
    return UsageSummary(plan=sub.plan, status=sub.status, period_start=start, period_end=end,
                        quota_minutes=sub.monthly_minutes_quota, used_minutes=round(used, 2),
                        reserved_minutes=round(reserved, 2),
                        remaining_minutes=round(max(0.0, sub.monthly_minutes_quota - used - reserved), 2),
                        projects_last_24h=_projects_last_24h(db, owner_id, now),
                        projects_per_day=sub.max_projects_per_day, quotas_enabled=settings.quotas_enabled)


def _check_active(sub: Subscription) -> None:
    if sub.status != SubscriptionStatus.active.value:
        raise QuotaExceeded("subscription_inactive", "Sua conta está suspensa. Entre em contato com o suporte.",
                            403)


def check_create_project(db: Session, owner_id: uuid.UUID, settings: Settings) -> None:
    """Chamar dentro da transação que cria o projeto (a trava da assinatura vale até o commit)."""
    sub = _subscription(db, owner_id, settings, lock=True)
    _check_active(sub)
    if not settings.quotas_enabled:
        return
    if _projects_last_24h(db, owner_id, datetime.now(UTC)) >= sub.max_projects_per_day:
        raise QuotaExceeded("project_limit",
                            f"Limite de {sub.max_projects_per_day} projetos por dia atingido. Tente amanhã.", 429)


def check_process(db: Session, owner_id: uuid.UUID, project: Project, settings: Settings) -> None:
    """Chamar dentro da transação que enfileira o processamento."""
    sub = _subscription(db, owner_id, settings, lock=True)
    _check_active(sub)
    if not settings.quotas_enabled:
        return
    if db.scalar(select(func.count()).select_from(UsageLedger).where(
            UsageLedger.idempotency_key == f"processed:{project.id}")):
        return  # já cobrado (ex.: reprocessamento futuro): não consome cota de novo
    start, end = month_bounds(datetime.now(UTC))
    needed = float(project.source_duration_seconds or 0)
    used = _used_seconds(db, owner_id, start, end) + _reserved_seconds(db, owner_id, exclude=project.id)
    available = sub.monthly_minutes_quota * 60 - used
    if needed > available:
        raise QuotaExceeded(
            "quota_exceeded",
            f"Este vídeo tem {needed / 60:.1f} min e restam {max(0.0, available) / 60:.1f} min da sua cota mensal "
            f"de {sub.monthly_minutes_quota} min.")


def charge_processed(db: Session, project: Project, job_id: uuid.UUID, settings: Settings) -> None:
    """Débito idempotente dos minutos processados (mesma transação que conclui o projeto)."""
    seconds = Decimal(str(project.source_duration_seconds or 0))
    cost = Decimal(f"{float(seconds) / 60 * settings.cost_per_processed_minute_usd:.6f}")
    db.execute(insert(UsageLedger).values(
        owner_id=project.owner_id, project_id=project.id, job_id=job_id, kind="processed_seconds",
        quantity=seconds, estimated_cost_usd=cost, idempotency_key=f"processed:{project.id}",
    ).on_conflict_do_nothing(index_elements=["idempotency_key"]))
