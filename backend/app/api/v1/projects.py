import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import JSONResponse

from app.api.deps import DB, CurrentProfile
from app.core.config import Settings, get_settings
from app.models.enums import ProjectStatus
from app.schemas import (
    CandidateList,
    CandidateOut,
    ErrorResponse,
    FinalizeUploadOut,
    JobOut,
    MediaAssetOut,
    ProcessOut,
    ProjectCreate,
    ProjectList,
    ProjectOut,
    ProjectStatusOut,
    TranscriptOut,
    TranscriptSegmentOut,
    UploadErrorResponse,
    UploadUrlOut,
    UploadUrlRequest,
)
from app.services import jobs as jobs_svc
from app.services import projects as svc
from app.services import uploads
from app.services.media_probe import ProbeUnavailable
from app.services.queue import QueueUnavailable
from app.services.storage import Storage, StorageError, get_storage

router = APIRouter(prefix="/projects", tags=["projetos"])

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Projeto não encontrado"}}
UPLOAD_ERRORS = {**NOT_FOUND, 409: {"model": UploadErrorResponse}, 422: {"model": UploadErrorResponse},
                 503: {"model": ErrorResponse}}
StorageDep = Annotated[Storage, Depends(get_storage)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def _not_found() -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, "Projeto não encontrado")


def _load(db, profile, project_id: uuid.UUID):
    project = svc.get_owned(db, profile.id, project_id)
    if project is None:
        raise _not_found()
    return project


def _upload_error(exc: uploads.UploadRejected) -> JSONResponse:
    return JSONResponse(status_code=exc.http_status, content={"detail": exc.message, "code": exc.code})


def _unavailable() -> HTTPException:
    return HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                         "Serviço de arquivos indisponível no momento. Tente novamente em instantes.")


def _busy() -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, "Outra operação está em andamento neste projeto. Aguarde.")


@router.post("", response_model=ProjectOut, status_code=status.HTTP_201_CREATED, summary="Criar projeto")
def create_project(body: ProjectCreate, db: DB, profile: CurrentProfile) -> ProjectOut:
    return ProjectOut.model_validate(svc.create_project(db, profile.id, body.title))


@router.get("", response_model=ProjectList, summary="Listar meus projetos")
def list_projects(
    db: DB,
    profile: CurrentProfile,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
    status_filter: Annotated[ProjectStatus | None, Query(alias="status")] = None,
) -> ProjectList:
    items, total = svc.list_owned(db, profile.id, limit, offset, status_filter)
    return ProjectList(items=[ProjectOut.model_validate(p) for p in items], total=total, limit=limit, offset=offset)


@router.get("/{project_id}", response_model=ProjectOut, responses=NOT_FOUND, summary="Consultar projeto")
def get_project(project_id: uuid.UUID, db: DB, profile: CurrentProfile) -> ProjectOut:
    return ProjectOut.model_validate(_load(db, profile, project_id))


@router.get("/{project_id}/status", response_model=ProjectStatusOut, responses=NOT_FOUND,
            summary="Status e progresso do processamento")
def get_status(project_id: uuid.UUID, db: DB, profile: CurrentProfile) -> ProjectStatusOut:
    p = _load(db, profile, project_id)
    job = jobs_svc.latest_job(db, p.id)
    return ProjectStatusOut(id=p.id, status=p.status, progress=p.progress, status_message=p.status_message,
                            error_code=p.error_code, updated_at=p.updated_at,
                            job=JobOut.model_validate(job) if job else None)


@router.get("/{project_id}/transcript", response_model=TranscriptOut, responses=NOT_FOUND,
            summary="Transcrição do vídeo (segmentos com timestamps)")
def get_transcript(project_id: uuid.UUID, db: DB, profile: CurrentProfile) -> TranscriptOut:
    from sqlalchemy import select

    from app.models import TranscriptSegment

    p = _load(db, profile, project_id)
    rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.project_id == p.id)
                      .order_by(TranscriptSegment.seq)).all()
    return TranscriptOut(project_id=p.id, segments=[TranscriptSegmentOut.model_validate(r) for r in rows])


@router.get("/{project_id}/candidates", response_model=CandidateList, responses=NOT_FOUND,
            summary="Trechos candidatos avaliados pela IA",
            description="Todos os trechos avaliados, do maior para o menor score. `selected=true` indica os "
                        "escolhidos (até 10, sem sobreposição) para virar cortes.")
def get_candidates(project_id: uuid.UUID, db: DB, profile: CurrentProfile,
                   only_selected: Annotated[bool, Query()] = False) -> CandidateList:
    from sqlalchemy import select

    from app.models import ClipCandidate

    p = _load(db, profile, project_id)
    q = select(ClipCandidate).where(ClipCandidate.project_id == p.id)
    if only_selected:
        q = q.where(ClipCandidate.selected.is_(True))
    rows = db.scalars(q.order_by(ClipCandidate.score.desc(), ClipCandidate.start_seconds)).all()
    return CandidateList(project_id=p.id, items=[CandidateOut.model_validate(r) for r in rows])


@router.post("/{project_id}/upload-url", response_model=UploadUrlOut, responses=UPLOAD_ERRORS,
             summary="Obter URL assinada para enviar o vídeo",
             description="Retorna um POST pré-assinado (multipart/form-data). Envie todos os `fields` e, por "
                         "último, o arquivo no campo `file`. Depois chame `finalize-upload`.")
def upload_url(project_id: uuid.UUID, body: UploadUrlRequest, db: DB, profile: CurrentProfile,
               settings: SettingsDep, storage: StorageDep):
    try:
        ticket = uploads.request_upload(db, settings, storage, profile.id, project_id, body.filename,
                                        body.content_type, body.size_bytes)
    except uploads.UploadRejected as exc:
        return _upload_error(exc)
    except uploads.Busy as exc:
        raise _busy() from exc
    except StorageError as exc:
        raise _unavailable() from exc
    if ticket is None:
        raise _not_found()
    return UploadUrlOut(asset_id=ticket.asset.id, url=ticket.url, fields=ticket.fields,
                        expires_at=ticket.expires_at, max_bytes=ticket.max_bytes)


@router.post("/{project_id}/finalize-upload", response_model=FinalizeUploadOut, responses=UPLOAD_ERRORS,
             summary="Confirmar o envio e validar o vídeo",
             description="Valida tipo real, integridade e duração com FFprobe. Idempotente: repetir a chamada "
                         "após o sucesso devolve o mesmo resultado (`already_finalized=true`).")
def finalize_upload(project_id: uuid.UUID, db: DB, profile: CurrentProfile, settings: SettingsDep,
                    storage: StorageDep):
    try:
        result = uploads.finalize_upload(db, settings, storage, profile.id, project_id)
    except uploads.UploadRejected as exc:
        return _upload_error(exc)
    except uploads.Busy as exc:
        raise _busy() from exc
    except ProbeUnavailable as exc:
        raise _unavailable() from exc
    if result is None:
        raise _not_found()
    return FinalizeUploadOut(project=ProjectOut.model_validate(result.project),
                             asset=MediaAssetOut.model_validate(result.asset),
                             already_finalized=result.already_finalized)


@router.post("/{project_id}/process", response_model=ProcessOut, status_code=status.HTTP_202_ACCEPTED,
             responses={**UPLOAD_ERRORS, 200: {"model": ProcessOut, "description": "Processamento já existente"}},
             summary="Iniciar o processamento",
             description="Idempotente: repetir a chamada com processamento em fila, em andamento ou concluído "
                         "devolve o mesmo job (HTTP 200, `created=false`). Após uma falha, cria nova tentativa.")
def process_project(project_id: uuid.UUID, db: DB, profile: CurrentProfile, settings: SettingsDep):
    try:
        result = jobs_svc.request_processing(db, settings, profile.id, project_id)
    except jobs_svc.ProcessRejected as exc:
        return JSONResponse(status_code=exc.http_status, content={"detail": exc.message, "code": exc.code})
    except jobs_svc.Busy as exc:
        raise _busy() from exc
    except QueueUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            "Fila de processamento indisponível. Tente novamente em instantes.") from exc
    if result is None:
        raise _not_found()
    job, created = result
    project = svc.get_owned(db, profile.id, project_id)
    body = ProcessOut(project=ProjectOut.model_validate(project), job=JobOut.model_validate(job), created=created)
    return JSONResponse(status_code=202 if created else 200, content=body.model_dump(mode="json"))


@router.post("/{project_id}/cancel", response_model=ProjectOut,
             responses={**NOT_FOUND, 409: {"model": ErrorResponse}}, summary="Cancelar processamento")
def cancel_project(project_id: uuid.UUID, db: DB, profile: CurrentProfile) -> ProjectOut:
    p = _load(db, profile, project_id)
    try:
        return ProjectOut.model_validate(svc.cancel_project(db, p))
    except svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            f"Não é possível cancelar um projeto com status '{exc.current}'") from exc


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT,
               responses={**NOT_FOUND, 409: {"model": ErrorResponse}, 503: {"model": ErrorResponse}},
               summary="Excluir projeto e seus arquivos")
def delete_project(project_id: uuid.UUID, db: DB, profile: CurrentProfile, storage: StorageDep) -> Response:
    p = _load(db, profile, project_id)
    try:
        svc.delete_project(db, storage, p)
    except svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Projeto em processamento. Cancele antes de excluir.") from exc
    except StorageError as exc:
        db.rollback()
        raise _unavailable() from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
