"""test_database_isolation.py — Regression tests for Phase 2, Module 9: Harden Test Database Isolation.

Verifies that:
1. Inherited DATABASE_URL from shell/parent process cannot route tests to production.
2. Ordinary test runtime uses an isolated, disposable SQLite database in a temp directory.
3. The test runtime is isolated from production database configuration.
4. PostgreSQL tests cannot run without explicit test-only opt-in.
5. Intentional tests of database initialization can supply controlled values via parameters/monkeypatching.
6. Test database cleanup safely removes only the disposable test database and preserves unrelated files.
7. Production fail-closed behavior from Module 1 remains completely intact.
"""
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
import pytest
from sqlalchemy import create_engine, text

from config import get_settings, settings
from database import (
    DatabaseInitializationError,
    create_engine_with_fallback,
    is_fallback_permitted,
    is_production_like_env,
)


def test_inherited_database_url_not_used_by_ordinary_tests():
    """Verify that an inherited DATABASE_URL environment variable is neutralized by conftest.py."""
    # Run a subprocess with a simulated external/production DATABASE_URL in its environment
    backend_dir = Path(__file__).resolve().parent.parent
    sub_env = os.environ.copy()
    sub_env["DATABASE_URL"] = "postgresql+psycopg://fake_officer:fake_pass@fake.pooler.supabase.com:5432/postgres"
    sub_env["ENV"] = "prod"
    sub_env["PYTHONPATH"] = str(backend_dir)

    code = (
        "import os\n"
        "from tests.conftest import _DISPOSABLE_DB_URL\n"
        "from config import get_settings\n"
        "s = get_settings()\n"
        "assert s.DATABASE_URL == _DISPOSABLE_DB_URL, f'DATABASE_URL was not neutralized: {s.DATABASE_URL}'\n"
        "assert s.DATABASE_URL.startswith('sqlite:///'), f'Expected SQLite test URL: {s.DATABASE_URL}'\n"
        "assert 'supabase' not in s.DATABASE_URL.lower()\n"
        "print('ISOLATION_CONFIRMED')\n"
    )

    res = subprocess.run(
        [sys.executable, "-c", code],
        cwd=backend_dir,
        env=sub_env,
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0, f"Subprocess failed with code {res.returncode}:\n{res.stdout}\n{res.stderr}"
    assert "ISOLATION_CONFIRMED" in res.stdout


def test_runtime_uses_disposable_test_database(disposable_test_db):
    """Verify that the test runtime uses a disposable SQLite database in a temporary directory."""
    url = disposable_test_db["url"]
    path = disposable_test_db["path"]
    tmp_dir = disposable_test_db["tmp_dir"]

    # Must start with sqlite:///
    assert url.startswith("sqlite:///")

    # Must be located in the OS temporary directory
    temp_root = Path(tempfile.gettempdir()).resolve()
    assert temp_root in Path(tmp_dir).resolve().parents or Path(tmp_dir).resolve().is_relative_to(temp_root)

    # Must NOT be in the repository directory
    backend_dir = Path(__file__).resolve().parent.parent
    repo_root = backend_dir.parent
    assert not Path(path).resolve().is_relative_to(backend_dir)
    assert not Path(path).resolve().is_relative_to(repo_root)

    # Connect to the disposable database and verify functionality
    eng = create_engine(url)
    with eng.connect() as conn:
        res = conn.execute(text("SELECT 1")).scalar()
        assert res == 1
    eng.dispose()


def test_test_database_isolated_from_production():
    """Verify that current settings are in test mode and cannot access production."""
    s = get_settings()
    assert s.ENV == "test"
    assert is_production_like_env(s.ENV) is False
    assert is_fallback_permitted(s.ENV) is True
    assert s.is_sqlite is True
    assert not s.DATABASE_URL.startswith("postgresql")
    assert not any(token in s.DATABASE_URL.lower() for token in ("supabase", "onrender", "aws"))


def test_postgres_tests_cannot_run_without_explicit_opt_in(monkeypatch):
    """Verify that PostgreSQL schema tests cannot run without explicit test-only opt-in."""
    from tests.conftest import check_postgres_test_safety

    # 1. When PG_TEST_URL is unset -> safe=False, skips
    safe, reason = check_postgres_test_safety(None)
    assert safe is False
    assert "PG_TEST_URL not set" in reason

    # 2. When PG_TEST_URL is set to an unrecognized remote URL without opt-in -> safe=False, skips
    monkeypatch.delenv("ALLOW_REMOTE_PG_TEST", raising=False)
    safe, reason = check_postgres_test_safety("postgresql+psycopg://officer:secret@db.production.net:5432/niyamnetra")
    assert safe is False
    assert "ALLOW_REMOTE_PG_TEST=true" in reason

    # 3. When ALLOW_REMOTE_PG_TEST=true is explicitly set -> safe=True
    monkeypatch.setenv("ALLOW_REMOTE_PG_TEST", "true")
    safe, reason = check_postgres_test_safety("postgresql+psycopg://officer:secret@db.production.net:5432/niyamnetra")
    assert safe is True
    assert reason == ""

    # 4. When PG_TEST_URL is a local/test database -> safe=True
    monkeypatch.delenv("ALLOW_REMOTE_PG_TEST", raising=False)
    safe, reason = check_postgres_test_safety("postgresql+psycopg://postgres:postgres@localhost:5432/test_niyamnetra")
    assert safe is True
    assert reason == ""


def test_intentional_database_initialization_tests_supported(monkeypatch, tmp_path):
    """Verify that intentional tests of database selection can pass explicit URLs or monkeypatch."""
    # Explicit parameters to create_engine_with_fallback
    sqlite_custom = f"sqlite:///{tmp_path / 'custom.db'}"
    eng, is_sqlite = create_engine_with_fallback(url=sqlite_custom, env="test")
    assert is_sqlite is True
    assert str(eng.url) == sqlite_custom
    eng.dispose()

    # Monkeypatching within test scope
    test_url = f"sqlite:///{tmp_path / 'monkeypatched.db'}"
    monkeypatch.setenv("DATABASE_URL", test_url)
    assert os.environ["DATABASE_URL"] == test_url


def test_cleanup_preserves_unrelated_files_and_databases(tmp_path):
    """Verify that test database cleanup safely preserves unrelated files and databases."""
    from tests.conftest import cleanup_test_database

    # Create a simulated disposable test directory and db inside OS temp
    test_run_dir = Path(tempfile.mkdtemp(prefix="niyamnetra_test_db_sample_"))
    disposable_db = test_run_dir / "disposable.db"
    disposable_db.write_text("sqlite disposable test content")

    # Create an unrelated file and database in another directory
    unrelated_dir = tmp_path / "user_data"
    unrelated_dir.mkdir(parents=True, exist_ok=True)
    unrelated_file = unrelated_dir / "user_notes.txt"
    unrelated_file.write_text("important user notes")

    unrelated_db = unrelated_dir / "production_replica.db"
    unrelated_db.write_text("sqlite format simulated")

    # Run cleanup on the disposable target
    cleanup_test_database(disposable_db, test_run_dir)

    # Disposable database should be cleaned up
    assert not disposable_db.exists()
    assert not test_run_dir.exists()

    # Unrelated files and databases must remain untouched
    assert unrelated_file.exists()
    assert unrelated_file.read_text() == "important user notes"
    assert unrelated_db.exists()
    assert unrelated_db.read_text() == "sqlite format simulated"

    # Attempting to clean up an arbitrary non-test directory should be refused by the guard
    arbitrary_dir = tmp_path / "important_project_dir"
    arbitrary_dir.mkdir(parents=True, exist_ok=True)
    arbitrary_file = arbitrary_dir / "critical.py"
    arbitrary_file.write_text("# critical code")
    cleanup_test_database(arbitrary_file, arbitrary_dir)
    assert arbitrary_dir.exists()
    assert arbitrary_file.exists()


def test_module1_fail_closed_invariants_preserved():
    """Verify that production fail-closed behavior from Module 1 remains intact."""
    # Production with missing DATABASE_URL raises DatabaseInitializationError
    with pytest.raises(DatabaseInitializationError) as exc_missing:
        create_engine_with_fallback(url="", env="prod")
    assert "DATABASE_URL is not configured" in str(exc_missing.value)

    # Production with SQLite URL raises DatabaseInitializationError
    with pytest.raises(DatabaseInitializationError) as exc_sqlite:
        create_engine_with_fallback(url="sqlite:///prod.db", env="prod")
    assert "SQLite DATABASE_URL is prohibited in environment 'prod'" in str(exc_sqlite.value)
