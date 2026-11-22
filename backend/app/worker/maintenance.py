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


def main() -> None:
    parser = argparse.ArgumentParser(description="Manutenção do AutoClip IA")
    parser.add_argument("--loop", action="store_true", help="repete a cada MAINTENANCE_INTERVAL_SECONDS")
    parser.add_argument("--retencao", action="store_true", help="roda a retenção agora (além da manutenção)")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    while True:
        try:
            log_event(logger, "Manutenção executada", **run_maintenance(settings))
            if args.retencao:
                log_event(logger, "Retenção executada (manual)", **run_retention_locked(settings))
            elif retention_due(settings):
                report = run_retention_locked(settings)
                log_event(logger, "Retenção executada", **{k: v for k, v in report.items() if k != "failures"},
                          failures=len(report.get("failures", [])))
        except Exception:
            logger.exception("Falha na manutenção")
        if not args.loop:
            break
        time.sleep(settings.maintenance_interval_seconds)


if __name__ == "__main__":
    main()
