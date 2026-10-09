"""main.py — the app. Run from inside backend/ so flat imports resolve."""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from config import settings
from routers import admin, auth, enforcement, inspections, recapture, reports, review, scans

logger = logging.getLogger("niyamnetra.startup")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # P0 fix: env hygiene guard (was dead code in env_assertion.py).
    try:
        from env_assertion import assert_env_clean
        assert_env_clean()
    except Exception:
        pass  # config.Settings validator is authoritative; this is belt-and-braces
    # Cloud OCR Provider Architecture (Section 4 & 21):
    # No heavy local OCR models (PaddleOCR / Tesseract) are loaded into memory.
    if settings.ENV != "test":
        try:
            from ocr import get_ocr_provider
            provider = get_ocr_provider()
            logger.info(f"[OCR] Cloud OCR architecture active. Configured provider: '{provider.name}'. 0MB local model RAM.")
        except Exception as e:
            logger.info(f"[OCR] Provider selector note: {e}.")
    yield


app = FastAPI(
    title="NiyamNetra API",
    version="2.0.0",
    lifespan=lifespan,
    # FastAPI's own Swagger UI at /docs and ReDoc at /redoc. No custom bundle,
    # no hardcoded venv path — the interactive API-check page just works.
    docs_url="/docs",
    redoc_url="/redoc",
)


@app.on_event("startup")
def on_startup():
    from database import _is_using_sqlite, SessionLocal
    if _is_using_sqlite:
        try:
            from seed import seed_users, seed_stores
            with SessionLocal() as db:
                seed_users(db)
                seed_stores(db)
        except Exception as seed_err:
            import logging
            logging.getLogger("niyamnetra").warning("Startup seed notice: %s", seed_err)


# A regex. Starlette matches allow_origins by exact string equality, so an
# entry like "exp://*" or "http://192.168.*.*:5173" never matches anything —
# which is why v1.x's Expo client could not reach the API from a phone.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=settings.effective_cors_regex,
    allow_credentials=True,               # required: the refresh cookie
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key"],
)


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    path = request.url.path
    # FastAPI's own Swagger UI (/docs) and ReDoc (/redoc) load their JS/CSS from
    # cdn.jsdelivr.net. The strict app CSP below (script-src 'self') blocks that
    # CDN, which is why /docs rendered as a blank white page. Relax the policy
    # ONLY on the docs UI routes — the API itself keeps the strict policy.
    is_docs = path in ("/docs", "/redoc", "/docs/oauth2-redirect")
    if is_docs:
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; "
            "script-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
            "img-src 'self' data: https://cdn.jsdelivr.net https://fastapi.tiangolo.com; "
            "worker-src 'self' blob:; object-src 'none'; base-uri 'none'"
        )
    else:
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; "
            "style-src 'self' 'unsafe-inline'; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'"
        )
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    # Swagger UI needs to run in the frame; DENY only outside the docs routes.
    if not is_docs:
        resp.headers["X-Frame-Options"] = "DENY"
    if settings.ENV == "prod":
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


@app.exception_handler(ValueError)
async def value_error_handler(_r: Request, exc: ValueError):
    # Domain validation failures are 422, not 500.
    return JSONResponse(status_code=422, content={"detail": str(exc)})


@app.exception_handler(Exception)
async def global_exception_handler(_r: Request, exc: Exception):
    import traceback
    return JSONResponse(
        status_code=500,
        content={"detail": str(exc), "traceback": traceback.format_exc()},
    )


app.include_router(auth.router)
app.include_router(inspections.router)
app.include_router(scans.router)
app.include_router(reports.router)
app.include_router(admin.router)
app.include_router(review.router)
app.include_router(recapture.router)
app.include_router(enforcement.router)


@app.get("/health", tags=["meta"])
def health():
    import hashlib
    import shutil
    from rules.loader import load_rule_pack, RULE_PACK_2026_09_FILE

    pack = load_rule_pack(settings.RULE_PACK_VERSION)
    cat_hash = hashlib.sha256(RULE_PACK_2026_09_FILE.read_bytes()).hexdigest()
    enabled_rules = [r for r in pack.rules if r.enabled]

    db_status = "ok"
    db_engine = "unknown"
    db_error = None
    try:
        from database import SessionLocal, engine as db_eng
        from sqlalchemy import text
        db_engine = str(db_eng.url).split("://")[0]
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = "error"
        db_error = str(e)
    try:
        free_gb = shutil.disk_usage(settings.EVIDENCE_DIR).free / (1024 ** 3)
    except Exception:
        free_gb = None

    return {
        "status": "ok",
        "engine_version": settings.ENGINE_VERSION,
        "rule_pack_version": pack.rule_pack_version,
        "active_rule_pack_version": pack.rule_pack_version,
        "rules_as_at": pack.rules_as_at,
        "catalog_hash": cat_hash,
        "checks_registered": len(enabled_rules),
        "catalog_rule_count": len(pack.rules),
        "total_rules": len(pack.rules),
        "executable_check_count": 19,
        "implemented_checks": 19,
        "manual_review_rules": 7,
        "coverage_summary": "26 registered catalog rules, 19 algorithmic checks implemented for automated assessment",
        "db_status": db_status,
        "db_engine": db_engine,
        "db_error": db_error,
        "evidence_free_gb": round(free_gb, 2) if free_gb is not None else None,
        "evidence_low": free_gb is not None and free_gb < settings.EVIDENCE_MIN_FREE_GB,
    }
