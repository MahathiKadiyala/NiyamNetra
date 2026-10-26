"""conftest.py"""
import os
import sys
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)   # passes the validator
os.environ.setdefault("DATABASE_URL", "sqlite:///./test.db")

SQLITE_URL = "sqlite:///:memory:"
# Set PG_TEST_URL to run the dual-engine suite. Without it those tests skip
# loudly rather than passing vacuously.
PG_URL = os.environ.get("PG_TEST_URL")


def _build(url):
    eng = create_engine(url, connect_args={"check_same_thread": False}
                        if url.startswith("sqlite") else {})
    if url.startswith("sqlite"):
        @event.listens_for(eng, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return eng


@pytest.fixture(params=["sqlite", "postgres"])
def engine(request):
    """Every schema-level test runs against both engines.

    The project claims SQLite in development and PostgreSQL in production. A
    suite that only exercises SQLite cannot see that RAISE(ABORT),
    AUTOINCREMENT and json_each are SQLite-only — which is how v1.x shipped a
    DDL block headed "works for Postgres" that did not.
    """
    if request.param == "postgres":
        if not PG_URL:
            pytest.skip("PG_TEST_URL not set — PostgreSQL half of the suite not run")
        eng = _build(PG_URL)
    else:
        eng = _build(SQLITE_URL)

    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.set_main_option("script_location", str(ini_path.parent / "alembic"))
    cfg.set_main_option("sqlalchemy.url", str(eng.url))
    command.upgrade(cfg, "head")     # NOT create_all — the constraints matter
    yield eng
    eng.dispose()


@pytest.fixture
def db(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.rollback()
    s.close()


# =========================================================================
# GLUE FIXTURES
#
# Everything below this line is not transcribed from 10_TESTING.md. The doc
# names these fixtures (load_fixture, client, the seeded_* rows, the token
# fixtures, the image fixtures) but never defines them, because they are the
# wiring between the verbatim tests above and the modules under test. They are
# synthesised here to the signatures the tests call them with, and to nothing
# more.
# =========================================================================
# __GLUE_MARKER__
