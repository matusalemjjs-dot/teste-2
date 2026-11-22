import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import JSONResponse

from app.api.deps import DB, CurrentProfile
from app.core.config import Settings, get_settings
from app.models.enums import ClipStatus
from app.schemas import DownloadUrlOut, DownloadUrlRequest, ErrorResponse, JobOut, RegenerateOut, RegenerateRequest
from app.services import audit
from app.services import clips as svc
from app.services import jobs as jobs_svc
from app.services.queue import QueueUnavailable
from app.services.storage import Storage, get_storage

router = APIRouter(prefix="/clips", tags=["cortes"])
StorageDep = Annotated[Storage, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(get_settings)]
NOT_FOUND = {404: {"model": ErrorResponse, "description": "Corte não encontrado"}}


def _load(db, profile, clip_id):
    clip = svc.get_owned_clip(db, profile.id, clip_id)
    if clip is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Corte não encontrado")
    return clip


@router.get("/{clip_id}", responses=NOT_FOUND, summary="Consultar corte")
def get_clip(clip_id: uuid.UUID, db: DB, profile: CurrentProfile, storage: StorageDep, settings: SettingsDep):
    return svc.to_out(db, _load(db, profile, clip_id), storage, settings)


@router.post("/{clip_id}/download-url", response_model=DownloadUrlOut,
             responses={**NOT_FOUND, 409: {"model": ErrorResponse}},
             summary="URL temporária para baixar ou reproduzir o corte",
             description="Emitida somente para o dono do corte e somente com o corte pronto. Expira em "
                         "DOWNLOAD_URL_EXPIRES_SECONDS.")
def download_url(clip_id: uuid.UUID, db: DB, profile: CurrentProfile, storage: StorageDep, settings: SettingsDep,
                 body: DownloadUrlRequest | None = None) -> DownloadUrlOut:
    body = body or DownloadUrlRequest()
    clip = _load(db, profile, clip_id)
    if clip.video_asset_id is None or clip.status not in (ClipStatus.ready.value, ClipStatus.pending.value,
                                                          ClipStatus.rendering.value):
        raise HTTPException(status.HTTP_409_CONFLICT, "O corte ainda não está disponível para download.")
    result = svc.download_url(db, clip, storage, settings, file=body.file, disposition=body.disposition)
    if result is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Este corte não possui legenda.")
    url, expires_at, name = result
    audit.record(db, profile.id, "clip.download_url", "clip", clip.id, {"file": body.file})
    db.commit()
    return DownloadUrlOut(url=url, expires_at=expires_at, filename=name)


@router.post("/{clip_id}/regenerate", response_model=RegenerateOut, status_code=status.HTTP_202_ACCEPTED,
             responses={**NOT_FOUND, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
             summary="Regenerar o corte (layout e legendas)",
             description="Idempotente: com uma regeneração do mesmo corte em andamento, devolve o job existente "
                         "(HTTP 200, created=false). A versão anterior continua disponível até a nova ficar pronta.")
def regenerate(clip_id: uuid.UUID, body: RegenerateRequest, db: DB, profile: CurrentProfile, storage: StorageDep,
               settings: SettingsDep):
    try:
        result = jobs_svc.request_regenerate(db, profile.id, clip_id, body.layout, body.subtitles)
    except jobs_svc.ProcessRejected as exc:
        return JSONResponse(status_code=exc.http_status, content={"detail": exc.message, "code": exc.code})
    except jobs_svc.Busy as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "Outra operação está em andamento neste corte.") from exc
    except QueueUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "Fila indisponível. Tente novamente.") from exc
    if result is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Corte não encontrado")
    job, created = result
    clip = svc.get_owned_clip(db, profile.id, clip_id)
    body_out = RegenerateOut(clip=svc.to_out(db, clip, storage, settings), job=JobOut.model_validate(job),
                             created=created)
    return JSONResponse(status_code=202 if created else 200, content=body_out.model_dump(mode="json"))
