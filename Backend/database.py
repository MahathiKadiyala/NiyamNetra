"""Engine, session factory, and the per-connection SQLite pragmas."""
from __future__ import annotations

from collections.abc import Generator
import logging
from pathlib import Path
import re
from typing import Any
import urllib.parse

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from config import settings

logger = logging.getLogger("niyamnetra.database")


class DatabaseInitializationError(RuntimeError):
    """Raised when database initialization fails in an environment where fallback is prohibited."""
    pass


class Base(DeclarativeBase):
    pass


def _apply_sqlite_pragmas(eng: Any) -> None:
    try:
        @event.listens_for(eng, "connect")
        def _sqlite_pragmas(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.close()
    except Exception:
        pass


def is_fallback_permitted(env: str | None) -> bool:
    """Return True if SQLite fallback from a failed primary database is explicitly permitted.

    Only local development environments ('dev', 'development') and test environments
    ('test') are permitted to fall back to a local SQLite database when the primary database
    is unavailable. All other environments (production, staging, preview, unknown, empty)
    must fail closed.
    """
    env_name = str(env or "").strip().lower()
    return env_name in ("dev", "development", "test")


def is_production_like_env(env: str | None) -> bool:
    """Return True if the environment should be treated conservatively as production-like.

    'prod', 'production', and any unrecognized or ambiguous environments (e.g. 'staging',
    'preview', 'qa', None, '') are treated as production-like and prohibited from running
    ephemeral SQLite.
    """
    env_name = str(env or "").strip().lower()
    return env_name in ("prod", "production") or env_name not in ("dev", "development", "test")


def _sanitize_db_error(exc: Exception | str, url: str | None = None) -> str:
    """Scrub passwords, credentials, and tokens from exception strings and connection URLs."""
    raw = str(exc)

    # 1. Scrub password directly from the configured URL if present
    if url:
        try:
            parsed = urllib.parse.urlsplit(url)
            if parsed.password:
                raw = raw.replace(parsed.password, "[REDACTED]")
                raw = raw.replace(urllib.parse.unquote(parsed.password), "[REDACTED]")
        except Exception:
            pass

    # 2. Scrub basic auth in any URLs (e.g. postgresql://user:pass@host:5432/db)
    raw = re.sub(r"://([^:]+):([^@\s]+)@", r"://\1:[REDACTED]@", raw)

    # 3. Scrub key=value style passwords (e.g. password=secret)
    raw = re.sub(r"(?i)(password\s*=\s*)(['\"]?)[^'\s\"]+\2", r"\1[REDACTED]", raw)

    # 4. Integrate project-wide sanitizer if available
    try:
        from ocr.security import sanitize_sensitive_text
        sanitized = sanitize_sensitive_text(raw)
        if sanitized:
            raw = sanitized
    except Exception:
        pass

    return raw


_CONFIG_DEFAULT = object()


def _create_engine_with_fallback(
    url: Any = _CONFIG_DEFAULT,
    env: Any = _CONFIG_DEFAULT,
):
    if url is _CONFIG_DEFAULT:
        url = getattr(settings, "DATABASE_URL", None)
    if env is _CONFIG_DEFAULT:
        env = getattr(settings, "ENV", "dev")

    env_name = str(env or "").strip().lower()
    url_str = str(url or "").strip()
    is_sqlite = url_str.startswith("sqlite")

    # Guard 1: Missing URL must fail closed in all environments
    if not url_str:
        logger.critical(
            "DATABASE_URL is not configured (environment: '%s'). Failing closed.",
            env_name,
        )
        raise DatabaseInitializationError(
            f"DATABASE_URL is not configured (environment: '{env_name}'). "
            "Ephemeral SQLite fallback is prohibited."
        )

    # Guard 2: Production / production-like environments MUST NOT run against SQLite
    if is_production_like_env(env_name) and is_sqlite:
        logger.critical(
            "SQLite URL '%s' is prohibited in production-like environment '%s'. Failing closed.",
            _sanitize_db_error(url_str, url=url_str),
            env_name,
        )
        raise DatabaseInitializationError(
            f"SQLite DATABASE_URL is prohibited in environment '{env_name}'. "
            "Supabase/PostgreSQL is required."
        )

    if not is_sqlite:
        test_eng = None
        try:
            test_eng = create_engine(
                url_str,
                echo=False,
                future=True,
                pool_pre_ping=True,
                connect_args={"connect_timeout": 5},
            )
            with test_eng.connect() as conn:
                conn.execute(text("SELECT 1"))
            logger.info("Connected to primary PostgreSQL database.")
            return test_eng, False
        except Exception as exc:
            if test_eng is not None:
                try:
                    test_eng.dispose()
                except Exception:
                    pass

            sanitized_exc = _sanitize_db_error(exc, url=url_str)
            if not is_fallback_permitted(env_name):
                logger.critical(
                    "Primary database connection failed in fail-closed environment '%s': %s. "
                    "Ephemeral SQLite fallback is prohibited; failing closed.",
                    env_name,
                    sanitized_exc,
                )
                raise DatabaseInitializationError(
                    f"Failed to connect to primary database in environment '{env_name}': {sanitized_exc}. "
                    "SQLite fallback is prohibited."
                ) from None

            logger.warning(
                "Primary database connection failed (%s): %s. Using local SQLite fallback.",
                env_name,
                sanitized_exc,
            )
            sqlite_db = Path(__file__).resolve().parent / "niyamnetra.db"
            fallback_url = f"sqlite:///{sqlite_db}"
            fb_eng = create_engine(
                fallback_url,
                echo=False,
                future=True,
                connect_args={"check_same_thread": False},
            )
            _apply_sqlite_pragmas(fb_eng)
            import models  # noqa: F401
            Base.metadata.create_all(bind=fb_eng)
            return fb_eng, True
    else:
        # Direct SQLite connection (permitted in dev and test)
        eng = create_engine(
            url_str,
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
        )
        _apply_sqlite_pragmas(eng)
        return eng, True


create_engine_with_fallback = _create_engine_with_fallback
engine, _is_using_sqlite = _create_engine_with_fallback()
SessionLocal = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

