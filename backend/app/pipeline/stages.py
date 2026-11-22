"""Etapas do pipeline. Cada etapa recebe o JobContext e só registra resultados reais.

Etapas ainda não implementadas falham explicitamente com `stage_unavailable`: o sistema nunca marca
um projeto como concluído sem ter executado o processamento de verdade.
"""

import hashlib
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select

from app.db.session import get_sessionmaker
from app.models import MediaAsset, Project, TranscriptSegment
from app.models.enums import AssetKind, AssetStatus
from app.models.enums import ProjectStatus as PS
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


def extract_audio(ctx: JobContext, fmt: str) -> Path:
    src = ctx.data["source_path"]
    s = ctx.settings
    if fmt == "wav":
        out, codec = ctx.workdir / "audio.wav", ["-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le"]
    else:
        out, codec = ctx.workdir / "audio.mp3", ["-ac", "1", "-ar", "16000", "-c:a", "libmp3lame", "-b:a", "32k"]
    cmd = [s.ffmpeg_path, "-nostdin", "-y", "-v", "error", "-threads", str(s.ffmpeg_threads), "-i", str(src),
           "-map", "0:a:0", "-vn", *codec, str(out)]
    r = ctx.run(cmd, timeout=s.transcription_timeout_seconds)
    if r.returncode != 0 or not out.exists() or out.stat().st_size == 0:
        raise PipelineError("audio_extraction_failed", "Não foi possível extrair o áudio do vídeo.",
                            detail=r.stderr.decode("utf-8", "replace")[-2000:])
    return out


def transcribe(ctx: JobContext) -> None:
    """Extrai o áudio, transcreve com o provedor configurado e grava os segmentos (substituindo os anteriores)."""
    from app.transcription import TranscriptionError, get_transcriber
    from app.transcription.base import normalize_segments

    try:
        transcriber = get_transcriber(ctx.settings)
    except TranscriptionError as exc:
        raise PipelineError(exc.code, exc.message, exc.detail) from exc
    with get_sessionmaker()() as db:
        duration = float(db.scalar(select(Project.source_duration_seconds).where(Project.id == ctx.project_id)) or 0)

    ctx.stage_progress(0.0, "Extraindo o áudio", min_interval=0)
    audio = extract_audio(ctx, transcriber.audio_format)
    ctx.stage_progress(0.1, "Transcrevendo o áudio", min_interval=0)

    def on_progress(fraction: float) -> None:
        ctx.stage_progress(0.1 + 0.9 * fraction, f"Transcrevendo o áudio ({int(fraction * 100)}%)")

    try:
        result = transcriber.transcribe(audio, duration, on_progress)
    except TranscriptionError as exc:
        raise PipelineError(exc.code, exc.message, exc.detail) from exc
    finally:
        audio.unlink(missing_ok=True)

    segments = normalize_segments(result.segments, duration or result.audio_duration)
    if not segments:
        raise PipelineError("no_speech", "Não identificamos fala no vídeo. Os cortes são gerados a partir da fala.")

    with get_sessionmaker()() as db:
        project = db.scalar(select(Project).where(Project.id == ctx.project_id).with_for_update())
        if project is None or PS(project.status) != PS.processing:
            db.rollback()
            from app.pipeline.context import Canceled

            raise Canceled()
        # Idempotência: uma nova execução substitui a transcrição anterior em vez de duplicá-la.
        db.execute(delete(TranscriptSegment).where(TranscriptSegment.project_id == ctx.project_id))
        db.add_all([
            TranscriptSegment(
                project_id=ctx.project_id, seq=i, start_seconds=seg.start, end_seconds=seg.end, text=seg.text,
                confidence=seg.confidence,
                words=[{"start": round(w.start, 3), "end": round(w.end, 3), "word": w.word} for w in seg.words]
                or None,
            )
            for i, seg in enumerate(segments)
        ])
        db.commit()
    ctx.data["transcript"] = segments
    ctx.data["language"] = result.language


def analyze(ctx: JobContext) -> None:
    """Gera janelas de 15–60 s, avalia com o modelo de linguagem, valida e seleciona até N trechos distintos."""
    from decimal import Decimal

    from app.analysis.llm import LLMError, get_scorer
    from app.analysis.scoring import score, validate
    from app.analysis.scoring import select as select_candidates
    from app.analysis.windows import Seg, build_windows
    from app.models import ClipCandidate, UsageLedger

    s = ctx.settings
    with get_sessionmaker()() as db:
        rows = db.scalars(select(TranscriptSegment).where(TranscriptSegment.project_id == ctx.project_id)
                          .order_by(TranscriptSegment.seq)).all()
        duration = float(db.scalar(select(Project.source_duration_seconds).where(Project.id == ctx.project_id)) or 0)
    segs = [Seg(r.seq, float(r.start_seconds), float(r.end_seconds), r.text,
                float(r.confidence) if r.confidence is not None else None) for r in rows]
    windows = build_windows(segs, s.clip_min_seconds, s.clip_max_seconds, s.llm_max_windows)
    if not windows:
        raise PipelineError("no_candidates", f"Não há fala suficiente para formar trechos de "
                                             f"{s.clip_min_seconds:g} a {s.clip_max_seconds:g} segundos.")
    try:
        scorer = get_scorer(s)
    except LLMError as exc:
        raise PipelineError(exc.code, exc.message, exc.detail) from exc

    by_id = {w.id: w for w in windows}
    evaluations = []
    usage_in = usage_out = 0
    batches = [windows[i:i + s.llm_windows_per_request] for i in range(0, len(windows), s.llm_windows_per_request)]
    try:
        for n, batch in enumerate(batches):
            ctx.stage_progress(n / len(batches), f"Analisando trechos ({n * 100 // len(batches)}%)", min_interval=0)
            raw, usage = scorer.evaluate(batch)
            usage_in += usage.input_tokens
            usage_out += usage.output_tokens
            batch_ids = {w.id: w for w in batch}
            evaluations.extend(validate(raw, batch_ids))
    except LLMError as exc:
        raise PipelineError(exc.code, exc.message, exc.detail) from exc
    finally:
        if usage_in or usage_out:  # registra consumo real, uma vez por job (chave idempotente)
            cost = (usage_in * s.llm_input_price_per_mtok + usage_out * s.llm_output_price_per_mtok) / 1_000_000
            with get_sessionmaker()() as db:
                from sqlalchemy.dialects.postgresql import insert

                db.execute(insert(UsageLedger).values(
                    owner_id=ctx.owner_id, project_id=ctx.project_id, job_id=ctx.job_id, kind="llm_tokens",
                    quantity=usage_in + usage_out, estimated_cost_usd=Decimal(f"{cost:.6f}"),
                    idempotency_key=f"llm_tokens:{ctx.job_id}",
                ).on_conflict_do_nothing(index_elements=["idempotency_key"]))
                db.commit()

    candidates = [score(ev, by_id[ev.window_id]) for ev in evaluations]
    chosen = select_candidates(candidates, duration=duration, min_s=s.clip_min_seconds, max_s=s.clip_max_seconds,
                    limit=s.max_clips)
    if not chosen:
        raise PipelineError("no_candidates", "A análise não encontrou trechos válidos para gerar cortes.",
                            detail=f"windows={len(windows)} evaluations={len(evaluations)}")

    with get_sessionmaker()() as db:
        project = db.scalar(select(Project).where(Project.id == ctx.project_id).with_for_update())
        if project is None or PS(project.status) != PS.processing:
            db.rollback()
            from app.pipeline.context import Canceled

            raise Canceled()
        db.execute(delete(ClipCandidate).where(ClipCandidate.project_id == ctx.project_id))  # idempotência
        db.add_all([
            ClipCandidate(project_id=ctx.project_id, start_seconds=c.window.start, end_seconds=c.window.end,
                          score=c.score, title=c.title, justification=c.justification,
                          criteria={**c.criteria, "first_seq": c.window.first_seq, "last_seq": c.window.last_seq},
                          selected=c.selected, model_name=scorer.model_name)
            for c in candidates
        ])
        db.commit()
    ctx.data["selected_candidates"] = [(c.window.start, c.window.end) for c in chosen]


def render(ctx: JobContext) -> None:
    """Renderiza os trechos selecionados. Um corte com falha não impede os demais."""
    from app.models import Clip, ClipCandidate
    from app.models.enums import ClipStatus
    from app.rendering.clips import render_one, reset_project_clips
    from app.rendering.render import RenderError

    s = ctx.settings
    reset_project_clips(ctx)
    with get_sessionmaker()() as db:
        cands = db.scalars(select(ClipCandidate).where(ClipCandidate.project_id == ctx.project_id,
                                                       ClipCandidate.selected.is_(True))
                           .order_by(ClipCandidate.score.desc(), ClipCandidate.start_seconds)).all()
        clips = [Clip(project_id=ctx.project_id, owner_id=ctx.owner_id, candidate_id=c.id, title=c.title,
                      justification=c.justification, score=c.score, start_seconds=c.start_seconds,
                      end_seconds=c.end_seconds, status=ClipStatus.rendering.value, layout=s.render_default_layout,
                      render_version=1) for c in cands]
        db.add_all(clips)
        db.commit()
        clip_ids = [c.id for c in clips]
    if not clip_ids:
        raise PipelineError("no_candidates", "Nenhum trecho selecionado para gerar cortes.")

    ok = 0
    for n, clip_id in enumerate(clip_ids):
        ctx.stage_progress(n / len(clip_ids), f"Gerando cortes ({n} de {len(clip_ids)})", min_interval=0)
        try:
            render_one(ctx, clip_id, layout=s.render_default_layout, subtitles=s.render_subtitles, version=1)
            ok += 1
        except RenderError as exc:
            with get_sessionmaker()() as db:
                clip = db.get(Clip, clip_id)
                if clip is not None:
                    clip.status, clip.error_code = ClipStatus.failed.value, exc.code
                    db.commit()
            import logging

            from app.core.logging import log_event
            log_event(logging.getLogger("autoclip.render"), "Falha ao renderizar corte", clip_id=str(clip_id),
                      code=exc.code)
    if ok == 0:
        raise PipelineError("render_failed", "Não foi possível gerar nenhum corte. Tente novamente.")
    ctx.data["final_message"] = (f"{ok} cortes gerados" if ok == len(clip_ids)
                                 else f"{ok} de {len(clip_ids)} cortes gerados; os demais falharam")


def unavailable(label: str) -> Callable[[JobContext], None]:
    def run(ctx: JobContext) -> None:
        raise PipelineError("stage_unavailable",
                            f"A etapa de {label} ainda não está disponível nesta versão do AutoClip IA. "
                            "Nenhum corte foi gerado.")  # etapas anteriores (ex.: transcrição) ficam salvas
    return run


def default_pipeline() -> list[Stage]:
    return [
        Stage("prepare", "preparação do vídeo", 5, prepare_source),
        Stage("transcribe", "transcrição", 35, transcribe),
        Stage("analyze", "análise inteligente", 20, analyze),
        Stage("render", "renderização dos cortes", 40, render),
    ]


def get_pipeline() -> list[Stage]:
    return default_pipeline()
