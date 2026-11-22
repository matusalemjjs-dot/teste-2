"""Etapas do pipeline. Cada etapa recebe o JobContext e só registra resultados reais.

Etapas ainda não implementadas falham explicitamente com `stage_unavailable`: o sistema nunca marca
um projeto como concluído sem ter executado o processamento de verdade.
"""

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass

from sqlalchemy import select

from app.db.session import get_sessionmaker
from app.models import MediaAsset
from app.models.enums import AssetKind, AssetStatus
from app.pipeline.context import JobContext, PipelineError
from app.services.storage import StorageError, get_storage, iter_object


@dataclass(frozen=True)
class Stage:
    name: str
    label: str  # exibido ao usuário
    weight: int  # peso relativo no progresso
    run: Callable[[JobContext], None]


def prepare_source(ctx: JobContext) -> None:
    """Baixa o vídeo validado para o diretório temporário, conferindo tamanho e checksum."""
    with get_sessionmaker()() as db:
        asset = db.scalar(select(MediaAsset).where(
            MediaAsset.project_id == ctx.project_id, MediaAsset.kind == AssetKind.source_video.value,
            MediaAsset.status == AssetStatus.ready.value).order_by(MediaAsset.created_at.desc()).limit(1))
        if asset is None:
            raise PipelineError("source_missing", "O vídeo do projeto não foi encontrado. Envie-o novamente.")
        key, expected_size, expected_sum, asset_id = (asset.storage_key, asset.size_bytes, asset.checksum_sha256,
                                                      asset.id)

    free = shutil.disk_usage(ctx.workdir).free
    if free - (expected_size or 0) < ctx.settings.worker_min_free_disk_mb * 1024 * 1024:
        raise PipelineError("insufficient_disk",
                            "Servidor temporariamente sem espaço para processar. Tente novamente mais tarde.",
                            detail=f"free={free} needed={expected_size}")

    path = ctx.workdir / "source.mp4"
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("wb") as fh:
            for chunk in iter_object(get_storage(), key):
                fh.write(chunk)
                digest.update(chunk)
                size += len(chunk)
                ctx.checkpoint(min_interval=2.0)
    except StorageError as exc:
        raise PipelineError("storage_error", "Não foi possível ler o vídeo no armazenamento. Tente novamente.",
                            detail=str(exc)) from exc

    checksum = digest.hexdigest()
    if expected_size is not None and size != expected_size:
        raise PipelineError("source_changed", "O arquivo do vídeo mudou após a validação. Envie-o novamente.",
                            detail=f"size {size} != {expected_size}")
    if expected_sum and checksum != expected_sum:
        raise PipelineError("source_changed", "O arquivo do vídeo mudou após a validação. Envie-o novamente.",
                            detail="checksum mismatch")
    if not expected_sum:
        with get_sessionmaker()() as db:
            db.get(MediaAsset, asset_id).checksum_sha256 = checksum
            db.commit()
    ctx.data["source_path"] = path
    ctx.data["source_sha256"] = checksum


def unavailable(label: str) -> Callable[[JobContext], None]:
    def run(ctx: JobContext) -> None:
        raise PipelineError("stage_unavailable",
                            f"A etapa de {label} ainda não está disponível nesta versão do AutoClip IA. "
                            "Nenhum corte foi gerado.")
    return run


def default_pipeline() -> list[Stage]:
    return [
        Stage("prepare", "preparação do vídeo", 5, prepare_source),
        Stage("transcribe", "transcrição", 35, unavailable("transcrição")),
        Stage("analyze", "análise inteligente", 20, unavailable("análise inteligente")),
        Stage("render", "renderização dos cortes", 40, unavailable("renderização")),
    ]


def get_pipeline() -> list[Stage]:
    return default_pipeline()
