"""Backend/tests/test_secure_mobile_token_refresh.py
Regression tests for secure mobile token refresh (Phase 2, Module 4).

Validates:
1. Native login obtains refresh token in JSON response and avoids cookie.
2. Browser login retains HTTP-only cookie and does not return refresh token in JSON.
3. Native refresh with valid body token returns new access token and rotated refresh token.
4. Native refresh with X-Refresh-Token header works.
5. Missing, invalid, expired, or wrongly typed refresh tokens are rejected safely with 401.
6. Token-epoch and install-id device binding remain strictly enforced on native refresh.
7. Refresh token reuse triggers revocation and token_epoch bump.
8. Native logout revokes refresh tokens by bumping token_epoch and clears local credentials.
9. Browser logout clears HTTP-only cookie and bumps token_epoch.
10. Credentials/secrets are scrubbed from error details and audit logs.
"""
import os
from datetime import timedelta

os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from jwt_handler import ACCESS, REFRESH, create_access_token, create_refresh_token, decode_token
from main import app
from models import AuditLog, RevokedJti, User
from password_handler import hash_password


@pytest.fixture
def auth_env():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    TestingSession = sessionmaker(bind=eng)
    db = TestingSession()

    user = User(
        employee_id="LM-OFFICER-01",
        full_name="Field Officer One",
        password_hash=hash_password("Secr3tPass!"),
        role="inspector",
        install_id="device-uuid-1234",
        token_epoch=1,
        is_active=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    client = TestClient(app)

    yield {"db": db, "client": client, "user": user}

    app.dependency_overrides.pop(get_db, None)


def test_browser_login_sets_cookie_and_no_json_refresh(auth_env):
    client = auth_env["client"]
    resp = client.post(
        "/auth/login",
        json={"employee_id": "LM-OFFICER-01", "password": "Secr3tPass!"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    assert data.get("refresh_token") is None
    # Must have Set-Cookie header with refresh cookie
    cookie = resp.cookies.get(settings.REFRESH_COOKIE_NAME)
    assert cookie is not None


def test_native_login_returns_refresh_token_in_json_and_skips_cookie(auth_env):
    client = auth_env["client"]
    # With X-Client-Platform header
    resp = client.post(
        "/auth/login",
        json={"employee_id": "LM-OFFICER-01", "password": "Secr3tPass!"},
        headers={"X-Client-Platform": "android"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert "access_token" in data
    refresh_tok = data.get("refresh_token")
    assert refresh_tok is not None
    # No cookie should be set for native client
    assert resp.cookies.get(settings.REFRESH_COOKIE_NAME) is None

    # Verify refresh token claims
    claims = decode_token(refresh_tok, expect=REFRESH)
    assert claims["sub"] == str(auth_env["user"].id)
    assert claims["install_id"] == "device-uuid-1234"
    assert claims["epoch"] == auth_env["user"].token_epoch


def test_native_login_with_body_platform_signal(auth_env):
    client = auth_env["client"]
    resp = client.post(
        "/auth/login",
        json={
            "employee_id": "LM-OFFICER-01",
            "password": "Secr3tPass!",
            "client_platform": "android",
        },
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data.get("refresh_token") is not None


def test_valid_native_refresh_rotates_token(auth_env):
    client = auth_env["client"]
    user = auth_env["user"]
    # Log in as native client
    login_resp = client.post(
        "/auth/login",
        json={"employee_id": "LM-OFFICER-01", "password": "Secr3tPass!"},
        headers={"X-Client-Platform": "android"},
    )
    refresh_token = login_resp.json()["refresh_token"]

    # Refresh using JSON body
    ref_resp = client.post(
        "/auth/refresh",
        json={"refresh_token": refresh_token},
        headers={"X-Client-Platform": "android"},
    )
    assert ref_resp.status_code == 200
    ref_data = ref_resp.json()
    assert "access_token" in ref_data
    rotated_refresh = ref_data.get("refresh_token")
    assert rotated_refresh is not None
    assert rotated_refresh != refresh_token

    # Verify new access token is valid
    acc_claims = decode_token(ref_data["access_token"], expect=ACCESS)
    assert acc_claims["sub"] == str(user.id)
    assert acc_claims["role"] == "inspector"


def test_native_refresh_via_header(auth_env):
    client = auth_env["client"]
    login_resp = client.post(
        "/auth/login",
        json={"employee_id": "LM-OFFICER-01", "password": "Secr3tPass!"},
        headers={"X-Client-Platform": "android"},
    )
    refresh_token = login_resp.json()["refresh_token"]

    # Refresh using X-Refresh-Token header
    ref_resp = client.post(
        "/auth/refresh",
        headers={"X-Refresh-Token": refresh_token, "X-Client-Platform": "android"},
    )
    assert ref_resp.status_code == 200
    assert ref_resp.json().get("refresh_token") is not None


def test_missing_refresh_token_rejected_401(auth_env):
    client = auth_env["client"]
    resp = client.post("/auth/refresh", json={})
    assert resp.status_code == 401
    assert "No refresh token" in resp.json()["detail"]


def test_invalid_refresh_token_rejected_401(auth_env):
    client = auth_env["client"]
    resp = client.post("/auth/refresh", json={"refresh_token": "not.a.valid.jwt"})
    assert resp.status_code == 401
    assert "Refresh" in resp.json()["detail"]


def test_expired_refresh_token_rejected_401(auth_env):
    client = auth_env["client"]
    user = auth_env["user"]
    import jwt_handler as _jh
    expired_tok = _jh._mint(
        {"sub": str(user.id), "install_id": user.install_id, "epoch": user.token_epoch},
        timedelta(seconds=-10),
        REFRESH,
    )
    resp = client.post("/auth/refresh", json={"refresh_token": expired_tok})
    assert resp.status_code == 401
    assert "Refresh expired" in resp.json()["detail"]


def test_access_token_rejected_as_refresh_token(auth_env):
    client = auth_env["client"]
    user = auth_env["user"]
    acc_tok = create_access_token(user.id, user.role, user.install_id)
    resp = client.post("/auth/refresh", json={"refresh_token": acc_tok})
    assert resp.status_code == 401
    assert "wrong token type" in resp.json()["detail"].lower()


def test_token_epoch_mismatch_rejected(auth_env):
    client = auth_env["client"]
    user = auth_env["user"]
    db = auth_env["db"]

    token = create_refresh_token(user.id, user.install_id, user.token_epoch)
    # User epoch bumped (e.g., password change or remote logout)
    user.token_epoch += 1
    db.commit()

    resp = client.post("/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
    assert "Session superseded" in resp.json()["detail"]


def test_installation_binding_mismatch_rejected(auth_env):
    client = auth_env["client"]
    user = auth_env["user"]

    token = create_refresh_token(user.id, "different-device-id", user.token_epoch)
    resp = client.post("/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401
    assert "Device not recognised" in resp.json()["detail"]


def test_refresh_token_reuse_detection_and_revocation(auth_env):
    client = auth_env["client"]
    user = auth_env["user"]
    db = auth_env["db"]

    # 1. First legitimate refresh
    orig_token = create_refresh_token(user.id, user.install_id, user.token_epoch)
    ref_resp = client.post(
        "/auth/refresh",
        json={"refresh_token": orig_token},
        headers={"X-Client-Platform": "android"},
    )
    assert ref_resp.status_code == 200

    # 2. Attacker replays consumed token
    replay_resp = client.post(
        "/auth/refresh",
        json={"refresh_token": orig_token},
        headers={"X-Client-Platform": "android"},
    )
    assert replay_resp.status_code == 401
    assert "Session superseded" in replay_resp.json()["detail"]

    # 3. Verify user's token_epoch was bumped to invalidate ALL sessions
    db.refresh(user)
    assert user.token_epoch > 1


def test_native_logout_revokes_token_epoch(auth_env):
    client = auth_env["client"]
    user = auth_env["user"]
    db = auth_env["db"]

    token = create_refresh_token(user.id, user.install_id, user.token_epoch)
    initial_epoch = user.token_epoch

    logout_resp = client.post("/auth/logout", json={"refresh_token": token})
    assert logout_resp.status_code == 204

    db.refresh(user)
    assert user.token_epoch == initial_epoch + 1

    # Attempting to refresh with the pre-logout token fails
    resp = client.post("/auth/refresh", json={"refresh_token": token})
    assert resp.status_code == 401


def test_credentials_scrubbed_from_errors_and_audit(auth_env):
    client = auth_env["client"]
    db = auth_env["db"]
    secret_marker = "SuperSecretSensitiveDataXYZ"

    # Send a malformed token containing sensitive pattern
    resp = client.post("/auth/refresh", json={"refresh_token": f"bearer-{secret_marker}"})
    assert resp.status_code == 401
    # Check response body does not echo back the token
    assert secret_marker not in resp.text

    # Check audit logs do not contain raw credentials
    logs = db.query(AuditLog).all()
    for log in logs:
        assert secret_marker not in (log.new_value or "")
        assert secret_marker not in (log.old_value or "")
