"""Sobe a pilha completa para os testes E2E do frontend (Playwright).

S3 emulado (moto) + Redis + banco `autoclip_e2e` recém-migrado + worker real + API (uvicorn).
Gera também os vídeos usados pelos testes em tests/e2e/.artifacts/.
Uso: python -m tests.e2e.stack   (Ctrl+C encerra tudo)
"""

import atexit
import os
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ART = Path(__file__).resolve().parent / ".artifacts"
API_PORT = int(os.environ.get("E2E_API_PORT", "8100"))
S3_PORT = int(os.environ.get("E2E_S3_PORT", "5101"))
REDIS_PORT = int(os.environ.get("E2E_REDIS_PORT", "6391"))
WEB_ORIGIN = os.environ.get("E2E_WEB_ORIGIN", "http://127.0.0.1:3100")
DB = "autoclip_e2e"

ENV = {
    "ENVIRONMENT": "test", "LOG_LEVEL": "WARNING",
    "DATABASE_URL": f"postgresql+psycopg://autoclip:autoclip@localhost:5432/{DB}",
    "SUPABASE_URL": "https://e2e-local.supabase.co",
    "SUPABASE_JWT_SECRET": os.environ.get("E2E_JWT_SECRET", "segredo-e2e-apenas-local-com-32-bytes-ou-mais"),
    "CORS_ORIGINS": f'["{WEB_ORIGIN}"]', "S3_CORS_ORIGINS": f'["{WEB_ORIGIN}"]',
    "S3_ENDPOINT_URL": f"http://127.0.0.1:{S3_PORT}", "S3_PUBLIC_ENDPOINT_URL": f"http://127.0.0.1:{S3_PORT}",
    "S3_BUCKET": "autoclip-e2e", "S3_ACCESS_KEY_ID": "e2e", "S3_SECRET_ACCESS_KEY": "e2e-secret",
    "REDIS_URL": f"redis://127.0.0.1:{REDIS_PORT}/0", "QUEUE_NAME": "autoclip-e2e",
    "WHISPER_MODEL": "tiny", "WHISPER_BEAM_SIZE": "1", "RENDER_PRESET": "ultrafast", "ANTHROPIC_API_KEY": "",
    # beta fechado como em produção; os testes usam e-mails @exemplo.com (domínio liberado só aqui)
    "BETA_MODE": "true", "BETA_ALLOWED_EMAILS": "@exemplo.com",
    "WORKER_TMP_DIR": tempfile.mkdtemp(prefix="autoclip-e2e-"),
}
os.environ.update(ENV)
for var in ("NO_PROXY", "no_proxy"):
    os.environ[var] = ",".join(filter(None, [os.environ.get(var), "127.0.0.1", "localhost"]))

children: list[subprocess.Popen] = []


def cleanup(*_):
    for p in children:
        if p.poll() is None:
            p.terminate()
    for p in children:
        try:
            p.wait(10)
        except subprocess.TimeoutExpired:
            p.kill()


def ensure_db():
    import psycopg

    with psycopg.connect("postgresql://autoclip:autoclip@localhost:5432/postgres", autocommit=True) as conn:
        if not conn.execute("SELECT 1 FROM pg_database WHERE datname=%s", (DB,)).fetchone():
            conn.execute(f'CREATE DATABASE "{DB}"')
    from alembic.config import Config

    from alembic import command

    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")


def make_media():
    sys.path.insert(0, str(ROOT))
    from tests.media import FALA_LONGA, make_speech_video  # (não importar conftest: ele altera o ambiente)

    ART.mkdir(exist_ok=True)
    if not (ART / "fala.mp4").exists():
        make_speech_video(ART / "fala.mp4", FALA_LONGA, pad_to=90)
    (ART / "nao-e-video.mp4").write_bytes(b"isto nao e um video " * 2000)


def main():
    atexit.register(cleanup)
    signal.signal(signal.SIGTERM, lambda *a: (cleanup(), sys.exit(0)))
    from moto.server import ThreadedMotoServer

    moto = ThreadedMotoServer(ip_address="127.0.0.1", port=S3_PORT, verbose=False)
    moto.start()
    redis_dir = tempfile.mkdtemp(prefix="autoclip-e2e-redis-")
    children.append(subprocess.Popen(["redis-server", "--port", str(REDIS_PORT), "--bind", "127.0.0.1", "--save", "",
                                      "--appendonly", "no", "--dir", redis_dir], stdout=subprocess.DEVNULL))
    ensure_db()
    from app.storage_setup import setup

    print("storage:", "; ".join(setup()), flush=True)
    make_media()
    from app.core.config import get_settings
    from app.transcription.download import load_model  # garante o modelo antes do primeiro job

    load_model(get_settings())
    children.append(subprocess.Popen([sys.executable, "-m", "tests.e2e.worker"], cwd=ROOT, env=os.environ.copy()))
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=API_PORT, log_level="warning")


if __name__ == "__main__":
    main()
