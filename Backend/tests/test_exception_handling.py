"""tests/test_exception_handling.py — Verify production exception information disclosure protections."""
import logging
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from config import settings
from main import app
from ocr.security import sanitize_sensitive_text


@pytest.fixture
def test_client():
    # Register test routes on app to trigger various exceptions
    if not any(getattr(route, "path", None) == "/test-route-unhandled-exception" for route in app.routes):
        @app.get("/test-route-unhandled-exception")
        def route_unhandled():
            raise RuntimeError("Database connection string postgresql://user:secretpass@db.internal:5432/db")

    if not any(getattr(route, "path", None) == "/test-route-synthetic-secret" for route in app.routes):
        @app.get("/test-route-synthetic-secret")
        def route_synthetic_secret():
            raise RuntimeError(
                "Failed connection: postgresql://postgres:FakeDbPassword998877@db.supabase.co:5432/postgres "
                "with Bearer fake_bearer_token_1234567890 and api_key=FakeApiKey9876543210"
            )

    if not any(getattr(route, "path", None) == "/test-route-encoded-secret" for route in app.routes):
        @app.get("/test-route-encoded-secret")
        def route_encoded_secret():
            raise RuntimeError(
                "Connection failed: postgresql://user%40domain:p%40ssw%3Ard_secure@db.internal:5432/main "
                "and cache error redis://:RedisSecretToken999@cache.internal:6379/0"
            )

    if not any(getattr(route, "path", None) == "/test-route-http-exception" for route in app.routes):
        @app.get("/test-route-http-exception")
        def route_http():
            raise HTTPException(status_code=403, detail="Standard forbidden access")

    if not any(getattr(route, "path", None) == "/test-route-value-error" for route in app.routes):
        @app.get("/test-route-value-error")
        def route_value_error():
            raise ValueError("Invalid parameter value for domain")

    return TestClient(app, raise_server_exceptions=False)


@pytest.mark.parametrize("env_val", ["prod", "production", "PROD", "staging", "cloud_prod", "unknown_env"])
def test_production_and_unknown_environments_sanitized(test_client, monkeypatch, caplog, env_val):
    """Any production, non-dev, or unknown environment returns generic 500 without traceback or internal details."""
    monkeypatch.setattr(settings, "ENV", env_val)
    with caplog.at_level(logging.ERROR, logger="niyamnetra.error"):
        resp = test_client.get("/test-route-unhandled-exception")

    assert resp.status_code == 500
    data = resp.json()

    # Exact sanitized response
    assert data == {"detail": "Internal server error"}
    assert "traceback" not in data

    # Verify sensitive text is absent from the entire client response body
    assert "secretpass" not in resp.text
    assert "postgresql://" not in resp.text
    assert "RuntimeError" not in resp.text
    assert "Traceback" not in resp.text


def test_server_side_logging_sanitizes_credentials(test_client, monkeypatch, caplog):
    """Server-side logs capture tracebacks, but scrub synthetic credentials (passwords, tokens, keys)."""
    monkeypatch.setattr(settings, "ENV", "prod")
    with caplog.at_level(logging.ERROR, logger="niyamnetra.error"):
        resp = test_client.get("/test-route-synthetic-secret")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}

    # Find the log record
    matching_records = [
        r for r in caplog.records
        if "Unhandled exception processing GET /test-route-synthetic-secret" in r.message
    ]
    assert len(matching_records) >= 1
    log_text = matching_records[0].message

    # The stack trace must be present for operator diagnostics
    assert "Traceback (most recent call last)" in log_text

    # Synthetic credentials MUST BE REDACTED and never exposed in server logs
    assert "FakeDbPassword998877" not in log_text
    assert "fake_bearer_token_1234567890" not in log_text
    assert "FakeApiKey9876543210" not in log_text
    assert "[REDACTED]" in log_text


def test_server_side_logging_sanitizes_encoded_and_redis_uris(test_client, monkeypatch, caplog):
    """Encoded database passwords and username-optional URI passwords are redacted in server logs."""
    monkeypatch.setattr(settings, "ENV", "prod")
    with caplog.at_level(logging.ERROR, logger="niyamnetra.error"):
        resp = test_client.get("/test-route-encoded-secret")

    assert resp.status_code == 500
    assert resp.json() == {"detail": "Internal server error"}

    matching_records = [
        r for r in caplog.records
        if "Unhandled exception processing GET /test-route-encoded-secret" in r.message
    ]
    assert len(matching_records) >= 1
    log_text = matching_records[0].message

    assert "p%40ssw%3Ard_secure" not in log_text
    assert "RedisSecretToken999" not in log_text
    assert "user%40domain:[REDACTED]@db.internal" in log_text
    assert "redis://:[REDACTED]@cache.internal" in log_text


def test_sanitizer_preserves_traceback_frames_and_line_numbers():
    """Sanitization preserves file paths, line numbers, and function names while redacting secrets."""
    sample_tb = (
        'Traceback (most recent call last):\n'
        '  File "/app/backend/routers/scans.py", line 42, in process_scan\n'
        '    execute_query("postgresql://postgres:MySecretPass123@db.prod:5432/main")\n'
        '  File "/app/backend/database.py", line 18, in execute_query\n'
        '    raise ConnectionError("Auth failed with token Bearer secret_jwt_token_123456")\n'
        'ConnectionError: Auth failed with token Bearer secret_jwt_token_123456\n'
    )
    sanitized = sanitize_sensitive_text(sample_tb)

    # Frame structure and lines are preserved
    assert 'File "/app/backend/routers/scans.py", line 42, in process_scan' in sanitized
    assert 'File "/app/backend/database.py", line 18, in execute_query' in sanitized
    assert 'ConnectionError' in sanitized

    # Credentials are sanitized
    assert "MySecretPass123" not in sanitized
    assert "secret_jwt_token_123456" not in sanitized
    assert "[REDACTED]" in sanitized


def test_development_exception_diagnostics_preserved(test_client, monkeypatch, caplog):
    """In non-prod (ENV='dev' or 'test'), detailed diagnostics and traceback are preserved."""
    monkeypatch.setattr(settings, "ENV", "dev")
    with caplog.at_level(logging.ERROR, logger="niyamnetra.error"):
        resp = test_client.get("/test-route-unhandled-exception")

    assert resp.status_code == 500
    data = resp.json()

    assert "detail" in data
    assert "Database connection string" in data["detail"]
    assert "traceback" in data
    assert "Traceback (most recent call last)" in data["traceback"]

    # Verify server-side logging also occurred
    assert any("Unhandled exception processing GET /test-route-unhandled-exception" in record.message for record in caplog.records)


def test_http_exceptions_unaffected(test_client):
    """Standard HTTPExceptions preserve their status codes and details."""
    resp = test_client.get("/test-route-http-exception")
    assert resp.status_code == 403
    assert resp.json() == {"detail": "Standard forbidden access"}


def test_value_error_domain_validation_unaffected(test_client):
    """ValueError handler continues to map to 422 domain validation failure."""
    resp = test_client.get("/test-route-value-error")
    assert resp.status_code == 422
    assert resp.json() == {"detail": "Invalid parameter value for domain"}
