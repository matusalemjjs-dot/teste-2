"""Inicia o worker: `python -m app.worker.run` (opção `--burst` processa a fila e encerra)."""

import argparse
import logging
import sys

from rq import Worker

from app.core.config import get_settings
from app.core.logging import configure_logging, log_event
from app.db.session import get_engine, get_sessionmaker
from app.services.jobs import sweep_stale_jobs
from app.services.queue import get_queue, get_redis
from app.worker.limits import apply_limits
from app.worker.tasks import on_work_horse_killed
from app.worker.tmp import cleanup_stale

logger = logging.getLogger("autoclip.worker")


class LimitedWorker(Worker):
    """Worker padrão (fork por job) que aplica limites de memória/CPU/arquivo no processo do job."""

    def main_work_horse(self, job, queue):
        # O processo filho não pode reutilizar as conexões do pai (recomendação do SQLAlchemy para fork).
        get_engine().dispose(close=False)
        limits = apply_limits(get_settings())
        log_event(logger, "Limites aplicados ao job", job_id=job.id, **limits)
        return super().main_work_horse(job, queue)


def maintenance() -> dict:
    settings = get_settings()
    with get_sessionmaker()() as db:
        stale = sweep_stale_jobs(db, settings)
    return {"stale_jobs_failed": stale, "tmp_dirs_removed": cleanup_stale(settings)}


def build_worker() -> Worker:
    queue = get_queue()
    if sys.platform == "win32":
        # Sem fork e sem SIGALRM no Windows: executa no próprio processo, com timeout por thread.
        from rq.timeouts import TimerDeathPenalty
        from rq.worker import SimpleWorker

        class WindowsWorker(SimpleWorker):
            death_penalty_class = TimerDeathPenalty

        return WindowsWorker([queue], connection=get_redis())
    return LimitedWorker([queue], connection=get_redis(), work_horse_killed_handler=on_work_horse_killed)


def main() -> None:
    parser = argparse.ArgumentParser(description="Worker de processamento do AutoClip IA")
    parser.add_argument("--burst", action="store_true", help="processa os jobs pendentes e encerra")
    args = parser.parse_args()
    settings = get_settings()
    configure_logging(settings.log_level)
    log_event(logger, "Manutenção na inicialização", **maintenance())
    build_worker().work(burst=args.burst, logging_level=settings.log_level)


if __name__ == "__main__":
    main()
