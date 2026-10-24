"""Tests for fail-closed production database initialization behavior.

Validates that:
- Production primary-database connection failure raises a clear startup error.
- A missing production database configuration or driver does not result in SQLite fallback.
- No fallback schema creation or seed operation occurs on a production failure path.
- Sensitive database connection information is not leaked in captured logs.
- Existing permitted development and test database behavior remains correct.
- The error handling works for the actual exception types used by this project.
"""
import logging
import socket
from unittest.mock import MagicMock, patch
import pytest
from sqlalchemy.exc import ArgumentError, NoSuchModuleError, OperationalError

from database import (
    Base,
    DatabaseInitializationError,
    create_engine_with_fallback,
    is_fallback_permitted,
    is_production_like_env,
    _sanitize_db_error,
)


POSTGRES_TEST_URL = (
    "postgresql+psycopg://nn_officer:P%40ssw0rd_Secret_999@aws-0-ap-south-1.pooler.supabase.com:5432/postgres"
)
UNENCODED_SECRET = "P@ssw0rd_Secret_999"


class TestEnvironmentClassification:
    """Verify conservative environment categorization for fallback decisions."""

    @pytest.mark.parametrize("env", ["dev", "development", "DEV", " DEVELOPMENT "])
    def test_development_permits_fallback(self, env: str):
        assert is_fallback_permitted(env) is True
        assert is_production_like_env(env) is False

    @pytest.mark.parametrize("env", ["prod", "production", "PROD", " PRODUCTION "])
    def test_production_prohibits_fallback(self, env: str):
        assert is_fallback_permitted(env) is False
        assert is_production_like_env(env) is True

    @pytest.mark.parametrize("env", ["test", "TEST"])
    def test_test_environment_permits_isolated_fallback(self, env: str):
        assert is_fallback_permitted(env) is True
        assert is_production_like_env(env) is False

    @pytest.mark.parametrize("env", ["staging", "preview", "qa", "uat", "unknown", "", None])
    def test_ambiguous_environments_treated_conservatively(self, env: str | None):
        # Ambiguous / unrecognized environments must fail closed (treated as production-like)
        assert is_fallback_permitted(env) is False
        assert is_production_like_env(env) is True


class TestProductionFailClosed:
    """Production and ambiguous environments must fail closed without SQLite fallback."""

    def test_production_postgres_connection_failure_raises_startup_error(self):
        """When PostgreSQL connection fails in production, raise DatabaseInitializationError."""
        mock_eng = MagicMock()
        mock_eng.connect.side_effect = OperationalError(
            "statement", {}, Exception("connection to server failed: Connection refused")
        )

        with patch("database.create_engine", return_value=mock_eng):
            with pytest.raises(DatabaseInitializationError) as exc_info:
                create_engine_with_fallback(url=POSTGRES_TEST_URL, env="prod")

        assert issubclass(DatabaseInitializationError, RuntimeError)
        assert "Failed to connect to primary database in environment 'prod'" in str(exc_info.value)
        assert "SQLite fallback is prohibited" in str(exc_info.value)

    @pytest.mark.parametrize(
        "driver_exc",
        [
            NoSuchModuleError("Can't load plugin: sqlalchemy.dialects:postgresql.psycopg"),
            ModuleNotFoundError("No module named 'psycopg'"),
            ImportError("cannot import name 'psycopg'"),
        ],
    )
    def test_production_missing_driver_raises_error(self, driver_exc: Exception):
        """Missing database driver in production raises error without creating SQLite fallback."""
        with patch("database.create_engine", side_effect=driver_exc):
            with pytest.raises(DatabaseInitializationError) as exc_info:
                create_engine_with_fallback(url=POSTGRES_TEST_URL, env="prod")

        assert "SQLite fallback is prohibited" in str(exc_info.value)

    @pytest.mark.parametrize("missing_url", ["", "   ", None])
    def test_production_missing_database_url_raises_error(self, missing_url: str | None):
        """Missing DATABASE_URL in production raises DatabaseInitializationError."""
        with pytest.raises(DatabaseInitializationError) as exc_info:
            create_engine_with_fallback(url=missing_url, env="prod")

        assert "DATABASE_URL is not configured" in str(exc_info.value)
        assert "Ephemeral SQLite fallback is prohibited" in str(exc_info.value)

    @pytest.mark.parametrize("sqlite_url", ["sqlite:///niyamnetra.db", "sqlite:///:memory:"])
    def test_production_rejects_sqlite_url(self, sqlite_url: str):
        """Configuring SQLite URL in production is prohibited and fails closed."""
        with pytest.raises(DatabaseInitializationError) as exc_info:
            create_engine_with_fallback(url=sqlite_url, env="prod")

        assert "SQLite DATABASE_URL is prohibited in environment 'prod'" in str(exc_info.value)
        assert "Supabase/PostgreSQL is required" in str(exc_info.value)

    @pytest.mark.parametrize("ambiguous_env", ["staging", "preview", "unknown", ""])
    def test_ambiguous_environment_postgres_failure_fails_closed(self, ambiguous_env: str):
        """Unknown or ambiguous deployment environments fail closed on primary DB failure."""
        mock_eng = MagicMock()
        mock_eng.connect.side_effect = OperationalError(
            "statement", {}, Exception("network unreachable")
        )

        with patch("database.create_engine", return_value=mock_eng):
            with pytest.raises(DatabaseInitializationError) as exc_info:
                create_engine_with_fallback(url=POSTGRES_TEST_URL, env=ambiguous_env)

        assert "SQLite fallback is prohibited" in str(exc_info.value)

    def test_production_no_schema_creation_or_seeding_on_failure(self):
        """No schema creation (Base.metadata.create_all) or SQLite file creation occurs on failure."""
        mock_eng = MagicMock()
        mock_eng.connect.side_effect = OperationalError("statement", {}, Exception("timed out"))

        with patch("database.create_engine", return_value=mock_eng):
            with patch.object(Base.metadata, "create_all") as mock_create_all:
                with pytest.raises(DatabaseInitializationError):
                    create_engine_with_fallback(url=POSTGRES_TEST_URL, env="prod")

                mock_create_all.assert_not_called()


class TestCredentialProtectionInLogs:
    """Ensure database passwords and credentials are never leaked in logs or error messages."""

    def test_credentials_scrubbed_from_error_and_logs_in_production(self, caplog):
        """Verify raw passwords and URL credentials are redacted from logs and exceptions."""
        caplog.set_level(logging.DEBUG)

        mock_eng = MagicMock()
        raw_error_msg = (
            f"password authentication failed for user 'nn_officer' "
            f"connecting to {POSTGRES_TEST_URL}"
        )
        mock_eng.connect.side_effect = OperationalError("statement", {}, Exception(raw_error_msg))

        with patch("database.create_engine", return_value=mock_eng):
            with pytest.raises(DatabaseInitializationError) as exc_info:
                create_engine_with_fallback(url=POSTGRES_TEST_URL, env="prod")

        err_str = str(exc_info.value)
        log_str = caplog.text

        # The raw password must NEVER appear anywhere
        assert UNENCODED_SECRET not in err_str
        assert UNENCODED_SECRET not in log_str
        assert "P%40ssw0rd_Secret_999" not in err_str
        assert "P%40ssw0rd_Secret_999" not in log_str

        # Redaction markers must be present
        assert "[REDACTED]" in err_str or "REDACTED" in err_str
        assert "[REDACTED]" in log_str or "REDACTED" in log_str

    def test_credentials_scrubbed_from_logs_in_development_fallback(self, caplog, tmp_path):
        """In development fallback, logs must still redact sensitive credentials."""
        caplog.set_level(logging.DEBUG)

        mock_eng = MagicMock()
        raw_error_msg = f"failed to connect with password={UNENCODED_SECRET}"
        mock_eng.connect.side_effect = OperationalError("statement", {}, Exception(raw_error_msg))

        # Use temporary directory for fallback db to avoid mutating project directory
        temp_sqlite = tmp_path / "dev_fallback.db"
        with patch("database.create_engine", side_effect=[mock_eng, MagicMock()]):
            with patch("database.Path") as mock_path_cls:
                mock_path_cls.return_value.resolve.return_value.parent = tmp_path
                with patch.object(Base.metadata, "create_all"):
                    eng, is_sqlite = create_engine_with_fallback(
                        url=POSTGRES_TEST_URL, env="dev"
                    )

        assert is_sqlite is True
        assert UNENCODED_SECRET not in caplog.text
        assert "[REDACTED]" in caplog.text

    def test_sanitize_db_error_handles_various_formats(self):
        """Direct unit test on _sanitize_db_error helper."""
        # 1. URL basic auth
        raw1 = "Failed to connect to postgresql+psycopg://user:super_secret_pw@db.render.com:5432/app"
        clean1 = _sanitize_db_error(raw1)
        assert "super_secret_pw" not in clean1
        assert "://user:[REDACTED]@" in clean1

        # 2. Key-value password
        raw2 = "libpq error: password=my_plain_text_secret host=localhost"
        clean2 = _sanitize_db_error(raw2)
        assert "my_plain_text_secret" not in clean2
        assert "password=[REDACTED]" in clean2

        # 3. Encoded password in configured URL
        url3 = "postgresql+psycopg://app:Complex%21Pass%231@db.internal:5432/main"
        raw3 = "Connection failure with Complex!Pass#1 and Complex%21Pass%231"
        clean3 = _sanitize_db_error(raw3, url=url3)
        assert "Complex!Pass#1" not in clean3
        assert "Complex%21Pass%231" not in clean3


class TestPreservedPermittedWorkflows:
    """Verify development and test workflows remain completely intact."""

    def test_development_fallback_permitted_when_postgres_fails(self, tmp_path):
        """Development environment successfully falls back to local SQLite on Postgres failure."""
        mock_eng = MagicMock()
        mock_eng.connect.side_effect = OperationalError(
            "statement", {}, Exception("no local postgres running")
        )

        with patch("database.create_engine", side_effect=[mock_eng, MagicMock()]):
            with patch.object(Base.metadata, "create_all") as mock_create_all:
                eng, is_sqlite = create_engine_with_fallback(
                    url=POSTGRES_TEST_URL, env="dev"
                )

        assert is_sqlite is True
        mock_create_all.assert_called_once()

    def test_development_direct_sqlite_permitted(self):
        """Development environment directly connects to SQLite without attempting Postgres."""
        sqlite_url = "sqlite:///:memory:"
        eng, is_sqlite = create_engine_with_fallback(url=sqlite_url, env="dev")
        assert is_sqlite is True
        assert eng.url.database == ":memory:"

    def test_test_environment_preserves_isolated_sqlite(self):
        """Test environment preserves isolated in-memory SQLite workflow."""
        sqlite_url = "sqlite:///:memory:"
        eng, is_sqlite = create_engine_with_fallback(url=sqlite_url, env="test")
        assert is_sqlite is True
        assert eng.url.database == ":memory:"

    def test_test_environment_postgres_failure_permits_fallback(self):
        """Test environment permits falling back to isolated SQLite when configured Postgres is down."""
        mock_eng = MagicMock()
        mock_eng.connect.side_effect = OperationalError("statement", {}, Exception("no pg"))

        with patch("database.create_engine", side_effect=[mock_eng, MagicMock()]):
            with patch.object(Base.metadata, "create_all") as mock_create_all:
                eng, is_sqlite = create_engine_with_fallback(
                    url=POSTGRES_TEST_URL, env="test"
                )

        assert is_sqlite is True
        mock_create_all.assert_called_once()

    def test_production_successful_postgres_connection(self):
        """When primary PostgreSQL succeeds in production, it is returned with is_sqlite=False."""
        mock_eng = MagicMock()
        mock_conn = MagicMock()
        mock_eng.connect.return_value.__enter__.return_value = mock_conn

        with patch("database.create_engine", return_value=mock_eng):
            eng, is_sqlite = create_engine_with_fallback(url=POSTGRES_TEST_URL, env="prod")

        assert is_sqlite is False
        assert eng is mock_eng
        mock_conn.execute.assert_called_once()


class TestProjectExceptionTypes:
    """Ensure error handling covers the actual exception types raised in this project."""

    @pytest.mark.parametrize(
        "exc_instance",
        [
            OperationalError("statement", {}, Exception("could not translate host name")),
            NoSuchModuleError("psycopg"),
            ArgumentError("Could not parse rfc1738 URL from string"),
            ModuleNotFoundError("No module named 'psycopg'"),
            ImportError("cannot import name 'psycopg'"),
            TimeoutError("Connection timed out after 5s"),
            socket.gaierror(11001, "getaddrinfo failed"),
            ValueError("Bad database URL"),
        ],
    )
    def test_actual_exception_types_fail_closed_in_production(self, exc_instance: Exception):
        """All network, dialect, configuration, and driver exceptions fail closed in production."""
        mock_eng = MagicMock()
        mock_eng.connect.side_effect = exc_instance

        # Test both when create_engine raises and when connect raises
        with patch("database.create_engine", side_effect=[exc_instance]):
            with pytest.raises(DatabaseInitializationError):
                create_engine_with_fallback(url=POSTGRES_TEST_URL, env="prod")

        with patch("database.create_engine", return_value=mock_eng):
            with pytest.raises(DatabaseInitializationError):
                create_engine_with_fallback(url=POSTGRES_TEST_URL, env="prod")
