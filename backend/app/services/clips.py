"""Consulta de cortes e emissão de URLs temporárias (sempre após checar o dono)."""

import re
import unicodedata
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.models import Clip, MediaAsset
from app.schemas import ClipOut
from app.services.storage import Storage


def get_owned_clip(db: Session, owner_id: uuid.UUID, clip_id: uuid.UUID) -> Clip | None:
    return db.scalar(select(Clip).where(Clip.id == clip_id, Clip.owner_id == owner_id))


def list_project_clips(db: Session, project_id: uuid.UUID) -> list[Clip]:
    return list(db.scalars(select(Clip).where(Clip.project_id == project_id)
                           .order_by(Clip.score.desc(), Clip.start_seconds)))


def safe_filename(title: str, ext: str) -> str:
    ascii_title = unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^A-Za-z0-9]+", "-", ascii_title).strip("-").lower()[:60] or "corte"
    return f"{slug}.{ext}"


def to_out(db: Session, clip: Clip, storage: Storage, settings: Settings) -> ClipOut:
    assets = {a.id: a for a in db.scalars(select(MediaAsset).where(MediaAsset.id.in_(
        [i for i in (clip.video_asset_id, clip.thumbnail_asset_id) if i])))}
    video = assets.get(clip.video_asset_id)
    thumb = assets.get(clip.thumbnail_asset_id)
    thumb_url = storage.presign_get(thumb.storage_key, settings.download_url_expires_seconds, public=True) \
        if thumb else None
    start, end = float(clip.start_seconds), float(clip.end_seconds)
    return ClipOut(
        id=clip.id, project_id=clip.project_id, title=clip.title, justification=clip.justification,
        score=clip.score, start_seconds=start, end_seconds=end,
        duration_seconds=float(video.duration_seconds) if video and video.duration_seconds else round(end - start, 3),
        status=clip.status, layout=clip.layout, render_version=clip.render_version, error_code=clip.error_code,
        width=video.width if video else None, height=video.height if video else None,
        size_bytes=video.size_bytes if video else None, thumbnail_url=thumb_url,
        has_subtitles=clip.subtitle_asset_id is not None, created_at=clip.created_at, updated_at=clip.updated_at,
    )


def download_url(db: Session, clip: Clip, storage: Storage, settings: Settings, *, file: str,
                 disposition: str) -> tuple[str, datetime, str] | None:
    asset_id = clip.video_asset_id if file == "video" else clip.subtitle_asset_id
    if asset_id is None:
        return None
    asset = db.get(MediaAsset, asset_id)
    ext = "mp4" if file == "video" else "srt"
    name = safe_filename(clip.title, ext)
    expires = settings.download_url_expires_seconds
    url = storage.presign_get(asset.storage_key, expires, public=True,
                              download_name=name if disposition == "attachment" else None)
    return url, datetime.now(UTC) + timedelta(seconds=expires), name
