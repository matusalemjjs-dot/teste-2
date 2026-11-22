"""Integração com Redis/RQ."""

from functools import lru_cache

from redis import Redis
from rq import Queue

from app.core.config import get_settings

PROCESS_TASK = "app.worker.tasks.process_project"


class QueueUnavailable(Exception):
    pass


@lru_cache
def get_redis() -> Redis:
    return Redis.from_url(get_settings().redis_url, socket_connect_timeout=3, socket_timeout=10)


def get_queue() -> Queue:
    return Queue(get_settings().queue_name, connection=get_redis())


def enqueue_process(job_id: str) -> str:
    settings = get_settings()
    try:
        job = get_queue().enqueue_call(
            PROCESS_TASK,
            args=(job_id,),
            job_id=job_id,  # o id do job no RQ é o id em processing_jobs
            timeout=settings.job_timeout_seconds,
            result_ttl=3600,
            failure_ttl=7 * 24 * 3600,
            description=f"process_project {job_id}",
        )
    except Exception as exc:  # redis indisponível, timeout de conexão etc.
        raise QueueUnavailable(str(exc)) from exc
    return job.id


def cancel_queued(job_id: str) -> None:
    """Remove da fila um job que ainda não começou. Falhas são ignoradas: o worker também confere o
    status no banco antes de começar."""
    from rq.job import Job

    try:
        Job.fetch(job_id, connection=get_redis()).cancel()
    except Exception:
        pass
