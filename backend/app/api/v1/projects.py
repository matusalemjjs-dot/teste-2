import uuid
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query, Response, status

from app.api.deps import DB, CurrentProfile
from app.models.enums import ProjectStatus
from app.schemas import ErrorResponse, ProjectCreate, ProjectList, ProjectOut, ProjectStatusOut
from app.services import projects as svc

router = APIRouter(prefix="/projects", tags=["projetos"])

NOT_FOUND = {404: {"model": ErrorResponse, "description": "Projeto não encontrado"}}


def _load(db, profile, project_id: uuid.UUID):
    project = svc.get_owned(db, profile.id, project_id)
    if project is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Projeto não encontrado")
    return project


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
    return ProjectStatusOut(id=p.id, status=p.status, progress=p.progress, status_message=p.status_message,
                            error_code=p.error_code, updated_at=p.updated_at)


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
               responses={**NOT_FOUND, 409: {"model": ErrorResponse}}, summary="Excluir projeto")
def delete_project(project_id: uuid.UUID, db: DB, profile: CurrentProfile) -> Response:
    p = _load(db, profile, project_id)
    try:
        svc.delete_project(db, p)
    except svc.InvalidTransition as exc:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "Projeto em processamento. Cancele antes de excluir.") from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
