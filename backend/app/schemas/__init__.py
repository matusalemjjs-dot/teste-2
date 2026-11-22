import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.models.enums import ProjectStatus


class ErrorResponse(BaseModel):
    detail: str


class UsageOut(BaseModel):
    plan: str
    status: str
    period_start: datetime
    period_end: datetime
    quota_minutes: int
    used_minutes: float = Field(description="Minutos de vídeo processados com sucesso no mês")
    reserved_minutes: float = Field(description="Minutos de projetos em fila ou em processamento")
    remaining_minutes: float
    projects_last_24h: int
    projects_per_day: int
    quotas_enabled: bool
    llm_cost_usd: float = Field(0.0, description="Custo de IA medido no período (estimativa interna, US$)")
    llm_reserved_usd: float = Field(0.0, description="Custo de IA reservado para vídeos em processamento")
    llm_budget_usd: float | None = Field(None, description="Teto de custo de IA por usuário no período")


class MeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    email: str | None
    display_name: str | None
    is_admin: bool
    created_at: datetime
    usage: UsageOut | None = None


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


class RetentionOut(BaseModel):
    source_available: bool = Field(description="O vídeo original ainda existe (permite regenerar cortes)")
    source_expires_at: datetime | None = Field(description="Quando o vídeo original será apagado")
    project_expires_at: datetime | None = Field(description="Quando o projeto inteiro será apagado")


class AccountDeletionOut(BaseModel):
    projects_deleted: int
    files_deleted: int
    auth_account_deleted: bool
    message: str


class ProjectStatusOut(BaseModel):
    id: uuid.UUID
    status: ProjectStatus
    progress: int
    status_message: str | None
    error_code: str | None
    updated_at: datetime
    job: "JobOut | None" = None
    retention: RetentionOut | None = None


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


class CandidateOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: uuid.UUID
    start_seconds: float
    end_seconds: float
    score: int
    title: str
    justification: str
    criteria: dict | None
    selected: bool
    model_name: str | None


class CandidateList(BaseModel):
    project_id: uuid.UUID
    items: list[CandidateOut]


class ClipOut(BaseModel):
    id: uuid.UUID
    project_id: uuid.UUID
    title: str
    justification: str | None
    score: int
    start_seconds: float
    end_seconds: float
    duration_seconds: float
    status: str
    layout: str
    render_version: int
    error_code: str | None
    width: int | None = None
    height: int | None = None
    size_bytes: int | None = None
    thumbnail_url: str | None = Field(None, description="URL temporária da miniatura")
    has_subtitles: bool
    created_at: datetime
    updated_at: datetime


class ClipList(BaseModel):
    project_id: uuid.UUID
    items: list[ClipOut]


class DownloadUrlRequest(BaseModel):
    disposition: Literal["attachment", "inline"] = Field(
        "attachment", description="attachment = baixar; inline = reproduzir no navegador")
    file: Literal["video", "subtitle"] = "video"


class DownloadUrlOut(BaseModel):
    url: str
    expires_at: datetime
    filename: str


class RegenerateRequest(BaseModel):
    layout: Literal["center_crop", "blur_background"] = "center_crop"
    subtitles: bool = True


class RegenerateOut(BaseModel):
    clip: ClipOut
    job: JobOut
    created: bool


ProjectStatusOut.model_rebuild()
