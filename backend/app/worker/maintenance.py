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
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    while True:
        try:
            log_event(logger, "Manutenção executada", **run_maintenance(settings))
        except Exception:
            logger.exception("Falha na manutenção")
        if not args.loop:
            break
        time.sleep(settings.maintenance_interval_seconds)


if __name__ == "__main__":
    main()
