import uuid

from sqlalchemy.orm import Session

from app.models import AuditEvent


def record(db: Session, actor_id: uuid.UUID | None, action: str, resource_type: str,
           resource_id: uuid.UUID | None = None, details: dict | None = None) -> None:
    """Registra evento de auditoria. Não armazenar tokens nem conteúdo sensível em `details`."""
    db.add(AuditEvent(actor_id=actor_id, action=action, resource_type=resource_type,
                      resource_id=resource_id, details=details))
