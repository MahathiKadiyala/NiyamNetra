# Database Schema & Entity Data Model

**Document Code:** `DOC-06`  
**Applies to:** PostgreSQL (Production on Supabase) & SQLite (Development)  
**ORM Engine:** SQLAlchemy 2.0  
**Migration Framework:** Alembic (Migrations 0001 through 0009)  

---

## 1. Entity-Relationship Architecture

```text
┌─────────────────┐       conducts (1:N)        ┌─────────────────┐
│      User       │ ──────────────────────────> │   Inspection    │
│ (Field Officer) │                             │ (Store Session) │
└────────┬────────┘                             └────────┬────────┘
         │                                               │
         │ acts / traces (1:N)                           │ hosts / contains (1:N)
         ▼                                               ▼
┌─────────────────┐       traces (1:N)          ┌─────────────────┐
│    AuditLog     │ <────────────────────────── │      Scan       │
│ (SHA-256 Chain) │                             │ (Package Unit)  │
└─────────────────┘                             └────────┬────────┘
                                                         │
                                    ┌────────────────────┴────────────────────┐
                                    │ attaches (1:N)                          │ produces (1:N)
                                    ▼                                         ▼
                         ┌────────────────────┐                    ┌────────────────────┐
                         │     ScanImage      │                    │      Finding       │
                         │ (RAW SHA-256 + Crop│                    │ (26 Legal Checks)  │
                         └────────────────────┘                    └────────────────────┘
```

### Relational Entity Matrix

| Entity | Primary Key | Foreign Keys | Relationship / Cardinality | Description |
|---|---|---|---|---|
| **User** | `id` | None | `1:N` with Inspection, AuditLog, Finding overrides | Officers, supervisors, and central administrators |
| **Store** | `id` | None | `1:N` with Inspection | Retail establishments, supermarkets, warehouses |
| **Inspection** | `id` | `user_id`, `store_id` | `1:N` with Scan, AuditLog, ReportRecord | Store visit session with GPS geofencing |
| **Scan** | `id` | `inspection_id`, `duplicate_of` | `1:N` with ScanImage, Finding, AuditLog | Package evaluation unit with 3-state verdict |
| **ScanImage** | `id` | `scan_id` | `N:1` with Scan | RAW camera original bytes, SHA-256, and rectified crops |
| **Finding** | `id` | `scan_id` | `N:1` with Scan | Individual statutory rule findings (26 checks catalog) |
| **AuditLog** | `id` | `inspection_id`, `scan_id`, `user_id` | Append-only ledger | Cryptographic SHA-256 hash-chained tamper-evident log |
| **ReportRecord** | `id` | `inspection_id` | `N:1` with Inspection | Cryptographically signed PDF/DOCX inspection dockets |

---

## 2. Core Domain Entities

### 2.1 `User` (`users`)
Represents an authenticated field inspector, supervisory officer, or central administrator.
- **Key Columns:**
  - `id` (Integer, Primary Key)
  - `employee_id` (String(32), Unique, Indexed): Official identifier (e.g. `LM-TG-1042`, `LM-ADM-001`).
  - `role` (String(16)): Either `inspector` or `admin`. Enforced via CHECK constraint.
  - `password_hash` (String(128)): Passlib bcrypt hash (cost factor 12).
  - `install_id` (String(64), Nullable): 32-byte hex string generated on the client and bound during login.
  - `token_epoch` (Integer, Default 0): Incremented on password change or remote device reset, instantly invalidating active refresh tokens.

### 2.2 `Store` (`stores`)
Represents an inspected physical premise, warehouse, or retail shop.
- **Key Columns:**
  - `id` (Integer, Primary Key)
  - `name` (String(160)): Registered business name.
  - `address`, `city`, `district`, `state`, `pincode`: Normalized location descriptors.
  - `latitude`, `longitude` (Float): GPS coordinates used for geofence validation.
  - `geofence_radius_m` (Integer, Default 150): Authorized inspection perimeter in meters.

### 2.3 `Inspection` (`inspections`)
Represents a distinct enforcement visit conducted at a specific store by an officer.
- **Key Columns:**
  - `id` (Integer, Primary Key)
  - `user_id` (Integer, Foreign Key to `users.id`)
  - `store_id` (Integer, Foreign Key to `stores.id`)
  - `inspection_date` (Date): Canonical date of visit.
  - `status` (String(16)): `draft` -> `submitted`. Once submitted, scans and images are frozen.
  - `signature_status` (String(16), Nullable): `signed` or `refused` (for zero-scan merchant refusal records).
  - `geofence_status` (String(16)): `verified`, `out_of_bounds`, or `unavailable`.
  - `edited_offline` (Boolean, Default False): Set if inspection was recorded in offline mode.
  - `clock_skew_seconds` (Integer, Nullable): Time delta between device clock and server clock at sync.

### 2.4 `Scan` (`scans`)
Represents a single packaged commodity inspected during a visit.
- **Key Columns:**
  - `id` (Integer, Primary Key)
  - `inspection_id` (Integer, Foreign Key to `inspections.id`)
  - `commodity_generic`, `brand_name`, `batch_number`: Package labeling details.
  - `overall_result` (String(16)): `compliant`, `violation`, `not_assessed`, or `out_of_scope`.
  - `violation_limb` (String(16), Nullable): Legal Metrology Act limb (`36(1)` or `36(2)`).
  - `checks_total` (Integer, Default 19): Total executable checks evaluated per scan.
  - `checks_assessed` (Integer, Default 0): Count of checks that resolved to `pass` or `fail`.
  - `rule_pack_version` (String(32)): Active rule pack version tag (`2026.09.v1`).
  - `catalog_hash` (String(64)): SHA-256 digest of active statutory rule pack catalog.
  - `duplicate_of` (Integer, Foreign Key to `scans.id`, Nullable): Set if visual similarity exceeds threshold.
  - `ocr_confidence_mean` (Float, Nullable): Mean optical recognition confidence.
  - `ocr_text` (Text, Nullable): Concatenated OCR transcription.
  - `ocr_cache_hash` (String(64), Nullable): SHA-256 digest of sorted image records `(panel, sha256)`.
  - `ocr_cache` (Text, Nullable): Serialized cached OCR line data.
  - `llm_structured_data` (Text, Nullable): Serialized JSON of structured entity extractions.
  - `llm_cache_hash` (String(64), Nullable): Hash of OCR input and prompt version.
  - `llm_provider` (String(32), Nullable): Active LLM provider (e.g. `groq`).
  - `llm_model` (String(64), Nullable): Extraction model name (e.g. `llama-3.3-70b-versatile`).
  - `llm_duration_ms` (Float, Nullable): Inference duration in milliseconds.

> **Cache Fingerprint Note & Discrepancy Resolution:** While earlier audit reports colloquially referred to `ocr_cache_fingerprint` and `assessment_cache_fingerprint`, the actual durable database columns defined on the `scans` table (Alembic migrations 0007 and 0009) are `ocr_cache_hash` (String 64) and `llm_cache_hash` (String 64). The end-to-end composite `assessment_input_fingerprint` is dynamically computed in memory across geometry, scope flags, prompt schemas, and image digests via `compute_assessment_fingerprint(scan, images)` and tracked in `_ASSESSMENT_FINGERPRINTS` to gate the fast path.


### 2.5 `ScanImage` (`scan_images`)
Represents a captured packaging panel photo attached to a scan.
- **Key Columns:**
  - `id` (Integer, Primary Key)
  - `scan_id` (Integer, Foreign Key to `scans.id`)
  - `panel` (String(24)): `front`, `back`, `side`, `mrp`, `batch`, `principal`, or `other`.
  - `sha256` (String(64), Indexed): Cryptographic hash of original un-modified camera photo.
  - `file_path` (String(400)): Storage path for authoritative raw camera bytes.
  - `byte_size`, `width_px`, `height_px`: Image metadata.
  - `mime_type` (String(40)): MIME format (`image/jpeg`, `image/png`).
  - `phash` (String(16), Nullable): Banded 64-bit perceptual hash (8 bands of 8 bits) for deduplication.

### 2.6 `Finding` (`findings`)
Represents an individual statutory check evaluation on a scan.
- **Key Columns:**
  - `id` (Integer, Primary Key)
  - `scan_id` (Integer, Foreign Key to `scans.id`)
  - `check_id` (String(16)): Statutory code (e.g. `CHK01`, `CHK06`, `CHK18`).
  - `title` (String(200)): Statutory rule title.
  - `engine_verdict` (String(16)): Algorithmic verdict (`pass`, `fail`, `not_assessed`). **IMMUTABLE.**
  - `human_verdict` (String(16), Nullable): Officer override verdict (`pass`, `fail`, `not_assessed`).
  - `effective_verdict` (String(16), Nullable): Resolved effective verdict (`human_verdict` or `engine_verdict`).
  - `override_reason` (Text, Nullable): Mandatory officer justification (minimum 10 characters).
  - `overridden_by` (Integer, Nullable): User ID of the officer recording the adjudication.
  - `overridden_at` (DateTime, Nullable): UTC timestamp of officer action.
  - `severity` (String(16)): `critical`, `major`, `minor`, or `advisory`.
  - `citation` (String(160)): Specific statutory citation (e.g. *Rule 7(1) read with Table-I*).
  - `ledger_ref` (String(16)): Internal legal ledger reference (e.g. `L-03`).

### 2.7 `AuditLog` (`audit_logs`)
Append-only cryptographic ledger tracking every system action.
- **Key Columns:**
  - `id` (Integer, Primary Key)
  - `seq` (Integer, Nullable): Monotonic sequence number.
  - `inspection_id`, `scan_id`, `user_id`: Context foreign keys.
  - `action` (String(40)): Action type (e.g. `scan_created`, `assessment_rerun`, `finding_override`).
  - `hash_self` (String(64)): SHA-256 hash over `[hash_prev + user_id + action + timestamp + payload]`.
  - `hash_prev` (String(64)): Hash of previous audit ledger row, creating an unbroken hash chain.
  - `timestamp` (DateTime): UTC server arrival time.

---

## 3. Supporting Security & Operational Entities

- **`RevokedJti` (`revoked_jtis`):** Tracks invalidated JWT token IDs (`jti`) upon explicit logout.
- **`ReportRecord` (`report_records`):** Archive metadata for generated PDF/DOCX inspection dockets and QR digests.
- **`IdempotencyKey` (`idempotency_keys`):** Caches incoming client `Idempotency-Key` headers for 24 hours to guarantee exactly-once processing during offline queue replays; stores worker lease tokens (`status_code: 102`) for concurrent finalization.

---

## 4. Alembic Migration Series (0001–0009)

The repository's authoritative Alembic migration sequence comprises 9 migrations in `Backend/alembic/versions/`:

| Migration ID | Filename | Scope of Schema Changes |
|---|---|---|
| `0001` | `0001_initial_schema.py` | Core seven tables: users, stores, inspections, scans, scan_images, findings, audit_logs. |
| `0002` | `0002_listing_columns.py` | Adds e-commerce fields (`listing_url`, `listing_text`, `platform_has_origin_filter`) to `scans`. |
| `0003` | `0003_reference_pixel.py` | Adds `reference_pixel_size` to `scans` for optical scale reproducibility. |
| `0004` | `0004_token_hygiene.py` | Sanitizes image URLs in `scan_images` to remove raw bearer tokens and query strings. |
| `0005` | `0005_report_records.py` | Adds `report_records` table for immutable PDF/DOCX generation tracking and SHA-256 hashes. |
| `0006` | `0006_idempotency_keys_evidence_retention.py` | Creates `idempotency_keys` table and evidence retention fields. |
| `0007` | `0007_refusal_and_ocr_cache.py` | Adds `signature_status` to `inspections` and `ocr_cache_hash` / `ocr_cache` to `scans`. |
| `0008` | `0008_enforce_not_null_constraints.py` | Enforces strict NOT NULL constraints, foreign keys, and check constraints across tables. |
| `0009` | `0009_add_missing_phase2_to_phase6_columns.py` | Adds LLM metadata (`llm_structured_data`, `llm_cache_hash`, `llm_provider`, `llm_model`, `llm_duration_ms`), inspection sync metrics (`clock_skew_seconds`, `edited_offline`), and finding adjudication columns (`human_verdict`, `effective_verdict`). |

---

## 5. Migration Execution Runbook & Environment Deployment

### 5.1 Fresh Databases (Local / Dev / Test Environments)
- When initializing a clean SQLite database or test instance, SQLAlchemy creates tables via `Base.metadata.create_all(bind=engine)`.
- Alembic migrations use defensive column checks (`IF NOT EXISTS` or exception handling), ensuring that subsequent migration commands run idempotently as safe no-ops on clean schemas without column collisions.

### 5.2 Existing Production Databases (Supabase / PostgreSQL)
To apply pending migrations on an active PostgreSQL database:
```bash
cd Backend
alembic upgrade head
```

Verify migration status and commit history:
```bash
alembic current
alembic history --verbose
```

Run test suite verification post-migration:
```bash
pytest -q
```

### 5.3 SQLite to PostgreSQL Migration Runbook
1. Apply the complete migration chain against the target PostgreSQL `DATABASE_URL`:
   ```bash
   alembic upgrade head
   ```
2. Export and load seed or master store records if required for initial bootstrapping.
3. Configure `DATABASE_URL` in `.env` using the Psycopg 3 scheme:
   ```ini
   DATABASE_URL=postgresql+psycopg://<db_user>:<db_password>@<db_host>:5432/<db_name>?sslmode=require
   ```
4. Verify deployment integrity with the automated test suite:
   ```bash
   pytest -q
   ```

---

## 6. Seed Data Guard & Production Isolation

The seed fixture script (`Backend/seed.py`) populates baseline users (admin and inspectors) and demonstration stores:

- **Environment Restriction:** `seed.py` is strictly reserved for local development and demonstration (`ENV != production` / `ENV == 'dev'`).
- **Production Guard:** Seeding must **never** be executed against production databases (`ENV=production`). Executing seed scripts in production risks inserting synthetic demonstration stores, altering registered officer accounts, and perturbing the cryptographic integrity of append-only audit ledgers.
- **Credential Safety:** The seed script requires `SEED_PASSWORD` to be explicitly supplied via environment variables (`SEED_PASSWORD='...' python seed.py`), ensuring no default or literal passwords are ever committed to version control.
- **Idempotency Invariant:** `seed.py` guards users by `employee_id` and stores by `name`. The inspection corpus is seeded only when the `inspections` table is completely empty, ensuring running the script multiple times does not duplicate fixture records.
