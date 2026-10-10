"""Settings. Reads .env, validates at import time, fails loudly."""
from functools import lru_cache
from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent

WEAK_SECRETS = {
    "", "change_me", "changeme", "secret", "your-secret-key",
    "dev", "test", "niyamnetra", "sih2026",
}


class Settings(BaseSettings):
    # extra=ignore (not forbid): unknown env vars (e.g. platform-injected
    # PORT, RENDER_*) must not crash boot; required fields are still validated.
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # --- app ---
    ENV: Literal["dev", "prod", "test"] = "dev"
    APP_NAME: str = "NiyamNetra"
    PUBLIC_BASE_URL: str = "http://localhost:8000"

    # --- database ---
    # REQUIRED. Production runs Supabase / PostgreSQL only.
    # SQLite is supported for local dev and the pytest suite (database.py
    # branches on is_sqlite; tests run sqlite by default, postgres when
    # PG_TEST_URL is set). Set in .env as  postgresql+psycopg://...  (psycopg 3).
    # If it is missing, the app refuses to start rather than silently creating
    # a SQLite file.
    DATABASE_URL: str

    # --- auth ---
    JWT_SECRET: str
    JWT_ALGORITHM: Literal["HS256"] = "HS256"
    # Access tokens live 12h (C8): long enough for a field day without
    # re-login, short enough that a stolen token expires same-day. Logout and
    # password change bump token_epoch, which revokes REFRESH tokens only;
    # the current access token stays valid until expiry (client re-logins to
    # rotate it). Documented here so the behaviour is honest, not implied.
    ACCESS_TOKEN_HOURS: int = 12           # C8
    REFRESH_TOKEN_DAYS: int = 30           # C8 — single-use rotation with
    # reuse detection (revoked_jtis table): each refresh consumes its jti, and
    # replaying a consumed jti bumps token_epoch, killing every session.
    # Revocation is via token_epoch bump on logout / password change /
    # install reset / reuse detection. See routers/auth.py.
    REFRESH_COOKIE_NAME: str = "nn_refresh"

    # Cross-site refresh cookie. A browser will not store or send a
    # SameSite=Strict cookie when the page origin and the API origin are
    # different *sites* — which is exactly the case when the portal runs on
    # http://localhost:5173 against a backend on https://<app>.onrender.com.
    # Login still returns an access token, but session restore and the silent
    # 401 refresh both fail, so the officer is logged out on every reload.
    #
    # None means "decide from ENV": cross-site when ENV=prod (HTTPS available,
    # so the Secure attribute that SameSite=None requires can be set), strict on
    # local http. Set REFRESH_COOKIE_CROSS_SITE=true/false in .env to force it.
    #
    # Trade-off of cross-site mode: another site can cause POST /auth/refresh to
    # fire with the cookie attached, but it cannot read the response — the CORS
    # policy above only echoes credentials to allow-listed origins — so no token
    # leaves the browser. Do not widen CORS_ORIGIN_REGEX while this is on.
    REFRESH_COOKIE_CROSS_SITE: bool | None = None

    @property
    def refresh_cookie_cross_site(self) -> bool:
        if self.REFRESH_COOKIE_CROSS_SITE is not None:
            return self.REFRESH_COOKIE_CROSS_SITE
        return self.ENV == "prod"

    # --- CORS ---
    # A regex, not a glob. Starlette does not expand "*" inside an origin.
    #
    # Covers every private-network range a dev laptop actually gets, not just
    # 192.168.x.x: campus and office WiFi hand out 10.x.x.x (and Docker/VPN use
    # 172.16-31.x.x). An Expo web preview served from http://10.16.54.38:8081
    # was rejected by the old 192.168-only pattern, which looked exactly like
    # "the app cannot reach my local backend".
    #
    # Ports: 5173 Vite portal, 8081 Metro/Expo web, 19006 legacy Expo web,
    # 3000 generic dev server. exp:// is the Expo Go native scheme.
    CORS_ORIGIN_REGEX: str = (
        r"^(https?://localhost:(3000|5173|8081|19006)"
        r"|https?://127\.0\.0\.1:(3000|5173|8081|19006)"
        r"|https?://10\.\d{1,3}\.\d{1,3}\.\d{1,3}:(3000|5173|8081|19006)"
        r"|https?://192\.168\.\d{1,3}\.\d{1,3}:(3000|5173|8081|19006)"
        r"|https?://172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}:(3000|5173|8081|19006)"
        r"|https?://([a-zA-Z0-9_-]+\.)*vercel\.app(:[0-9]+)?"
        r"|https?://([a-zA-Z0-9_-]+\.)*onrender\.com(:[0-9]+)?"
        r"|exp://.*)$"
    )
    # Prod override: when ENV=prod the default above still allows localhost
    # origins, which is wrong for a public deploy. Set CORS_ORIGIN_REGEX_PROD
    # to a strict pattern (e.g. your portal domain) and it is used instead.
    # If unset in prod, boot logs a warning (see _warn_prod_cors) and keeps
    # the default — fail-open with a loud warning, not a silent localhost.
    CORS_ORIGIN_REGEX_PROD: str | None = (
        r"^(https?://([a-zA-Z0-9_-]+\.)*(vercel\.app|onrender\.com)(:[0-9]+)?|exp://.*)$"
    )

    @property
    def effective_cors_regex(self) -> str:
        if self.ENV == "prod" and self.CORS_ORIGIN_REGEX_PROD:
            return self.CORS_ORIGIN_REGEX_PROD
        return self.CORS_ORIGIN_REGEX

    # How many right-most X-Forwarded-For hops to trust (Render = 1 proxy).
    # _client_ip takes the entry at index -count, not [0], so a client cannot
    # spoof the IP by prepending to the header.
    TRUSTED_PROXY_COUNT: int = 1

    # --- evidence ---
    EVIDENCE_DIR: Path = BASE_DIR / "evidence"
    OUT_DIR: Path = BASE_DIR / "out"
    MAX_UPLOAD_MB: int = 25                # not 5 — see note
    MAX_IMAGE_PIXELS: int = 80_000_000     # decompression-bomb ceiling, 09 §2.5
    MIN_PANEL_PIXEL_HEIGHT: int = 400      # C14 / 02 §7.4
    MAX_RESIDUAL_TILT_DEG: float = 15.0
    EVIDENCE_MIN_FREE_GB: float = 5.0      # below this, new inspections are refused
    DERIVED_MAX_WIDTH_PX: int = 1600       # display copies only; originals are never resized

    # --- capacity, 11 §4 ---
    # ASSESS_CONCURRENCY is enforced by the semaphore in routers/scans.py
    # (0 → cpu_count, 503+Retry-After when busy). RATE_LIMIT_API_PER_MIN and
    # DASHBOARD_CACHE_TTL_SECONDS are documented reservations, not enforced
    # per-request guards: login brute-force is braked by the limiter in
    # routers/auth.py; the review-queue badge is never cached.
    ASSESS_CONCURRENCY: int = 0            # 0 → os.cpu_count()
    ASSESS_QUEUE_WAIT_S: int = 20          # then 503 with Retry-After
    RATE_LIMIT_LOGIN_PER_MIN: int = 5      # per employee_id and per IP
    RATE_LIMIT_API_PER_MIN: int = 300      # reserved; not enforced per-request
    DASHBOARD_CACHE_TTL_SECONDS: int = 60  # never applied to the review-queue badge

    # --- OCR ---
    # Render free tier has 512MB RAM; PaddleOCR needs ~1.5GB, Tesseract needs
    # apt binaries absent from the native Python runtime. So the deploy-safe
    # path is CLOUD OCR (zero RAM, only httpx): Google Vision (best accuracy,
    # Hindi + small fonts) first, OCR.space (free, no card) second, then local
    # Tesseract/Paddle only when present. See ocr_engine.run_ocr cascade.
    OCR_PROVIDER: str = "auto"  # auto | google | ocrspace | azure
    OCR_PROVIDER_ORDER: str = "google_vision,ocr_space,azure"
    OCR_PROVIDER_CHAIN: str = "google_vision,ocr_space,azure"
    OCR_LANGS: str = "en,hi"
    OCR_MIN_CONFIDENCE: float = 0.60
    TESSERACT_CMD: str | None = None
    DISABLE_PADDLE: bool = False  # set 1 on 512MB deploys to skip import attempt
    # PaddleOCR recognition language (2.8 has no Devanagari model, so Hindi is
    # covered by Tesseract eng+hin and Vision en+hi; override if yours does).
    OCR_PADDLE_LANG: str = "en"

    # Cloud OCR fallback (OCR.space). When BOTH PaddleOCR and Tesseract are
    # absent — exactly the slim Render deploy — run_ocr falls back to this HTTPS
    # API so a scan still gets real text (and therefore a real verdict) instead
    # of not_assessed. It needs no heavy packages: only httpx + opencv, both
    # already in requirements-render.txt. Leave the key empty to disable, and
    # the chain ends at engine="none" exactly as before.
    #
    # Privacy note: enabling this sends the label image to a third party. Fine
    # for a demo; weigh it for anything handling real inspection evidence.
    OCR_SPACE_API_KEY: str | None = None
    OCR_SPACE_URL: str = "https://api.ocr.space/parse/image"
    OCR_SPACE_ENGINE: int = 1              # engine 1 returns word boxes (needed
                                           # for letter-height checks) and supports
                                           # non-Latin langs; 2 is Latin-only, no overlay.
    OCR_SPACE_LANGUAGE: str = "eng"        # OCR.space code, e.g. "eng" or "hin".
    OCR_SPACE_TIMEOUT_S: float = 25.0
    # Best-accuracy cloud OCR: Google Cloud Vision DOCUMENT_TEXT_DETECTION.
    # Needs only httpx (already in requirements-render.txt), ~0MB RAM.
    # Get key: console.cloud.google.com → enable Vision API → create API key.
    # When set, it runs BEFORE OCR.space (higher accuracy on small/Hindi text).
    # Leave blank to use OCR.space only. See .env.example.
    GOOGLE_VISION_API_KEY: str | None = None
    GOOGLE_VISION_URL: str = "https://vision.googleapis.com/v1/images:annotate"
    GOOGLE_VISION_TIMEOUT_S: float = 25.0

    # Free Generative AI Vision OCR: Google Gemini Flash (Google AI Studio).
    # 100% FREE, NO credit card or billing required (aistudio.google.com/app/apikey).
    # Single-model call, short 8s timeout so a slow/erroring Gemini never stalls
    # the cascade. Auto order: Google Vision (real word boxes) -> Gemini -> Groq
    # -> OCR.space -> Tesseract -> Paddle, so letter-height/clear-space checks get
    # genuine per-line geometry from the first engine that supplies it.
    GEMINI_API_KEY: str | None = None
    GEMINI_MODEL: str = "gemini-2.5-flash-lite"
    GEMINI_TIMEOUT_S: float = 8.0

    # Free Ultra-fast Vision OCR / Cloud LLM: Groq (console.groq.com/keys)
    GROQ_API_KEY: str | None = None
    GROQ_MODEL: str = "openai/gpt-oss-20b"
    GROQ_TIMEOUT_S: float = 12.0

    # Azure AI Vision OCR (Read API v4.0 / Analyze features=read)
    AZURE_VISION_KEY: str | None = None
    AZURE_VISION_ENDPOINT: str | None = None
    AZURE_VISION_TIMEOUT_S: float = 15.0

    # --- Phase 3: Cloud LLM Structured Declaration Extraction ---
    LLM_PROVIDER: str = "groq"              # groq | gemini | auto
    LLM_MODEL: str = "openai/gpt-oss-20b"   # primary high-speed model
    LLM_FALLBACK_PROVIDER: str = "gemini"   # fallback provider
    LLM_FALLBACK_MODEL: str = "gemini-2.5-flash-lite"
    LLM_TIMEOUT_S: float = 12.0
    LLM_CACHE_ENABLED: bool = True

    # --- rules ---
    RULE_PACK_VERSION: str = "2026.09.v1"
    RULES_AS_AT: str = "2026-09-21"        # Fourth Amendment (GSR 826(E)) in force
    RULES_CATALOG: Path = BASE_DIR / "rules" / "rule_packs" / "lmpc_2026_09_v1.json"
    ENGINE_VERSION: str = "2.4.0"

    # --- supabase storage (optional mirror; local remains primary) ---
    SUPABASE_URL: str | None = None
    SUPABASE_SERVICE_KEY: str | None = None
    SUPABASE_BUCKET: str = "NiyamNetra_Images"

    @field_validator("JWT_SECRET")
    @classmethod
    def _secret_must_be_strong(cls, v: str) -> str:
        if v.strip().lower() in WEAK_SECRETS or len(v) < 32:
            raise ValueError(
                "JWT_SECRET must be at least 32 characters and not a placeholder. "
                "Generate one: python -c \"import secrets; print(secrets.token_hex(32))\""
            )
        return v

    @property
    def is_sqlite(self) -> bool:
        return self.DATABASE_URL.startswith("sqlite")


@lru_cache
def get_settings() -> Settings:
    s = Settings()                      # raises at import if .env is missing JWT_SECRET
    s.EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    s.OUT_DIR.mkdir(parents=True, exist_ok=True)
    if s.ENV == "prod" and not s.CORS_ORIGIN_REGEX_PROD:
        s.CORS_ORIGIN_REGEX_PROD = (
            r"^(https?://([a-zA-Z0-9_-]+\.)*(vercel\.app|onrender\.com)(:[0-9]+)?|exp://.*)$"
        )
    return s


settings = get_settings()
