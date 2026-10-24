"""routers/auth.py"""
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Request, Response, status
from sqlalchemy.orm import Session

from pydantic import BaseModel, Field, field_validator

from audit import append_audit
from config import settings
from database import get_db
from jwt_handler import (
    ACCESS, REFRESH, TokenError, create_access_token, create_refresh_token, decode_token,
)
from models import User
from password_handler import hash_password, verify_password
from rbac import get_current_user
from schemas import LoginRequest, LoginResponse, LogoutRequest, RefreshRequest, UserOut

router = APIRouter(prefix="/auth", tags=["auth"])

# DB-backed login rate limiter: per employee_id+IP, max 5 attempts per 60s
# sliding window. Rows live in login_attempts so every worker enforces the
# same count (the old per-process dict did not survive multi-worker deploys).
# 429 with Retry-After when exceeded. This is a brute-force brake, not a
# distributed throttle.
_LOGIN_WINDOW_S = 60.0
_LOGIN_MAX_PER_WINDOW = 5


def _login_rate_key(employee_id: str, ip: str | None) -> str:
    return f"{(employee_id or '').strip().lower()}|{(ip or 'unknown')}"


def _check_login_rate_limit(db: Session, employee_id: str, ip: str | None) -> None:
    from datetime import timedelta
    from models import LoginAttempt, utcnow
    now = utcnow()
    cutoff = now - timedelta(seconds=_LOGIN_WINDOW_S)
    key = _login_rate_key(employee_id, ip)
    # Opportunistic prune so the table cannot grow without bound.
    try:
        db.query(LoginAttempt).filter(LoginAttempt.attempted_at < cutoff).delete()
        db.commit()
    except Exception:
        db.rollback()
    hits = (db.query(LoginAttempt).filter(
        LoginAttempt.rate_key == key,
        LoginAttempt.attempted_at >= cutoff).count())
    if hits >= _LOGIN_MAX_PER_WINDOW:
        oldest = (db.query(LoginAttempt).filter(
            LoginAttempt.rate_key == key,
            LoginAttempt.attempted_at >= cutoff)
            .order_by(LoginAttempt.attempted_at).first())
        retry_after = 1
        if oldest is not None and oldest.attempted_at is not None:
            try:
                _old = oldest.attempted_at
                if _old.tzinfo is None:
                    _old = _old.replace(tzinfo=timezone.utc)
                retry_after = max(1, int(_LOGIN_WINDOW_S - (now - _old).total_seconds()) + 1)
            except Exception:
                pass
        raise HTTPException(
            status.HTTP_429_TOO_MANY_REQUESTS,
            detail="Too many login attempts; try again shortly",
            headers={"Retry-After": str(retry_after)},
        )
    try:
        db.add(LoginAttempt(rate_key=key, attempted_at=now))
        db.commit()
    except Exception:
        db.rollback()


def _set_refresh_cookie(resp: Response, token: str) -> None:
    # SameSite=None is only honoured together with Secure, so the two flags move
    # as one. See config.REFRESH_COOKIE_CROSS_SITE for why and for the trade-off.
    cross_site = settings.refresh_cookie_cross_site
    resp.set_cookie(
        key=settings.REFRESH_COOKIE_NAME,
        value=token,
        max_age=settings.REFRESH_TOKEN_DAYS * 86400,
        httponly=True,                     # no JavaScript can read it
        secure=cross_site or settings.ENV == "prod",   # over http on the LAN in dev
        samesite="none" if cross_site else "strict",
        path="/auth",
    )


def _is_native_client(request: Request, body: Any = None) -> bool:
    platform_header = (
        request.headers.get("x-client-platform")
        or request.headers.get("x-platform")
        or ""
    ).strip().lower()
    if platform_header in ("android", "ios", "native", "mobile"):
        return True
    if body and hasattr(body, "client_platform") and body.client_platform:
        if str(body.client_platform).strip().lower() in ("android", "ios", "native", "mobile"):
            return True
    return False


@router.post("/login", response_model=LoginResponse)
def login(body: LoginRequest, request: Request, response: Response,
          db: Session = Depends(get_db)):
    _check_login_rate_limit(db, body.employee_id, _client_ip(request))
    user = db.query(User).filter(User.employee_id == body.employee_id).first()

    # Constant-ish work on both paths, and one message for both failures, so
    # the endpoint is not a username oracle.
    ok = user is not None and user.is_active and verify_password(
        body.password, user.password_hash
    )
    if not ok:
        append_audit(
            db, user_id=user.id if user else None, action="login_failed",
            new_value=body.employee_id, ip_address=_client_ip(request),
        )
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED, detail="Invalid employee ID or password"
        )

    from routers.auth_helpers import bind_install       # see §3.4
    install_id = bind_install(db, user)

    access = create_access_token(user.id, user.role, install_id)
    refresh = create_refresh_token(user.id, install_id, user.token_epoch)

    is_native = _is_native_client(request, body)
    if not is_native:
        _set_refresh_cookie(response, refresh)

    append_audit(db, user_id=user.id, action="login", ip_address=_client_ip(request),
                 user_agent=request.headers.get("user-agent", "")[:240])

    return LoginResponse(
        access_token=access,
        expires_in=settings.ACCESS_TOKEN_HOURS * 3600,
        user=UserOut.model_validate(user),
        install_id=install_id,
        refresh_token=refresh if is_native else None,
    )


@router.post("/refresh", response_model=LoginResponse)
def refresh_token(
    request: Request,
    response: Response,
    body: RefreshRequest | None = Body(None),
    db: Session = Depends(get_db),
):
    raw = None
    if body and body.refresh_token:
        raw = body.refresh_token.strip()
    elif request.headers.get("x-refresh-token"):
        raw = request.headers.get("x-refresh-token").strip()
    else:
        raw = request.cookies.get(settings.REFRESH_COOKIE_NAME)

    if not raw:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="No refresh token")
    try:
        claims = decode_token(raw, expect=REFRESH)
    except TokenError as e:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail=f"Refresh {e}")

    try:
        refresh_uid = int(claims["sub"])
    except (ValueError, TypeError, OverflowError):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Refresh invalid")
    user = db.get(User, refresh_uid)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Account unavailable")
    # A password change invalidates every refresh token issued before it.
    if claims.get("epoch") != user.token_epoch:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session superseded")
    if user.install_id and claims.get("install_id") != user.install_id:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Device not recognised")

    # Single-use rotation with reuse detection (RFC 6819 §5.2.2.3). The
    # presented jti must not already be consumed: re-presenting a consumed jti
    # means the token was stolen and replayed, so every refresh token for the
    # user dies via a token_epoch bump and the replay fails closed.
    from models import RevokedJti, utcnow
    _jti = claims.get("jti")
    if not _jti:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Refresh invalid")
    if db.get(RevokedJti, _jti) is not None:
        user.token_epoch += 1
        try:
            db.add(RevokedJti(jti=f"reuse-{_jti}"[:32], user_id=user.id, reason="reuse"))
        except Exception:
            pass
        try:
            db.commit()
        except Exception:
            db.rollback()
        append_audit(db, user_id=user.id, action="refresh_reuse_detected",
                     ip_address=_client_ip(request))
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session superseded")
    try:
        db.add(RevokedJti(jti=_jti, user_id=user.id, reason="consumed"))
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Session superseded")
    # Opportunistic prune: refresh TTL is 30d, so anything revoked >31d ago
    # belongs to an expired token and can go.
    try:
        from datetime import timedelta
        _cut = utcnow() - timedelta(days=31)
        db.query(RevokedJti).filter(RevokedJti.revoked_at < _cut).delete()
        db.commit()
    except Exception:
        db.rollback()

    is_native = _is_native_client(request, body)
    if not is_native and (body and body.refresh_token or request.headers.get("x-refresh-token")):
        is_native = True

    new_refresh = create_refresh_token(user.id, user.install_id, user.token_epoch)
    if not is_native:
        _set_refresh_cookie(response, new_refresh)

    return LoginResponse(
        access_token=create_access_token(user.id, user.role, user.install_id),
        expires_in=settings.ACCESS_TOKEN_HOURS * 3600,
        user=UserOut.model_validate(user),
        install_id=user.install_id or "",
        refresh_token=new_refresh if is_native else None,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    body: LogoutRequest | None = Body(None),
    db: Session = Depends(get_db),
):
    """Revoke all refresh tokens for this user (token_epoch bump) and clear
    the refresh cookie. Idempotent: with no/invalid cookie the cookie is still
    cleared and no error is raised."""
    raw = None
    if body and body.refresh_token:
        raw = body.refresh_token.strip()
    elif request.headers.get("x-refresh-token"):
        raw = request.headers.get("x-refresh-token").strip()
    else:
        raw = request.cookies.get(settings.REFRESH_COOKIE_NAME)

    if raw:
        try:
            claims = decode_token(raw, expect=REFRESH)
        except TokenError:
            claims = None
        if claims is not None:
            try:
                uid = int(claims.get("sub"))
            except (ValueError, TypeError, OverflowError):
                uid = None
            if uid is not None:
                user = db.get(User, uid)
                if user is not None:
                    user.token_epoch += 1
                    db.commit()
                    append_audit(
                        db, user_id=user.id, action="logout",
                        ip_address=_client_ip(request),
                    )
    # The delete must repeat the attributes the cookie was SET with, or the
    # browser treats it as a different cookie and the old one survives logout.
    cross_site = settings.refresh_cookie_cross_site
    response.delete_cookie(
        settings.REFRESH_COOKIE_NAME,
        path="/auth",
        httponly=True,
        secure=cross_site or settings.ENV == "prod",
        samesite="none" if cross_site else "strict",
    )


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    return user


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=8, max_length=72)
    new_password: str = Field(min_length=12, max_length=72)

    @field_validator("new_password", mode="after")
    @classmethod
    def _strong(cls, v: str) -> str:
        # Same letter+digit rule as CreateUserRequest: an admin-created
        # password and a self-chosen one must meet the same bar.
        if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
            raise ValueError("Password must contain at least one letter and one digit")
        return v


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(body: ChangePasswordRequest, request: Request,
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """Verify the old password, set the new one, and bump token_epoch so every
    refresh token issued before now is dead (§3.2). The current access token
    keeps working until it expires; the client should re-login to rotate it."""
    if not verify_password(body.old_password, user.password_hash):
        append_audit(db, user_id=user.id, action="password_change_failed",
                     ip_address=_client_ip(request))
        raise HTTPException(status.HTTP_400_BAD_REQUEST,
                            detail="Old password is incorrect")
    user.password_hash = hash_password(body.new_password)
    user.token_epoch += 1
    db.commit()
    append_audit(db, user_id=user.id, action="password_changed",
                 ip_address=_client_ip(request))


def _client_ip(request: Request) -> str | None:
    # Only trust the forwarded header when a reverse proxy is configured; an
    # unproxied deployment lets any client set it. With TRUSTED_PROXY_COUNT
    # proxies (Render default 1), take the entry at index -count from the
    # right — the left-most entries are client-controlled and must not be
    # trusted (spoofing [0] lets any client forge its IP for rate limits).
    if settings.ENV == "prod":
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            parts = [p.strip() for p in fwd.split(",") if p.strip()]
            if parts:
                try:
                    count = max(1, int(getattr(settings, "TRUSTED_PROXY_COUNT", 1) or 1))
                except Exception:
                    count = 1
                idx = max(0, len(parts) - count)
                return parts[idx][:45]
    return request.client.host if request.client else None
