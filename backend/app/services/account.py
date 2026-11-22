"""Exclusão da conta e de todos os dados do usuário (LGPD, art. 18)."""

import logging
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event
from app.models import AuditEvent, ProcessingJob, Profile, Project
from app.models.enums import JobStatus
from app.services import audit, queue
from app.services.storage import Storage

logger = logging.getLogger("autoclip.account")


class AccountBusy(Exception):
    pass


@dataclass
class DeletionResult:
    projects_deleted: int
    files_deleted: int
    auth_account_deleted: bool
    auth_message: str


def was_deleted(db: Session, user_id: uuid.UUID) -> bool:
    """Impede que um token antigo (ainda não expirado) recrie o perfil de uma conta excluída."""
    return db.scalar(select(func.count()).select_from(AuditEvent).where(
        AuditEvent.action == "account.deleted", AuditEvent.resource_id == user_id)) > 0


def _delete_auth_user(settings: Settings, user_id: uuid.UUID) -> tuple[bool, str]:
    base = settings.supabase_admin_url or settings.supabase_url
    if not base or not settings.supabase_service_role_key:
        return False, ("Seus dados foram apagados. O login ainda precisa ser removido pelo administrador "
                       "(chave de serviço do Supabase não configurada).")
    key = settings.supabase_service_role_key.get_secret_value()
    req = urllib.request.Request(f"{base.rstrip('/')}/auth/v1/admin/users/{user_id}",
                                 method="DELETE", headers={"apikey": key, "Authorization": f"Bearer {key}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:  # noqa: S310 (URL da configuração)
            ok = 200 <= r.status < 300
    except urllib.error.HTTPError as exc:
        ok = exc.code == 404  # já não existe
    except (urllib.error.URLError, TimeoutError):
        ok = False
    if ok:
        return True, "Sua conta e todos os seus dados foram excluídos."
    return False, ("Seus dados foram apagados, mas não conseguimos remover o login agora. "
                   "O administrador concluirá a remoção.")


def delete_account(db: Session, storage: Storage, settings: Settings, user_id: uuid.UUID) -> DeletionResult:
    """Ordem segura: trava os projetos sem esperar; recusa se houver job rodando; apaga os arquivos do
    storage (se falhar, nada muda no banco e o usuário pode tentar de novo); apaga perfil e dados ligados
    em uma transação; cancela jobs na fila; por fim remove o login no Supabase."""
    try:
        projects = db.scalars(select(Project).where(Project.owner_id == user_id)
                              .with_for_update(nowait=True)).all()
    except OperationalError as exc:
        db.rollback()
        raise AccountBusy() from exc
    jobs = db.scalars(select(ProcessingJob).where(ProcessingJob.owner_id == user_id,
                                                  ProcessingJob.status.in_((JobStatus.queued.value,
                                                                            JobStatus.running.value)))
                      .with_for_update()).all()
    if any(j.status == JobStatus.running.value for j in jobs):
        db.rollback()
        raise AccountBusy()
    queued = [j.id for j in jobs]
    try:
        files = storage.delete_prefix(f"users/{user_id}/")
    except Exception:
        db.rollback()
        raise
    profile = db.get(Profile, user_id)
    if profile is not None:
        db.delete(profile)  # cascata: projetos, arquivos, transcrições, candidatos, cortes, jobs, assinatura
    # registro sem dados pessoais; também bloqueia a recriação do perfil por token antigo
    audit.record(db, None, "account.deleted", "account", user_id, {"projects": len(projects), "files": files})
    db.commit()
    for job_id in queued:
        queue.cancel_queued(str(job_id))
    auth_ok, message = _delete_auth_user(settings, user_id)
    if not auth_ok:
        audit.record(db, None, "account.auth_delete_pending", "account", user_id)
        db.commit()
    log_event(logger, "Conta excluída", projects=len(projects), files=files, auth_deleted=auth_ok)
    return DeletionResult(len(projects), files, auth_ok, message)


def policy(settings: Settings) -> dict:
    """Informações públicas de privacidade, sempre coerentes com a configuração em uso."""
    sub = [
        {"nome": "Supabase", "finalidade": "Autenticação (e-mail e senha)"},
        {"nome": "Anthropic (Claude)", "finalidade": "Análise da transcrição para escolher os melhores momentos"},
        {"nome": settings.storage_provider_name, "finalidade": "Armazenamento privado de vídeos e cortes"},
        {"nome": settings.hosting_provider_name, "finalidade": "Servidores da aplicação e do processamento"},
    ]
    if settings.transcription_provider == "openai":
        sub.append({"nome": "OpenAI", "finalidade": "Transcrição do áudio"})
    return {
        "retencao": {
            "video_original_dias": settings.retention_source_days,
            "projeto_dias": settings.retention_project_days,
            "projeto_sem_video_horas": settings.retention_abandoned_hours,
            "auditoria_dias": settings.retention_audit_days,
        },
        "limites": {"video_max_minutos": settings.max_video_minutes, "upload_max_mb": settings.max_upload_mb},
        "suboperadores": sub,
        "contato": settings.support_email,
        "transcricao_local": settings.transcription_provider == "faster_whisper",
        "texto_juridico_revisado": False,
    }
