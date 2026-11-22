"""Diretórios temporários dos jobs e limpeza de órfãos."""

import shutil
import tempfile
import time
from pathlib import Path

from app.core.config import Settings


def tmp_root(settings: Settings) -> Path:
    root = Path(settings.worker_tmp_dir or Path(tempfile.gettempdir()) / "autoclip")
    root.mkdir(parents=True, exist_ok=True)
    return root


def job_dir(settings: Settings, job_id: str) -> Path:
    path = tmp_root(settings) / f"job-{job_id}"
    if path.exists():  # resto de uma execução anterior do mesmo job
        shutil.rmtree(path, ignore_errors=True)
    path.mkdir(mode=0o700)
    return path


def cleanup_stale(settings: Settings, now: float | None = None) -> int:
    """Remove diretórios de jobs mais antigos que `worker_tmp_max_age_hours` (ex.: worker morto)."""
    now = now or time.time()
    limit = settings.worker_tmp_max_age_hours * 3600
    removed = 0
    for path in tmp_root(settings).glob("job-*"):
        try:
            if now - path.stat().st_mtime > limit:
                shutil.rmtree(path, ignore_errors=True)
                removed += 1
        except FileNotFoundError:
            pass
    return removed
