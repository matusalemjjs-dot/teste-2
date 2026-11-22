"""Regras de negócio de projetos, incluindo a máquina de estados."""

import uuid
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Project
from app.models.enums import ProjectStatus as S
from app.services import audit
from app.services.storage import Storage, project_prefix

# Transições permitidas. Estados finais: completed, failed, canceled (failed pode ser reenfileirado).
TRANSITIONS: dict[S, set[S]] = {
    S.pending: {S.uploaded, S.canceled, S.failed},
    S.uploaded: {S.queued, S.canceled, S.failed},
    S.queued: {S.processing, S.canceled, S.failed},
    S.processing: {S.completed, S.failed, S.canceled},
    S.completed: set(),
    S.failed: {S.queued},
    S.canceled: set(),
}

CANCELABLE = {S.pending, S.uploaded, S.queued, S.processing}
# Excluir durante o processamento é bloqueado para não deixar o worker sem dados; cancele antes.
DELETABLE = {S.pending, S.uploaded, S.completed, S.failed, S.canceled}


class InvalidTransition(Exception):
    def __init__(self, current: S, target: S):
        super().__init__(f"Transição inválida: {current} -> {target}")
        self.current, self.target = current, target


def can_transition(current: S, target: S) -> bool:
    return target in TRANSITIONS[current]


def transition(project: Project, target: S, message: str | None = None) -> None:
    current = S(project.status)
    if not can_transition(current, target):
        raise InvalidTransition(current, target)
    project.status = target.value
    if message is not None:
        project.status_message = message


def create_project(db: Session, owner_id: uuid.UUID, title: str) -> Project:
    project = Project(owner_id=owner_id, title=title, status=S.pending.value, progress=0,
                      rights_confirmed_at=datetime.now(UTC))
    db.add(project)
    db.flush()
    audit.record(db, owner_id, "project.create", "project", project.id)
    db.commit()
    db.refresh(project)
    return project


def get_owned(db: Session, owner_id: uuid.UUID, project_id: uuid.UUID) -> Project | None:
    """Sempre filtra pelo dono: projetos de terceiros são indistinguíveis de inexistentes."""
    return db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id))


def list_owned(db: Session, owner_id: uuid.UUID, limit: int, offset: int,
               status: S | None = None) -> tuple[list[Project], int]:
    q = select(Project).where(Project.owner_id == owner_id)
    if status:
        q = q.where(Project.status == status.value)
    total = db.scalar(select(func.count()).select_from(q.subquery())) or 0
    items = db.scalars(q.order_by(Project.created_at.desc(), Project.id).limit(limit).offset(offset)).all()
    return list(items), total


def cancel_project(db: Session, project: Project) -> Project:
    db.refresh(project, with_for_update=True)  # serializa com o worker
    if S(project.status) == S.canceled:
        return project  # idempotente
    if S(project.status) not in CANCELABLE:
        raise InvalidTransition(S(project.status), S.canceled)
    transition(project, S.canceled, "Processamento cancelado pelo usuário")
    from app.services.jobs import cancel_active_job  # import local evita ciclo

    cancel_active_job(db, project.id)
    audit.record(db, project.owner_id, "project.cancel", "project", project.id)
    db.commit()
    db.refresh(project)
    return project


def delete_project(db: Session, storage: Storage, project: Project) -> None:
    """Remove primeiro os arquivos do storage; se falhar, nada é apagado do banco (StorageError)."""
    from app.services.jobs import active_job_exists

    if S(project.status) not in DELETABLE or active_job_exists(db, project.id):
        raise InvalidTransition(S(project.status), S.canceled)
    removed = storage.delete_prefix(project_prefix(project.owner_id, project.id))
    audit.record(db, project.owner_id, "project.delete", "project", project.id, {"files_removed": removed})
    db.delete(project)
    db.commit()
