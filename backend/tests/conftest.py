import os
import time
import uuid

os.environ.setdefault("DATABASE_URL", "postgresql+psycopg://autoclip:autoclip@localhost:5432/autoclip_test")
os.environ["ENVIRONMENT"] = "test"
os.environ["SUPABASE_URL"] = "https://exemplo-teste.supabase.co"
os.environ["SUPABASE_JWT_SECRET"] = "segredo-de-teste-apenas-local-com-32-bytes-ou-mais"
os.environ["LOG_LEVEL"] = "WARNING"

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


@pytest.fixture(autouse=True)
def clean_tables():
    yield
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
