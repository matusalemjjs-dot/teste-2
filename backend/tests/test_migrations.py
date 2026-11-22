from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect

from alembic import command
from app.db.session import get_engine
from app.models import Base
from tests.conftest import alembic_config

TABLES = {"profiles", "projects", "media_assets", "transcript_segments", "clip_candidates", "clips",
          "processing_jobs", "usage_ledger", "subscriptions", "audit_events"}


def test_todas_as_tabelas_existem():
    assert TABLES <= set(inspect(get_engine()).get_table_names())


def test_migracao_sincronizada_com_modelos():
    with get_engine().connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn, opts={"compare_type": True}), Base.metadata)
    assert diff == [], diff


def test_downgrade_e_upgrade():
    cfg = alembic_config()
    command.downgrade(cfg, "base")
    assert not (TABLES & set(inspect(get_engine()).get_table_names()))
    command.upgrade(cfg, "head")
    assert TABLES <= set(inspect(get_engine()).get_table_names())


def test_indices_principais():
    insp = inspect(get_engine())
    idx = {i["name"] for i in insp.get_indexes("projects")}
    assert {"ix_projects_owner_id", "ix_projects_status", "ix_projects_owner_created"} <= idx
