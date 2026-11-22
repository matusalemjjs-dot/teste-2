"""Renderiza um corte, publica no storage e atualiza o banco."""

import uuid
from decimal import Decimal

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from app.db.session import get_sessionmaker
from app.models import Clip, MediaAsset, TranscriptSegment, UsageLedger
from app.models.enums import AssetKind, AssetStatus, ClipStatus
from app.pipeline.context import JobContext
from app.rendering.render import RenderError, render_command, thumbnail_command, verify_output
from app.rendering.subtitles import build_cues, to_ass, to_srt
from app.services.storage import StorageError, clip_key, get_storage, project_prefix


def load_segments(project_id: uuid.UUID, start: float, end: float) -> list[dict]:
    with get_sessionmaker()() as db:
        rows = db.scalars(select(TranscriptSegment).where(
            TranscriptSegment.project_id == project_id, TranscriptSegment.end_seconds > start,
            TranscriptSegment.start_seconds < end).order_by(TranscriptSegment.seq)).all()
        return [{"start": float(r.start_seconds), "end": float(r.end_seconds), "text": r.text, "words": r.words}
                for r in rows]


def render_one(ctx: JobContext, clip_id: uuid.UUID, *, layout: str, subtitles: bool, version: int) -> None:
    """Gera vídeo, miniatura e legenda (SRT) de um corte e publica como nova versão.
    A versão anterior só é removida depois que a nova estiver publicada."""
    s = ctx.settings
    with get_sessionmaker()() as db:
        clip = db.get(Clip, clip_id)
        start, end = float(clip.start_seconds), float(clip.end_seconds)
    duration = round(end - start, 3)
    src = ctx.data["source_path"]
    tag = f"{clip_id}-v{version}"
    out, thumb = ctx.workdir / f"{tag}.mp4", ctx.workdir / f"{tag}.jpg"
    srt = ass = None

    cues = build_cues(load_segments(ctx.project_id, start, end), start, end)
    if cues:
        srt = ctx.workdir / f"{tag}.srt"
        srt.write_text(to_srt(cues), encoding="utf-8")
        if subtitles:
            ass = ctx.workdir / f"{tag}.ass"
            ass.write_text(to_ass(cues, width=s.render_width, height=s.render_height, font=s.subtitle_font,
                                  size=s.subtitle_font_size), encoding="utf-8")

    r = ctx.run(render_command(s, src, out, start, duration, layout, ass), timeout=s.render_timeout_seconds)
    if r.returncode != 0 or not out.exists():
        raise RenderError("render_failed", "Falha ao gerar o vídeo do corte.",
                          detail=r.stderr.decode("utf-8", "replace")[-2000:])
    real_dur, w, h = verify_output(s, out, duration)
    r = ctx.run(thumbnail_command(s, out, thumb, min(real_dur / 2, 3.0)), timeout=120)
    if r.returncode != 0 or not thumb.exists():
        raise RenderError("render_failed", "Falha ao gerar a miniatura do corte.",
                          detail=r.stderr.decode("utf-8", "replace")[-1000:])
    ctx.checkpoint()

    storage = get_storage()
    files = [(AssetKind.clip_video, out, "mp4", "video/mp4"), (AssetKind.thumbnail, thumb, "jpg", "image/jpeg")]
    if srt:
        files.append((AssetKind.subtitle, srt, "srt", "application/x-subrip"))
    uploaded: list[tuple[AssetKind, str, int, str]] = []
    try:
        for kind, path, ext, ctype in files:
            key = clip_key(ctx.owner_id, ctx.project_id, clip_id, version, ext)
            storage.upload_file(path, key, ctype)
            uploaded.append((kind, key, path.stat().st_size, ctype))
    except StorageError as exc:
        for _, key, _, _ in uploaded:
            try:
                storage.delete(key)
            except StorageError:
                pass
        raise RenderError("storage_error", "Não foi possível salvar o corte. Tente novamente.", str(exc)) from exc

    old_keys: list[str] = []
    with get_sessionmaker()() as db:
        clip = db.scalar(select(Clip).where(Clip.id == clip_id).with_for_update())
        if clip is None:  # projeto excluído durante a renderização
            for _, key, _, _ in uploaded:
                storage.delete(key)
            from app.pipeline.context import Canceled
            raise Canceled()
        old_ids = [i for i in (clip.video_asset_id, clip.thumbnail_asset_id, clip.subtitle_asset_id) if i]
        new_ids: dict[AssetKind, uuid.UUID] = {}
        for kind, key, size, ctype in uploaded:
            asset = MediaAsset(project_id=ctx.project_id, owner_id=ctx.owner_id, kind=kind.value,
                               status=AssetStatus.ready.value, storage_key=key, content_type=ctype, size_bytes=size,
                               duration_seconds=Decimal(f"{real_dur:.3f}") if kind == AssetKind.clip_video else None,
                               width=w if kind == AssetKind.clip_video else None,
                               height=h if kind == AssetKind.clip_video else None)
            db.add(asset)
            db.flush()
            new_ids[kind] = asset.id
        clip.video_asset_id = new_ids[AssetKind.clip_video]
        clip.thumbnail_asset_id = new_ids[AssetKind.thumbnail]
        clip.subtitle_asset_id = new_ids.get(AssetKind.subtitle)
        clip.layout = layout
        clip.render_version = version
        clip.status = ClipStatus.ready.value
        clip.error_code = None
        if old_ids:
            old_keys = list(db.scalars(select(MediaAsset.storage_key).where(MediaAsset.id.in_(old_ids))))
            db.execute(delete(MediaAsset).where(MediaAsset.id.in_(old_ids)))
        db.execute(insert(UsageLedger).values(
            owner_id=ctx.owner_id, project_id=ctx.project_id, job_id=ctx.job_id, kind="render_seconds",
            quantity=Decimal(f"{real_dur:.3f}"),
            estimated_cost_usd=Decimal(f"{real_dur / 60 * s.cost_per_render_minute_usd:.6f}"),
            idempotency_key=f"render:{clip_id}:v{version}").on_conflict_do_nothing(index_elements=["idempotency_key"]))
        db.commit()
    for key in old_keys:
        try:
            storage.delete(key)
        except StorageError:
            pass  # órfão: removido junto com o projeto ou pela política de retenção
    for p in (out, thumb, srt, ass):
        if p:
            p.unlink(missing_ok=True)


def reset_project_clips(ctx: JobContext) -> None:
    """Idempotência do processamento completo: remove cortes anteriores do projeto (banco e storage)."""
    with get_sessionmaker()() as db:
        ids = [a for row in db.execute(select(Clip.video_asset_id, Clip.thumbnail_asset_id, Clip.subtitle_asset_id)
                                       .where(Clip.project_id == ctx.project_id)) for a in row if a]
        db.execute(delete(Clip).where(Clip.project_id == ctx.project_id))
        if ids:
            db.execute(delete(MediaAsset).where(MediaAsset.id.in_(ids)))
        db.commit()
    try:
        get_storage().delete_prefix(project_prefix(ctx.owner_id, ctx.project_id) + "clips/")
    except StorageError:
        pass
