"""Alertas operacionais e relatório diário, enviados para um webhook (Slack, Discord ou JSON genérico).

Os alertas também vão para o log (nível ERROR) — o webhook é um canal a mais, não o único. O mesmo alerta não
é repetido antes de ALERT_REPEAT_MINUTES (marcador no Redis; sem Redis, controle em memória do processo).
Nenhum alerta contém dados pessoais (e-mails, títulos, transcrições).
"""

import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.logging import log_event
from app.models import ProcessingJob

logger = logging.getLogger("autoclip.alerts")
_memoria: dict[str, float] = {}


@dataclass(frozen=True)
class Alert:
    key: str
    message: str


def workers_vivos() -> int | None:
    try:
        from rq import Worker

        from app.services.queue import get_redis
        return len(Worker.all(connection=get_redis()))
    except Exception:
        return None


def evaluate(db: Session, settings: Settings, now: datetime | None = None) -> list[Alert]:
    now = now or datetime.now(UTC)
    alerts: list[Alert] = []
    falhas = db.scalar(select(func.count()).select_from(ProcessingJob).where(
        ProcessingJob.status == "failed", ProcessingJob.finished_at >= now - timedelta(hours=1))) or 0
    if falhas >= settings.alert_failures_per_hour:
        alerts.append(Alert("falhas", f"{falhas} processamentos falharam na última hora "
                                      f"(limite de alerta: {settings.alert_failures_per_hour})."))
    mais_antigo = db.scalar(select(func.min(ProcessingJob.created_at)).where(ProcessingJob.status == "queued"))
    if mais_antigo and now - mais_antigo > timedelta(minutes=settings.alert_queue_wait_minutes):
        espera = int((now - mais_antigo).total_seconds() // 60)
        alerts.append(Alert("fila", f"Há job esperando na fila há {espera} min "
                                    f"(limite: {settings.alert_queue_wait_minutes} min)."))
    vivos = workers_vivos()
    if vivos is None:
        alerts.append(Alert("redis", "Redis inacessível: a fila e o limite de requisições estão sem funcionar."))
    elif vivos == 0:
        alerts.append(Alert("workers", "Nenhum worker vivo: nenhum vídeo está sendo processado."))
    if settings.llm_budget_total_usd > 0:
        from app.services.quotas import _llm_reserved, _llm_spent, month_bounds

        if settings.beta_mode and settings.beta_period_start and settings.beta_period_end:
            ini, fim = settings.beta_period_start, settings.beta_period_end
            ini = ini.replace(tzinfo=UTC) if ini.tzinfo is None else ini
            fim = fim.replace(tzinfo=UTC) if fim.tzinfo is None else fim
        else:
            ini, fim = month_bounds(now)
        gasto = _llm_spent(db, None, ini, fim) + _llm_reserved(db, settings, None)
        if gasto >= settings.alert_budget_fraction * settings.llm_budget_total_usd:
            alerts.append(Alert("custo", f"Custo de IA em US$ {gasto:.2f} (com reservas), "
                                         f"{gasto / settings.llm_budget_total_usd:.0%} do teto de "
                                         f"US$ {settings.llm_budget_total_usd:.2f}."))
    db.rollback()
    return alerts


def _primeira_vez(key: str, settings: Settings) -> bool:
    ttl = settings.alert_repeat_minutes * 60
    try:
        from app.services.queue import get_redis
        return bool(get_redis().set(f"autoclip:alert:{key}", "1", nx=True, ex=ttl))
    except Exception:
        agora = time.monotonic()
        if agora - _memoria.get(key, -1e12) < ttl:
            return False
        _memoria[key] = agora
        return True


def post_webhook(settings: Settings, text: str, payload: dict | None = None) -> bool:
    if not settings.alert_webhook_url:
        return False
    fmt = settings.alert_webhook_format.lower()
    body = {"text": text} if fmt == "slack" else {"content": text[:1900]} if fmt == "discord" \
        else {"text": text, **(payload or {})}
    req = urllib.request.Request(settings.alert_webhook_url.get_secret_value(), method="POST",
                                 data=json.dumps(body, ensure_ascii=False).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:  # noqa: S310 (URL da configuração)
            return 200 <= r.status < 300
    except (urllib.error.URLError, TimeoutError):
        log_event(logger, "Falha ao enviar webhook de alerta")
        return False


def send(settings: Settings, alerts: list[Alert]) -> list[Alert]:
    """Registra todos no log; envia ao webhook só os que não foram enviados recentemente."""
    enviados = []
    for a in alerts:
        logger.error("ALERTA %s: %s", a.key, a.message)
        if _primeira_vez(a.key, settings):
            enviados.append(a)
    if enviados:
        texto = "🚨 AutoClip IA — alerta\n" + "\n".join(f"• {a.message}" for a in enviados)
        post_webhook(settings, texto, {"alerts": [{"key": a.key, "message": a.message} for a in enviados]})
    return enviados


def daily_report_due(settings: Settings) -> bool:
    if not settings.daily_report_enabled:
        return False
    try:
        from app.services.queue import get_redis
        return bool(get_redis().set("autoclip:daily_report", "1", nx=True,
                                    ex=settings.daily_report_interval_hours * 3600))
    except Exception:
        return False


def send_daily_report(db: Session, settings: Settings) -> str:
    """Texto do relatório (sem dados pessoais) enviado ao webhook."""
    import contextlib
    import io

    from app.admin import _imprimir_relatorio, relatorio

    rep = relatorio(db, settings, settings.daily_report_interval_hours)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        _imprimir_relatorio(rep)
    texto = "📊 " + buf.getvalue().strip()
    post_webhook(settings, texto, {"relatorio": rep})
    return texto
