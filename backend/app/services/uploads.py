"""Fluxo de upload direto ao storage: solicitar URL assinada -> enviar -> finalizar (validar)."""

import logging
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event
from app.models import MediaAsset, Project
from app.models.enums import AssetKind, AssetStatus
from app.models.enums import ProjectStatus as S
from app.services import audit, media_probe
from app.services.projects import transition
from app.services.storage import Storage, StorageError, source_video_key, validated_source_key

logger = logging.getLogger("autoclip.uploads")
ALLOWED_CONTENT_TYPES = {"video/mp4"}


class UploadRejected(Exception):
    def __init__(self, code: str, message: str, http_status: int = 422):
        super().__init__(message)
        self.code, self.message, self.http_status = code, message, http_status


class Busy(Exception):
    """Outra requisição está operando sobre o mesmo projeto."""


@dataclass
class UploadTicket:
    asset: MediaAsset
    url: str
    fields: dict
    expires_at: datetime
    max_bytes: int


@dataclass
class FinalizeResult:
    project: Project
    asset: MediaAsset
    already_finalized: bool


def _lock_project(db: Session, project_id: uuid.UUID, owner_id: uuid.UUID) -> Project | None:
    try:
        return db.scalar(select(Project).where(Project.id == project_id, Project.owner_id == owner_id)
                         .with_for_update(nowait=True))
    except OperationalError as exc:
        db.rollback()
        raise Busy() from exc


def _source_asset(db: Session, project_id: uuid.UUID, status: AssetStatus) -> MediaAsset | None:
    return db.scalar(select(MediaAsset).where(
        MediaAsset.project_id == project_id, MediaAsset.kind == AssetKind.source_video.value,
        MediaAsset.status == status.value).order_by(MediaAsset.created_at.desc()).limit(1))


def validate_declared(settings: Settings, filename: str, content_type: str, size_bytes: int) -> None:
    if not filename.lower().endswith(".mp4"):
        raise UploadRejected("invalid_extension", "Apenas arquivos .mp4 são aceitos.")
    if content_type.lower() not in ALLOWED_CONTENT_TYPES:
        raise UploadRejected("invalid_type", "Tipo de arquivo não suportado. Envie um vídeo MP4.")
    if size_bytes <= 0:
        raise UploadRejected("empty_file", "O arquivo está vazio.")
    if size_bytes > settings.max_upload_bytes:
        raise UploadRejected("file_too_large", f"O arquivo excede o limite de {settings.max_upload_mb} MB.")


def request_upload(db: Session, settings: Settings, storage: Storage, owner_id: uuid.UUID,
                   project_id: uuid.UUID, filename: str, content_type: str, size_bytes: int) -> UploadTicket | None:
    validate_declared(settings, filename, content_type, size_bytes)
    project = _lock_project(db, project_id, owner_id)
    if project is None:
        return None
    if S(project.status) != S.pending:
        db.rollback()
        raise UploadRejected("invalid_state", "O vídeo deste projeto já foi enviado ou o projeto não aceita mais "
                                              "uploads.", 409)

    # Reutiliza o asset pendente (pedir a URL de novo não cria registros duplicados).
    asset = _source_asset(db, project.id, AssetStatus.pending)
    if asset is None:
        asset_id = uuid.uuid4()
        asset = MediaAsset(id=asset_id, project_id=project.id, owner_id=owner_id, kind=AssetKind.source_video.value,
                           status=AssetStatus.pending.value,
                           storage_key=source_video_key(owner_id, project.id, asset_id))
        db.add(asset)
    asset.content_type = "video/mp4"
    asset.size_bytes = size_bytes  # declarado; substituído pelo tamanho real ao finalizar
    project.status_message = "Aguardando envio do vídeo"
    project.error_code = None

    expires = settings.upload_url_expires_seconds
    try:
        presigned = storage.presign_upload(asset.storage_key, "video/mp4", settings.max_upload_bytes, expires)
    except StorageError:
        db.rollback()
        raise
    audit.record(db, owner_id, "upload.request", "project", project.id, {"asset_id": str(asset.id)})
    db.commit()
    return UploadTicket(asset=asset, url=presigned["url"], fields=presigned["fields"],
                        expires_at=datetime.now(UTC) + timedelta(seconds=expires),
                        max_bytes=settings.max_upload_bytes)


def _reject(db: Session, storage: Storage, project: Project, asset: MediaAsset, code: str, message: str) -> None:
    """Marca o asset como inválido, remove o arquivo e mantém o projeto em `pending` para nova tentativa."""
    asset.status = AssetStatus.invalid.value
    project.status_message = message
    project.error_code = code
    try:
        storage.delete(asset.storage_key)
    except StorageError:
        log_event(logger, "Falha ao remover upload inválido", asset_id=str(asset.id))
    audit.record(db, project.owner_id, "upload.rejected", "project", project.id,
                 {"asset_id": str(asset.id), "code": code})
    db.commit()


def _discard(storage: Storage, key: str) -> None:
    try:
        storage.delete(key)
    except StorageError:
        log_event(logger, "Falha ao remover arquivo (órfão para a retenção)", key_suffix=key.rsplit("/", 1)[-1])


def finalize_upload(db: Session, settings: Settings, storage: Storage, owner_id: uuid.UUID,
                    project_id: uuid.UUID) -> FinalizeResult | None:
    project = _lock_project(db, project_id, owner_id)
    if project is None:
        return None

    # Idempotência: finalizar de novo um upload já validado devolve o mesmo resultado.
    ready = _source_asset(db, project.id, AssetStatus.ready)
    if ready is not None:
        db.rollback()
        return FinalizeResult(project=project, asset=ready, already_finalized=True)
    if S(project.status) != S.pending:
        db.rollback()
        raise UploadRejected("invalid_state", "Este projeto não aceita mais uploads.", 409)

    asset = _source_asset(db, project.id, AssetStatus.pending)
    if asset is None:
        db.rollback()
        raise UploadRejected("upload_not_requested", "Solicite uma URL de upload antes de finalizar.", 409)

    try:
        info = storage.head(asset.storage_key)
    except StorageError as exc:
        db.rollback()
        raise media_probe.ProbeUnavailable("storage indisponível") from exc
    if info is None:
        db.rollback()
        raise UploadRejected("upload_missing", "O arquivo ainda não foi recebido. Envie o vídeo e tente novamente.",
                             409)

    # F1: copia ANTES de validar e valida a cópia. A URL de upload (ainda válida por alguns minutos) só
    # alcança a chave original; o arquivo validado e processado é a cópia, que não pode ser trocada.
    upload_key = asset.storage_key
    validated_key = validated_source_key(upload_key)
    try:
        storage.copy(upload_key, validated_key)
        info = storage.head(validated_key)
    except StorageError as exc:
        _discard(storage, validated_key)
        db.rollback()
        raise media_probe.ProbeUnavailable("storage indisponível") from exc
    if info is None:  # cópia não encontrada logo após copiar: trata como indisponibilidade
        db.rollback()
        raise media_probe.ProbeUnavailable("cópia validada ausente")

    try:
        if info.size_bytes <= 0:
            raise media_probe.ValidationFailed("empty_file", "O arquivo está vazio.")
        if info.size_bytes > settings.max_upload_bytes:
            raise media_probe.ValidationFailed("file_too_large",
                                               f"O arquivo excede o limite de {settings.max_upload_mb} MB.")
        try:
            head = storage.read_range(validated_key, 0, 63)
        except StorageError as exc:
            raise media_probe.ProbeUnavailable("storage indisponível") from exc
        media_probe.check_mp4_signature(head)
        source = storage.presign_get(validated_key, settings.probe_timeout_seconds * 4, public=False)
        result = media_probe.analyze(
            source, ffprobe_path=settings.ffprobe_path, ffmpeg_path=settings.ffmpeg_path,
            timeout=settings.probe_timeout_seconds, min_seconds=settings.min_video_seconds,
            max_seconds=settings.max_video_minutes * 60,
        )
    except media_probe.ValidationFailed as exc:
        _discard(storage, validated_key)
        _reject(db, storage, project, asset, exc.code, exc.message)
        log_event(logger, "Upload rejeitado", project_id=str(project.id), code=exc.code)
        raise UploadRejected(exc.code, exc.message) from exc
    except media_probe.ProbeUnavailable:
        _discard(storage, validated_key)
        db.rollback()
        raise

    asset.storage_key = validated_key
    asset.status = AssetStatus.ready.value
    asset.size_bytes = info.size_bytes
    asset.content_type = "video/mp4"
    asset.duration_seconds = Decimal(f"{result.duration_seconds:.3f}")
    asset.width, asset.height = result.width, result.height
    asset.checksum_sha256 = None  # calculado pelo worker ao baixar o arquivo (Etapa 3)
    asset.probe = {**result.summary, "rotation": result.rotation, "etag": (info.etag or "").strip('"')}
    project.source_duration_seconds = asset.duration_seconds
    project.error_code = None
    transition(project, S.uploaded, "Vídeo recebido e validado")
    audit.record(db, owner_id, "upload.finalize", "project", project.id,
                 {"asset_id": str(asset.id), "duration_seconds": result.duration_seconds})
    db.commit()
    db.refresh(project)
    _discard(storage, upload_key)  # o original não é mais usado; se falhar, vira órfão (limpo pela retenção)
    log_event(logger, "Upload validado", project_id=str(project.id), duration=result.duration_seconds)
    return FinalizeResult(project=project, asset=asset, already_finalized=False)
