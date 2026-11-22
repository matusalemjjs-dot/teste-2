import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ProjectStatus


class ErrorResponse(BaseModel):
    detail: str


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str | None
    display_name: str | None
    is_admin: bool
    created_at: datetime


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    # O usuário precisa declarar que tem autorização para processar o conteúdo (LGPD/direitos autorais).
    rights_confirmed: bool = Field(description="Confirma que o usuário tem direito de processar o conteúdo")

    @field_validator("title")
    @classmethod
    def strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("O título não pode ficar vazio")
        return v

    @field_validator("rights_confirmed")
    @classmethod
    def must_confirm(cls, v: bool) -> bool:
        if not v:
            raise ValueError("É necessário confirmar que você tem autorização para processar este conteúdo")
        return v


class ProjectOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    title: str
    status: ProjectStatus
    progress: int
    status_message: str | None
    error_code: str | None
    source_duration_seconds: float | None
    created_at: datetime
    updated_at: datetime


class ProjectList(BaseModel):
    items: list[ProjectOut]
    total: int
    limit: int
    offset: int


class ProjectStatusOut(BaseModel):
    id: uuid.UUID
    status: ProjectStatus
    progress: int
    status_message: str | None
    error_code: str | None
    updated_at: datetime
    job: "JobOut | None" = None


class UploadUrlRequest(BaseModel):
    filename: str = Field(min_length=1, max_length=255, description="Nome original (apenas para validação)")
    content_type: str = Field(max_length=100, examples=["video/mp4"])
    size_bytes: int = Field(description="Tamanho do arquivo em bytes")


class UploadUrlOut(BaseModel):
    asset_id: uuid.UUID
    method: str = "POST"
    url: str
    fields: dict[str, str] = Field(description="Campos a enviar no formulário multipart, antes do arquivo ('file')")
    expires_at: datetime
    max_bytes: int


class MediaAssetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    kind: str
    status: str
    content_type: str | None
    size_bytes: int | None
    duration_seconds: float | None
    width: int | None
    height: int | None
    created_at: datetime


class FinalizeUploadOut(BaseModel):
    project: ProjectOut
    asset: MediaAssetOut
    already_finalized: bool


class UploadErrorResponse(BaseModel):
    detail: str
    code: str


class JobOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    status: str
    stage: str | None
    attempts: int
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ProcessOut(BaseModel):
    project: ProjectOut
    job: JobOut
    created: bool = Field(description="false quando a requisição repetida devolveu o processamento existente")


class TranscriptSegmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    seq: int
    start_seconds: float
    end_seconds: float
    text: str
    confidence: float | None


class TranscriptOut(BaseModel):
    project_id: uuid.UUID
    segments: list[TranscriptSegmentOut]


ProjectStatusOut.model_rebuild()
