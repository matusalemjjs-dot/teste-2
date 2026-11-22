"""Prontidão e recepção de relatórios do navegador (erros de JavaScript e violações de CSP)."""

import json
import logging
import time
from datetime import UTC, datetime
from urllib.parse import urlsplit

from fastapi import HTTPException, Request
from sqlalchemy import func, select, text

from app.core.config import Settings
from app.core.logging import log_event, redact

logger = logging.getLogger("autoclip.client")
MAX_BODY = 8 * 1024


def readiness(settings: Settings) -> dict:
    from app.db.session import get_engine, get_sessionmaker
    from app.models import ProcessingJob
    from app.services.alerts import workers_vivos
    from app.services.queue import get_redis
    from app.services.storage import get_storage

    checks: dict = {}
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        checks["banco"] = True
    except Exception:
        checks["banco"] = False
    try:
        checks["redis"] = bool(get_redis().ping())
    except Exception:
        checks["redis"] = False
    try:
        get_storage()._internal.head_bucket(Bucket=settings.s3_bucket)
        checks["armazenamento"] = True
    except Exception:
        checks["armazenamento"] = False
    vivos = workers_vivos() if checks["redis"] else None
    checks["workers_vivos"] = vivos or 0
    if checks["banco"]:
        with get_sessionmaker()() as db:
            antigo = db.scalar(select(func.min(ProcessingJob.created_at)).where(ProcessingJob.status == "queued"))
            checks["na_fila"] = db.scalar(select(func.count()).select_from(ProcessingJob)
                                          .where(ProcessingJob.status == "queued"))
        checks["espera_mais_antiga_min"] = round((datetime.now(UTC) - antigo).total_seconds() / 60, 1) if antigo else 0
    checks["pronto"] = checks["banco"] and checks["redis"] and checks["armazenamento"] and checks["workers_vivos"] > 0
    return checks


def _limite_por_ip(request: Request, settings: Settings) -> None:
    ip = (request.client.host if request.client else "?")
    janela = int(time.time() // 60)
    try:
        from app.services.queue import get_redis

        r = get_redis()
        chave = f"rl:client:{ip}:{janela}"
        n = r.incr(chave)
        r.expire(chave, 70)
    except Exception:
        return  # sem Redis: não bloqueia (só registra em log)
    if n > settings.client_errors_per_minute:
        raise HTTPException(429, "Muitos relatórios.")


def _so_caminho(url: str | None) -> str | None:
    """Mantém só o caminho da URL (sem domínio, consulta ou fragmento: podem conter tokens)."""
    if not url or not isinstance(url, str):
        return None
    return urlsplit(url).path[:300] or "/"


async def record_client_report(request: Request, kind: str, settings: Settings) -> None:
    _limite_por_ip(request, settings)
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(413, "Relatório grande demais.")
    try:
        data = json.loads(body or b"{}")
    except ValueError:
        data = {}
    if kind == "csp_violation":
        # formatos: {"csp-report": {...}} (report-uri) ou lista de relatórios (Reporting API)
        rep = data.get("csp-report") if isinstance(data, dict) else None
        if rep is None and isinstance(data, list) and data:
            rep = data[0].get("body", {})
        rep = rep or {}
        campos = {"diretiva": str(rep.get("violated-directive") or rep.get("effectiveDirective") or "")[:100],
                  "bloqueado": _so_caminho(rep.get("blocked-uri") or rep.get("blockedURL"))
                  if str(rep.get("blocked-uri") or rep.get("blockedURL") or "").startswith("http")
                  else str(rep.get("blocked-uri") or rep.get("blockedURL") or "")[:50],
                  "origem_bloqueada": urlsplit(str(rep.get("blocked-uri") or rep.get("blockedURL") or "")).netloc[:100],
                  "pagina": _so_caminho(rep.get("document-uri") or rep.get("documentURL"))}
    else:
        d = data if isinstance(data, dict) else {}
        campos = {"mensagem": redact(str(d.get("message", ""))[:500]), "pagina": _so_caminho(d.get("url")),
                  "pilha": redact(str(d.get("stack", ""))[:2000]), "origem": str(d.get("source", ""))[:50]}
    log_event(logger, "Relatório do navegador", tipo=kind, **campos)
