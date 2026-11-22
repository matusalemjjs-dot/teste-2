"""Sinal de vida do job: uma thread registra atividade enquanto o processo do job está vivo.

Independe do que o pipeline está fazendo (FFmpeg, Whisper, chamada ao modelo de linguagem), então não
exige nenhuma mudança no código de processamento. Se o processo morrer (queda da máquina, SIGKILL,
falta de memória), o sinal para e a manutenção marca o job como `worker_lost`.
"""

import threading
import uuid

from sqlalchemy import text

from app.db.session import get_engine


class Heartbeat:
    def __init__(self, job_id: uuid.UUID, interval: float):
        self.job_id = job_id
        self.interval = interval
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name=f"heartbeat-{job_id}", daemon=True)

    def beat(self) -> None:
        with get_engine().begin() as conn:
            conn.execute(text("UPDATE processing_jobs SET updated_at = now() WHERE id = :id AND status = 'running'"),
                         {"id": self.job_id})

    def _run(self) -> None:
        while not self._stop.wait(self.interval):
            try:
                self.beat()
            except Exception:  # banco momentaneamente indisponível: tenta no próximo intervalo
                pass

    def __enter__(self) -> "Heartbeat":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._stop.set()
        self._thread.join(timeout=5)
