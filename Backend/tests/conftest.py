"""conftest.py — Test configuration, database isolation, and fixtures."""
import atexit
import os
import shutil
import sys
import tempfile
import uuid
from pathlib import Path
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

BACKEND_DIR = Path(__file__).resolve().parent.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

# =========================================================================
# TEST DATABASE ISOLATION GUARDRAILS (Phase 2, Module 9)
#
# 1. Neutralize any inherited DATABASE_URL from parent environment or shell
#    to guarantee ordinary test execution can NEVER connect to production.
# 2. Allocate an isolated, disposable local SQLite database in a unique
#    temporary directory outside tracked repository directories.
# 3. Clean up the disposable database safely upon test session completion.
# =========================================================================

# Save and pop any inherited database URL to prevent accidental use
_INHERITED_DATABASE_URL = os.environ.pop("DATABASE_URL", None)

_TEST_RUN_ID = uuid.uuid4().hex[:8]
_TEST_TMP_DIR = tempfile.mkdtemp(prefix=f"niyamnetra_test_db_{_TEST_RUN_ID}_")
_DISPOSABLE_DB_FILE = Path(_TEST_TMP_DIR) / f"test_{_TEST_RUN_ID}.db"
_DISPOSABLE_DB_URL = f"sqlite:///{_DISPOSABLE_DB_FILE}"

# Force safe test-only environment
os.environ["ENV"] = "test"
os.environ["DATABASE_URL"] = _DISPOSABLE_DB_URL
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)

# Clear cached settings so get_settings() reflects the isolated test configuration
try:
    if "config" in sys.modules:
        import config
        if hasattr(config, "get_settings"):
            config.get_settings.cache_clear()
        config.settings = config.get_settings()
except Exception:
    pass


def cleanup_test_database(db_file: Path | str, tmp_dir: Path | str | None = None):
    """Safely remove a test database file and its enclosing temporary directory.

    Guards against deleting arbitrary directories or files:
    - Only deletes db_file if it is within the OS temp directory and its parent or name matches test prefixes.
    - Only deletes tmp_dir if it is within the OS temp directory and its name starts with niyamnetra_test_db_.
    """
    db_path = Path(db_file).resolve()
    temp_root = Path(tempfile.gettempdir()).resolve()

    try:
        # Safety guard: only delete files inside OS temp dir that reside in a niyamnetra test directory or match test naming
        is_in_temp = temp_root in db_path.parents or db_path.is_relative_to(temp_root)
        is_test_named = any(part.startswith("niyamnetra_test_db_") for part in db_path.parts) or db_path.name.startswith("test_") or db_path.name.startswith("disposable")
        if is_in_temp and is_test_named:
            if db_path.exists():
                db_path.unlink(missing_ok=True)
            for ext in ("-wal", "-shm"):
                extra = Path(str(db_path) + ext)
                if extra.exists():
                    extra.unlink(missing_ok=True)

        if tmp_dir:
            tmp_path = Path(tmp_dir).resolve()
            # Safety guard: only remove directory if inside OS temp dir and named niyamnetra_test_db_*
            if (temp_root in tmp_path.parents or tmp_path.is_relative_to(temp_root)) and tmp_path.name.startswith("niyamnetra_test_db_"):
                shutil.rmtree(tmp_path, ignore_errors=True)
    except Exception:
        pass


def _cleanup_disposable_test_db():
    """Safely remove only the current test session's temporary database and directory."""
    try:
        if "database" in sys.modules:
            import database
            if hasattr(database, "engine") and database.engine:
                database.engine.dispose()
    except Exception:
        pass

    cleanup_test_database(_DISPOSABLE_DB_FILE, _TEST_TMP_DIR)


atexit.register(_cleanup_disposable_test_db)


def pytest_sessionfinish(session, exitstatus):
    """Pytest hook to clean up the disposable test database on test completion."""
    _cleanup_disposable_test_db()


SQLITE_URL = "sqlite:///:memory:"
# Set PG_TEST_URL to run the dual-engine suite. Without it those tests skip
# loudly rather than passing vacuously.
PG_URL = os.environ.get("PG_TEST_URL")


def check_postgres_test_safety(pg_url: str | None) -> tuple[bool, str]:
    """Validate whether PG_TEST_URL is configured and points to a safe test target."""
    if not pg_url:
        return False, "PG_TEST_URL not set — PostgreSQL half of the suite not run"
    # Safety guard: ensure PG_URL is an explicit test database
    if not any(token in pg_url.lower() for token in ("test", "localhost", "127.0.0.1")):
        if os.environ.get("ALLOW_REMOTE_PG_TEST", "").lower() != "true":
            return False, "PG_TEST_URL is not recognized as a local/test database. Set ALLOW_REMOTE_PG_TEST=true to opt in."
    return True, ""


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
        safe, reason = check_postgres_test_safety(PG_URL)
        if not safe:
            pytest.skip(reason)
        eng = _build(PG_URL)
    else:
        eng = _build(SQLITE_URL)

    from pathlib import Path
    from alembic import command
    from alembic.config import Config
    ini_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    cfg = Config(str(ini_path))
    cfg.attributes["configure_logger"] = False
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


@pytest.fixture
def disposable_test_db():
    """Provides details about the isolated disposable test database."""
    return {
        "url": _DISPOSABLE_DB_URL,
        "path": _DISPOSABLE_DB_FILE,
        "tmp_dir": _TEST_TMP_DIR,
        "inherited_url_neutralized": _INHERITED_DATABASE_URL is not None,
    }


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
