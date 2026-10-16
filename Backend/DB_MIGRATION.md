# Database Migrations — NiyamNetra

Authoritative schema reference: [`documents/06_DATA_MODEL.md`](../documents/06_DATA_MODEL.md)

## 1. Migration Chain (Alembic)

The NiyamNetra database schema evolves through 9 sequential Alembic migrations:

1. `0001_initial_schema`: Initial baseline schema, foreign keys, check constraints, and immutable audit triggers. Defines `users`, `stores`, `inspections`, `scans`, `scan_images`, `findings`, and `audit_logs`.
2. `0002_listing_columns`: Adds e-commerce fields (`listing_url`, `listing_text`, `platform_has_origin_filter`) on `scans` for Rule 6(10) and Rule 6(10A) checks (CHK15, CHK16).
3. `0003_reference_pixel`: Adds `reference_pixel_size` on `scans` to preserve optical calibration scale reproducibility across process restarts.
4. `0004_token_hygiene`: Database hygiene migration sanitizing image URLs in `scan_images` to remove raw bearer tokens and credential query strings.
5. `0005_report_records`: Adds `report_records` table for immutable PDF/DOCX generation tracking, SHA-256 docket digests, and tamper-evident QR verification.
6. `0006_idempotency_keys_evidence_retention`: Creates `idempotency_keys` table for request deduplication and distributed worker leasing, plus evidence retention lifecycle columns.
7. `0007_refusal_and_ocr_cache`: Adds `signature_status` (`signed` | `refused`) to `inspections` for zero-scan merchant refusal handling; adds `ocr_cache_hash` and `ocr_cache` to `scans` for derived OCR caching.
8. `0008_enforce_not_null_constraints`: Enforces strict NOT NULL constraints, check constraints, and referential integrity across core entity tables.
9. `0009_add_missing_phase2_to_phase6_columns`: Adds Phase 2–6 columns:
   - `scans`: `llm_structured_data`, `llm_cache_hash`, `llm_provider`, `llm_model`, `llm_duration_ms`
   - `inspections`: `clock_skew_seconds`, `edited_offline`
   - `findings`: `human_verdict`, `effective_verdict`, `override_reason`, `overridden_by`, `overridden_at`

## 2. Execution Runbook

### Fresh Databases (Local / Dev / Tests)
When initializing a fresh SQLite database, `Base.metadata.create_all(bind=engine)` creates all tables with current columns.
Alembic migrations use defensive `try/except` column additions and `IF NOT EXISTS` checks, making them idempotent no-ops on fresh schemas.

### Existing Production Databases (Supabase / PostgreSQL)
To apply pending migrations on an active PostgreSQL database:
```bash
cd Backend
alembic upgrade head
```

Verify migration status and history:
```bash
alembic current
alembic history --verbose
```

Run backend test verification:
```bash
pytest -q
```

### SQLite to PostgreSQL Migration
1. Apply the complete migration chain against the target PostgreSQL `DATABASE_URL`:
   ```bash
   alembic upgrade head
   ```
2. Export and load seed or master store records if required.
3. Configure `DATABASE_URL` in `.env` using the `postgresql+psycopg://` scheme (Psycopg 3).
4. Verify with automated test suite (`pytest -q`).
