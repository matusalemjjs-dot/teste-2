"""Limites de recursos do processo que executa o job (herdados pelos subprocessos FFmpeg)."""

import logging
import sys

from app.core.config import Settings

logger = logging.getLogger("autoclip.worker")


def apply_limits(settings: Settings) -> dict:
    applied: dict = {}
    if sys.platform == "win32":
        return applied  # sem setrlimit no Windows; use limites do contêiner/WSL
    import resource

    def set_limit(name: str, res: int, value: int) -> None:
        _, hard = resource.getrlimit(res)
        if hard != resource.RLIM_INFINITY:
            value = min(value, hard)
        resource.setrlimit(res, (value, hard))
        applied[name] = value

    if settings.worker_memory_limit_mb > 0:
        set_limit("memory_bytes", resource.RLIMIT_AS, settings.worker_memory_limit_mb * 1024 * 1024)
    if settings.worker_cpu_limit_seconds > 0:
        set_limit("cpu_seconds", resource.RLIMIT_CPU, settings.worker_cpu_limit_seconds)
    # Nenhum arquivo gerado pode passar de 2x o tamanho máximo de upload.
    set_limit("file_size_bytes", resource.RLIMIT_FSIZE, settings.max_upload_bytes * 2)
    return applied
