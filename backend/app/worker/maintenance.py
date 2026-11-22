"""Manutenção periódica: recupera jobs perdidos e limpa temporários.

Uso:  python -m app.worker.maintenance            (uma rodada)
      python -m app.worker.maintenance --loop     (a cada MAINTENANCE_INTERVAL_SECONDS)

Uma trava do Postgres (pg_try_advisory_lock, sem tabela nova) garante que só uma rodada execute por vez,
mesmo com vários workers ou agendadores.
"""

import argparse
import logging
import time

from sqlalchemy import text

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, log_event
from app.db.session import get_engine, get_sessionmaker
from app.services.jobs import reconcile_queued, sweep_stale_jobs
from app.services.queue import QueueUnavailable
from app.worker.tmp import cleanup_stale

logger = logging.getLogger("autoclip.maintenance")
LOCK_KEY = 7_202_611_001  # identificador fixo da trava de manutenção
RETENTION_LOCK_KEY = 7_202_611_002
RETENTION_LAST_KEY = "autoclip:retention:last_run"


def run_retention_locked(settings: Settings | None = None, now=None) -> dict:
    """Uma rodada de retenção, protegida por trava própria (nunca duas ao mesmo tempo)."""
    from dataclasses import asdict

    from app.services.retention import run_retention
    from app.services.storage import get_storage

    settings = settings or get_settings()
    with get_engine().connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": RETENTION_LOCK_KEY}).scalar():
            return {"skipped": True}
        try:
            with get_sessionmaker()() as db:
                return {"skipped": False, **asdict(run_retention(db, get_storage(), settings, now=now))}
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": RETENTION_LOCK_KEY})
            conn.commit()


def retention_due(settings: Settings) -> bool:
    """A retenção roda no máximo a cada RETENTION_INTERVAL_HOURS (marcador no Redis; se o Redis perder o
    marcador, ela roda de novo — é idempotente)."""
    from app.services.queue import get_redis

    try:
        return bool(get_redis().set(RETENTION_LAST_KEY, "1", nx=True, ex=settings.retention_interval_hours * 3600))
    except Exception:
        return False


def run_maintenance(settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    with get_engine().connect() as conn:
        if not conn.execute(text("SELECT pg_try_advisory_lock(:k)"), {"k": LOCK_KEY}).scalar():
            return {"skipped": True}
        try:
            report: dict = {"skipped": False}
            with get_sessionmaker()() as db:
                report["stale_jobs_failed"] = sweep_stale_jobs(db, settings)
                try:
                    report["queue"] = reconcile_queued(db, settings)
                except QueueUnavailable:
                    report["queue"] = "redis_indisponivel"
            report["tmp_dirs_removed"] = cleanup_stale(settings)
            return report
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": LOCK_KEY})
            conn.commit()


def ciclo(settings: Settings, *, forcar_retencao: bool = False) -> dict:
    """Uma rodada completa: manutenção, retenção (se devida), alertas e relatório diário (se devido)."""
    from app.services.alerts import Alert, daily_report_due, evaluate, send, send_daily_report

    rodada = run_maintenance(settings)
    log_event(logger, "Manutenção executada", **rodada)
    extras: list[Alert] = []
    if not rodada.get("skipped"):
        if rodada.get("stale_jobs_failed"):
            extras.append(Alert("worker_lost", f"{rodada['stale_jobs_failed']} job(s) sem sinal de vida foram "
                                               "marcados como perdidos (worker morto?)."))
        q = rodada.get("queue")
        if q == "redis_indisponivel":
            extras.append(Alert("redis", "Redis inacessível durante a manutenção."))
        elif isinstance(q, dict) and q.get("lost"):
            extras.append(Alert("queue_lost", f"{q['lost']} job(s) perdidos na fila após várias devoluções."))
    retencao = None
    if forcar_retencao or retention_due(settings):
        retencao = run_retention_locked(settings)
        log_event(logger, "Retenção executada", **{k: v for k, v in retencao.items() if k != "failures"},
                  failures=len(retencao.get("failures", [])))
        if retencao.get("failures"):
            extras.append(Alert("retencao", f"A retenção teve {len(retencao['failures'])} falha(s); será "
                                            "tentada de novo na próxima rodada."))
    with get_sessionmaker()() as db:
        alertas = evaluate(db, settings) + extras
        enviados = send(settings, alertas)
        relatorio = send_daily_report(db, settings) if daily_report_due(settings) else None
    return {"manutencao": rodada, "retencao": retencao, "alertas": [a.key for a in alertas],
            "alertas_enviados": [a.key for a in enviados], "relatorio_enviado": relatorio is not None}


def main() -> None:
    parser = argparse.ArgumentParser(description="Manutenção do AutoClip IA")
    parser.add_argument("--loop", action="store_true", help="repete a cada MAINTENANCE_INTERVAL_SECONDS")
    parser.add_argument("--retencao", action="store_true", help="roda a retenção agora (além da manutenção)")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    while True:
        try:
            ciclo(settings, forcar_retencao=args.retencao)
        except Exception:
            logger.exception("Falha na manutenção")
            from app.services.alerts import Alert, send
            send(settings, [Alert("manutencao", "A manutenção periódica falhou; veja os logs do serviço.")])
        if not args.loop:
            break
        time.sleep(settings.maintenance_interval_seconds)


if __name__ == "__main__":
    main()
