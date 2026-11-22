"""Contexto de execução de um job: progresso, cancelamento cooperativo e execução de comandos."""

import os
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from sqlalchemy import select

from app.core.config import Settings
from app.db.session import get_sessionmaker
from app.models import ProcessingJob, Project
from app.models.enums import ProjectStatus as S


class PipelineError(Exception):
    """Falha esperada, com mensagem para o usuário em português."""

    def __init__(self, code: str, message: str, detail: str | None = None):
        super().__init__(f"{code}: {message}")
        self.code, self.message, self.detail = code, message, detail


class Canceled(Exception):
    pass


@dataclass
class JobContext:
    job_id: uuid.UUID
    project_id: uuid.UUID
    owner_id: uuid.UUID
    workdir: Path
    settings: Settings
    data: dict = field(default_factory=dict)  # resultados entre etapas (ex.: caminho do vídeo baixado)
    _last_check: float = 0.0

    def is_canceled(self) -> bool:
        with get_sessionmaker()() as db:
            status = db.scalar(select(Project.status).where(Project.id == self.project_id))
        return status is None or S(status) == S.canceled

    def checkpoint(self, *, min_interval: float = 0.0) -> None:
        """Interrompe o job se o projeto foi cancelado ou excluído."""
        now = time.monotonic()
        if min_interval and now - self._last_check < min_interval:
            return
        self._last_check = now
        if self.is_canceled():
            raise Canceled()

    def progress(self, percent: int, message: str, stage: str | None = None) -> None:
        percent = max(0, min(99, int(percent)))  # 100 só ao concluir de verdade
        with get_sessionmaker()() as db:
            project = db.scalar(select(Project).where(Project.id == self.project_id).with_for_update())
            if project is None or S(project.status) != S.processing:
                db.rollback()
                raise Canceled()
            project.progress = max(project.progress, percent)
            project.status_message = message
            job = db.get(ProcessingJob, self.job_id)
            if job is not None and stage:
                job.stage = stage
            db.commit()

    def run(self, cmd: list[str], timeout: int, *, poll: float = 1.0) -> subprocess.CompletedProcess:
        """Executa um comando externo com timeout e cancelamento cooperativo. Saída limitada a arquivos
        no diretório de trabalho para não acumular memória."""
        out_path = self.workdir / f".cmd-{uuid.uuid4().hex}.out"
        err_path = self.workdir / f".cmd-{uuid.uuid4().hex}.err"
        start = time.monotonic()
        with out_path.open("wb") as out, err_path.open("wb") as err:
            proc = subprocess.Popen(cmd, stdout=out, stderr=err, stdin=subprocess.DEVNULL, cwd=self.workdir)
            try:
                while True:
                    try:
                        proc.wait(timeout=poll)
                        break
                    except subprocess.TimeoutExpired:
                        if time.monotonic() - start > timeout:
                            raise PipelineError("stage_timeout",
                                                "Uma etapa do processamento excedeu o tempo limite.") from None
                        self.checkpoint()
            finally:
                if proc.poll() is None:
                    proc.kill()
                    proc.wait()
        result = subprocess.CompletedProcess(cmd, proc.returncode, out_path.read_bytes()[-1_000_000:],
                                             err_path.read_bytes()[-20_000:])
        for p in (out_path, err_path):
            os.unlink(p)
        return result
