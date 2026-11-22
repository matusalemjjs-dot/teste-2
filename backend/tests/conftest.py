import os
import time
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://autoclip:autoclip@localhost:5432/autoclip_test")
os.environ["ENVIRONMENT"] = "test"
os.environ["SUPABASE_URL"] = "https://exemplo-teste.supabase.co"
os.environ["SUPABASE_JWT_SECRET"] = "segredo-de-teste-apenas-local-com-32-bytes-ou-mais"
os.environ["LOG_LEVEL"] = "WARNING"
os.environ.setdefault("WHISPER_MODEL", "tiny")  # modelo pequeno para os testes
os.environ.setdefault("WHISPER_BEAM_SIZE", "1")
os.environ.setdefault("RENDER_PRESET", "ultrafast")  # resolução final é a real (1080x1920); só acelera o encode
os.environ.setdefault("ANTHROPIC_API_KEY", "")  # testes não chamam a API real, salvo test_llm_real

import socket  # noqa: E402


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]

S3_PORT = _free_port()
os.environ["S3_ENDPOINT_URL"] = f"http://127.0.0.1:{S3_PORT}"
os.environ["S3_PUBLIC_ENDPOINT_URL"] = f"http://127.0.0.1:{S3_PORT}"
os.environ["S3_BUCKET"] = "autoclip-test"
os.environ["S3_ACCESS_KEY_ID"] = "teste"
os.environ["S3_SECRET_ACCESS_KEY"] = "teste-secreto"
REDIS_PORT = _free_port()
os.environ["REDIS_URL"] = f"redis://127.0.0.1:{REDIS_PORT}/0"
os.environ["QUEUE_NAME"] = "autoclip-test"
# O emulador S3 é local: não passar pelo proxy HTTP do ambiente (boto3 e ffmpeg respeitam NO_PROXY).
for var in ("NO_PROXY", "no_proxy"):
    os.environ[var] = ",".join(filter(None, [os.environ.get(var), "127.0.0.1", "localhost"]))

import jwt  # noqa: E402
import pytest  # noqa: E402
from alembic.config import Config  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import text  # noqa: E402

from alembic import command  # noqa: E402
from app.db.session import get_engine  # noqa: E402

ISSUER = "https://exemplo-teste.supabase.co/auth/v1"
SECRET = os.environ["SUPABASE_JWT_SECRET"]
BACKEND_DIR = os.path.dirname(os.path.dirname(__file__))


def alembic_config() -> Config:
    cfg = Config(os.path.join(BACKEND_DIR, "alembic.ini"))
    cfg.set_main_option("script_location", os.path.join(BACKEND_DIR, "alembic"))
    return cfg


@pytest.fixture(scope="session", autouse=True)
def migrated_db():
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    command.upgrade(cfg, "head")
    yield


@pytest.fixture(scope="session", autouse=True)
def s3_server():
    from moto.server import ThreadedMotoServer

    server = ThreadedMotoServer(ip_address="127.0.0.1", port=S3_PORT, verbose=False)
    server.start()
    from app.services.storage import get_storage
    storage = get_storage()
    storage._internal.create_bucket(Bucket=storage.bucket)
    yield server
    server.stop()


@pytest.fixture(scope="session", autouse=True)
def redis_server(tmp_path_factory):
    import subprocess
    import time as _time

    import redis

    proc = subprocess.Popen(["redis-server", "--port", str(REDIS_PORT), "--bind", "127.0.0.1", "--save", "",
                             "--appendonly", "no", "--dir", str(tmp_path_factory.mktemp("redis"))],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    client = redis.Redis(port=REDIS_PORT)
    for _ in range(50):
        try:
            client.ping()
            break
        except redis.ConnectionError:
            _time.sleep(0.1)
    yield proc
    proc.terminate()
    proc.wait()


@pytest.fixture(autouse=True)
def clean_tables(s3_server, redis_server, tmp_path):
    from app.core.config import get_settings
    settings = get_settings()
    previous = settings.worker_tmp_dir
    settings.worker_tmp_dir = str(tmp_path / "worker-tmp")
    yield
    settings.worker_tmp_dir = previous
    from app.services.queue import get_redis
    get_redis().flushdb()
    from app.services.storage import get_storage
    get_storage().delete_prefix("")
    with get_engine().begin() as conn:
        conn.execute(text(
            "TRUNCATE audit_events, usage_ledger, processing_jobs, clips, clip_candidates, "
            "transcript_segments, media_assets, projects, subscriptions, profiles CASCADE"
        ))


def make_token(sub: uuid.UUID | str | None = None, *, email="usuario@example.com", exp_delta=3600,
               aud="authenticated", iss=ISSUER, secret=SECRET, role="authenticated", alg="HS256") -> str:
    claims = {"sub": str(sub or uuid.uuid4()), "email": email, "aud": aud, "iss": iss, "role": role,
              "exp": int(time.time()) + exp_delta, "iat": int(time.time())}
    return jwt.encode(claims, secret, algorithm=alg)


@pytest.fixture
def client():
    from app.main import app
    with TestClient(app) as c:
        yield c


class User:
    def __init__(self, email: str):
        self.id = uuid.uuid4()
        self.email = email
        self.headers = {"Authorization": f"Bearer {make_token(self.id, email=email)}"}


@pytest.fixture
def alice() -> User:
    return User("alice@example.com")


@pytest.fixture
def bob() -> User:
    return User("bob@example.com")


FALA = ("Olá, bem-vindos ao nosso podcast. Hoje vamos falar sobre inteligência artificial e vídeos curtos. "
        "A primeira dica é começar com uma pergunta forte. A segunda dica é manter o ritmo.")


@pytest.fixture(scope="session")
def whisper_ready():
    """Pula os testes de transcrição real se o modelo não puder ser carregado (ex.: sem rede na 1ª vez)."""
    from app.core.config import get_settings
    from app.transcription import TranscriptionError
    from app.transcription.faster_whisper_provider import load_model
    try:
        load_model(get_settings())
    except TranscriptionError as exc:
        pytest.skip(f"Modelo Whisper indisponível: {exc.detail}")


@pytest.fixture(scope="session")
def speech_video(tmp_path_factory):
    """Vídeo real com fala sintética em português (espeak-ng), usado só como dado de teste."""
    import shutil
    if not shutil.which("espeak-ng"):
        pytest.skip("espeak-ng não instalado")
    from tests.media import make_speech_video
    return make_speech_video(tmp_path_factory.mktemp("fala") / "fala.mp4", FALA)


from tests.media import FALA_LONGA  # noqa: E402


@pytest.fixture(scope="session")
def long_speech_video(tmp_path_factory):
    """~80 s de fala sintética em português (espeak-ng), usado só como dado de teste."""
    import shutil
    if not shutil.which("espeak-ng"):
        pytest.skip("espeak-ng não instalado")
    from tests.media import make_speech_video
    return make_speech_video(tmp_path_factory.mktemp("fala_longa") / "longa.mp4", FALA_LONGA, pad_to=90)
