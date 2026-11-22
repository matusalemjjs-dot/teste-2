"""Convites do beta fechado.

Fontes: BETA_ALLOWED_EMAILS (configuração) e convites concedidos/revogados pelo comando de administração,
registrados como eventos de auditoria (`beta.invite` / `beta.revoke`, o mais recente vale) — sem tabela nova.
"""

import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import AuditEvent
from app.services import audit

_cache: dict = {"at": 0.0, "emails": set()}
CACHE_SECONDS = 30


def _normalize(email: str) -> str:
    return email.strip().lower()


def configured(settings: Settings) -> set[str]:
    return {_normalize(e) for e in settings.beta_allowed_emails.replace(";", ",").split(",") if e.strip()}


def granted(db: Session) -> set[str]:
    """Convites concedidos pelo administrador e não revogados (o evento mais recente de cada e-mail vale)."""
    state: dict[str, bool] = {}
    for action, details in db.execute(select(AuditEvent.action, AuditEvent.details).where(
            AuditEvent.action.in_(("beta.invite", "beta.revoke"))).order_by(AuditEvent.created_at, AuditEvent.id)):
        email = _normalize((details or {}).get("email", ""))
        if email:
            state[email] = action == "beta.invite"
    return {e for e, ok in state.items() if ok}


def revoked(db: Session) -> set[str]:
    state: dict[str, bool] = {}
    for action, details in db.execute(select(AuditEvent.action, AuditEvent.details).where(
            AuditEvent.action.in_(("beta.invite", "beta.revoke"))).order_by(AuditEvent.created_at, AuditEvent.id)):
        email = _normalize((details or {}).get("email", ""))
        if email:
            state[email] = action == "beta.revoke"
    return {e for e, r in state.items() if r}


def invalidate_cache() -> None:
    _cache["at"] = 0.0


def is_invited(db: Session, settings: Settings, email: str | None) -> bool:
    if not settings.beta_mode:
        return True
    if not email:
        return False
    email = _normalize(email)
    now = time.monotonic()
    if now - _cache["at"] > CACHE_SECONDS:
        _cache["emails"], _cache["revoked"], _cache["at"] = granted(db), revoked(db), now
    if email in _cache.get("revoked", set()):
        return False  # revogação pelo administrador vale mesmo para e-mails da configuração
    allowed = configured(settings) | _cache["emails"]
    domain = "@" + email.split("@")[-1]
    return email in allowed or domain in allowed


def grant(db: Session, email: str, operator: str) -> None:
    audit.record(db, None, "beta.invite", "invite", None, {"email": _normalize(email), "operador": operator})
    db.commit()
    invalidate_cache()


def revoke(db: Session, email: str, operator: str) -> None:
    audit.record(db, None, "beta.revoke", "invite", None, {"email": _normalize(email), "operador": operator})
    db.commit()
    invalidate_cache()
