# REST API Specification & Integration Contract

**Document Code:** `DOC-07`  
**Base Production URL:** `https://niyamnetra-backend.onrender.com`  
**Interactive API Documentation:** `https://niyamnetra-backend.onrender.com/docs` (OpenAPI 3.1)  
**Authentication Standard:** HTTP Bearer JSON Web Tokens (RFC 7519)  

---

## 1. Global API Conventions & Headers

1. **Authentication Header:**
   ```http
   Authorization: Bearer <access_token>
   ```
2. **Idempotency Key Header (Required for POST/PATCH state modifications):**
   ```http
   Idempotency-Key: <uuid-v4>
   ```
   Ensures that retried offline requests do not create duplicate inspection records or re-execute assessments.
3. **Standard HTTP Error Responses:**
   - `401 Unauthorized`: Token missing, expired, revoked, or signature invalid.
   - `403 Forbidden`: Insufficient role (e.g. inspector attempting admin action) or accessing another officer's unsubmitted inspection.
   - `404 Not Found`: Target resource does not exist.
   - `409 Conflict`: Inspection submitted and frozen, or concurrent modification.
   - `422 Unprocessable Entity`: Schema validation error.

---

## 2. Authentication API (`/auth`)

### `POST /auth/login`
Authenticates an officer or administrator and binds the session to the client device.
- **Auth Required:** None (Public).
- **Rate Limit:** 5 failed attempts per minute per IP.
- **Request Body:**
  ```json
  {
    "employee_id": "LM-TG-1042",
    "password": "<officer-password>",
    "install_id": "a8f7c9e012345678abcdef0123456789"
  }
  ```
- **Response (`200 OK`):**
  ```json
  {
    "access_token": "eyJhbGciOiJIUzI1Ni...",
    "token_type": "bearer",
    "expires_in": 43200,
    "user": {
      "id": 1,
      "employee_id": "LM-TG-1042",
      "full_name": "Inspector One",
      "role": "inspector",
      "is_active": true
    },
    "install_id": "a8f7c9e012345678abcdef0123456789"
  }
  ```

### `GET /auth/me`
Retrieves authenticated profile, role, and jurisdiction.
- **Auth Required:** Bearer Token (`inspector` or `admin`).
- **Response (`200 OK`):** User profile object.

### `POST /auth/logout`
Revokes active access token JTI and terminates device session.
- **Auth Required:** Bearer Token.
- **Response (`204 No Content`)**

---

## 3. Stores & Inspections API (`/stores`, `/inspections`)

### `GET /stores`
Returns registered store directory. Cached for 5 minutes with automatic invalidation.
- **Auth Required:** Bearer Token.
- **Query Params:** `q` (name filter), `district`, `limit` (default 50).
- **Response (`200 OK`):** Array of store objects with geofence coordinates.

### `POST /inspections`
Creates a new field inspection visit at a registered store.
- **Auth Required:** Bearer Token (`inspector`).
- **Request Body:**
  ```json
  {
    "store_id": 2,
    "transaction_type": "retail_sale",
    "latitude": 17.385044,
    "longitude": 78.486671,
    "gps_accuracy_m": 8.5,
    "notes": "Routine market surveillance visit"
  }
  ```
- **Response (`201 Created`):**
  ```json
  {
    "id": 70,
    "store_id": 2,
    "status": "draft",
    "geofence_status": "verified",
    "inspection_date": "2026-10-05"
  }
  ```

### `GET /inspections`
Lists inspection sessions for the authenticated officer or all sessions for admins.
- **Auth Required:** Bearer Token (`inspector` or `admin`).
- **Query Params:**
  - `status`: Optional filter (`draft` | `submitted`). When set to `draft`, allows discovering and resuming incomplete field visits.
  - `store_id`: Filter by integer store ID.
  - `date_from`, `date_to`: ISO 8601 date range filters.
  - `q`: Free-text search matching store name, commodity generic, brand, or batch number.
  - `category`: Commodity category filter (e.g. `edible_oils`, `packaged_food`).
  - `area`: City or district text filter.
- **Response (`200 OK`):** Array of inspection summary objects.

### `POST /inspections/{id}/submit` (Inspection Finalization)
Submits, evaluates, and freezes an inspection session docket.

> **Endpoint Verification Note:** While historical design notes and audit reports informally referred to `POST /inspections/{id}/finalize`, the actual authoritative endpoint registered in `Backend/routers/inspections.py` (line 898) and invoked by the field application (`Frontend_App/api/inspections.js` line 97) is `POST /inspections/{id}/submit`. No `/finalize` route exists; finalization is the core lifecycle state machine executed by `/submit`.

- **Auth Required:** Bearer Token (inspection owner or admin).
- **Concurrency & Idempotency:**
  - Cross-worker lease lock via `IdempotencyKey` table (`submit_claim_insp_{id}`, status code 102). Concurrent requests wait up to 6.0 seconds for completion or safely return the committed inspection.
  - Re-tries are safe and idempotent: returns persisted inspection record without double-evaluating.

- **Request Body:**
  ```json
  {
    "signature_status": "signed",
    "notes": "Trader acknowledged findings and accepted inspection summary"
  }
  ```
  *(Note: In merchant refusal scenarios with zero scans, `signature_status` is set to `"refused"`).*
- **Response (`200 OK`):**
  ```json
  {
    "id": 70,
    "status": "submitted",
    "signature_status": "signed",
    "overall_result": "compliant",
    "finalization_summary": {
      "status": "submitted",
      "total_scans": 2,
      "scans_evaluated": [46],
      "scans_reused": [45],
      "scans_pending_evidence": [],
      "scans_failed": [],
      "scans_blocked": [],
      "violations_count": 0,
      "compliant_count": 2,
      "not_assessed_count": 0
    }
  }
  ```

---

## 4. Scans & Evidence Capture API (`/inspections/{id}/scans`, `/scans`)

### `POST /inspections/{inspection_id}/scans`
Registers a new packaged commodity scan within an active inspection.
- **Auth Required:** Bearer Token.
- **Request Body:**
  ```json
  {
    "commodity_generic": "Sunflower Oil",
    "brand_name": "Fortune",
    "commodity_category": "edible_oils",
    "batch_number": "B2026-99",
    "geometry": {
      "panel_shape": "rectangular",
      "panel_height_mm": 220.0,
      "panel_width_mm": 110.0,
      "scale_source": "declared"
    }
  }
  ```
- **Response (`201 Created`):** Scan object with `id` and `checks_total = 19`.

### `POST /scans/{scan_id}/images`
Attaches an immutable panel photograph to an existing scan.
- **Auth Required:** Bearer Token.
- **Content-Type:** `multipart/form-data`.
- **Form Fields:** `panel` (`front`, `back`, `side`, `mrp`, `batch`), `file` (Binary JPEG/PNG).
- **Response (`201 Created`):**
  ```json
  {
    "image_id": 142,
    "sha256": "3a8b...f12",
    "usable": true,
    "quality_note": "optimal",
    "rectified": true,
    "replayed": false
  }
  ```

### `POST /scans/{scan_id}/assess`
Executes Cloud OCR, Cloud LLM structuring, and all 19 executable statutory rule evaluations.
- **Auth Required:** Bearer Token.
- **Caching & Fast-Path:** Computes `assessment_input_fingerprint`. If evidence and configurations are identical, rehydrates cached findings in < 45 ms.
- **Response (`200 OK`):**
  ```json
  {
    "id": 46,
    "overall_result": "compliant",
    "violation_limb": null,
    "checks_total": 19,
    "checks_assessed": 19,
    "rule_pack_version": "2026.09.v1",
    "catalog_hash": "38e3fd914522429597343f9afbaf0f6ac2f75a671f74221e6c9457f373ef0e9e",
    "findings": [
      {
        "check_id": "CHK01",
        "title": "Mandatory declarations under Rule 6(1)",
        "engine_verdict": "pass",
        "human_verdict": null,
        "effective_verdict": "pass",
        "severity": "critical",
        "citation": "Rule 6(1), LM (PC) Rules 2011",
        "confidence": 0.94
      }
    ],
    "diagnostics": {
      "assessment_input_fingerprint": "a9f8e7...",
      "ocr_provider": "google_vision",
      "execution_time_ms": 1582.4
    }
  }
  ```

---

## 5. Review Queue API (`/review`)

### `GET /review/queue`
Retrieves pending review items requiring officer adjudication.
- **Auth Required:** Bearer Token (`inspector` or `admin`).
- **Submission Guard:** Only submitted/finalized inspections enter the queue (`Inspection.status == "submitted"`). Active field drafts are strictly excluded.
- **Query Params:**
  - `status_filter`: `OPEN`, `IN_REVIEW`, `RESOLVED`, `ALL` (default `OPEN`).
  - `reason`: Coarse reason category (`not_assessed`, `violation`, `low_confidence`, `offline_edit`, `conflict`, `all`).
  - `subtype` / `subtype_filter`: Fine-grained category subtype:
    - `technical_recovery`: Transient OCR or provider network outage; eligible for safe reassessment.
    - `recapture_required`: Missing package panels or uncalibrated geometry.
    - `incomplete_coverage`: Unassessed checks due to missing evidence.
    - `unconfirmed_violation`: Algorithmic rule breaches awaiting officer adjudication.
    - `confirmed_violation`: Confirmed breaches awaiting formal notice generation.
    - `low_confidence`: Findings with optical confidence < 60%.
    - `offline_edit`: Visits edited offline with clock skew tracking.
    - `all`: All reviewable subtypes.
  - `area`: Filter by store city or district.
  - `priority`: `high`, `medium`, `low`.
  - `date_from`, `date_to`: ISO 8601 inspection date range.
  - `limit`: Integer (1 to 500, default 100).
  - `offset`: Integer (default 0).
- **Response (`200 OK`):**
  ```json
  {
    "total": 14,
    "offset": 0,
    "limit": 100,
    "summary": {
      "open": 8,
      "in_review": 4,
      "resolved": 2,
      "by_priority": {
        "high": 5,
        "medium": 7,
        "low": 2
      }
    },
    "items": [
      {
        "inspection_id": 70,
        "scan_id": 46,
        "overall_result": "violation",
        "review_status": "OPEN",
        "priority": "high",
        "reasons": [
          {
            "type": "violation",
            "subtype": "unconfirmed_violation",
            "severity": "critical",
            "detail": "1 unconfirmed violations awaiting officer review",
            "affected_checks": ["CHK06"]
          }
        ],
        "findings": [...]
      }
    ]
  }
  ```

### `GET /review/queue/summary`
Returns database-authoritative summary metrics for the review queue.
- **Auth Required:** Bearer Token (`inspector` or `admin`).
- **Query Params:** `area`, `status_filter`.
- **Response (`200 OK`):** Total open, in-review, resolved, and priority tallies.

### `GET /review/inspections/{inspection_id}`
Returns detailed review docket for a specific inspection, including multi-panel image links, assessment aggregation, and recent audit history.
- **Auth Required:** Bearer Token (owner or admin).
- **Response (`200 OK`):** Detailed review object with per-scan findings and audit history.

### `POST /review/inspections/{inspection_id}/adjudicate`
Records an authorized officer adjudication on an inspection or individual finding without altering `engine_verdict`.
- **Auth Required:** Bearer Token (`inspector` or `admin`).
- **Path Parameter:** `inspection_id` (integer).
- **Request Body:**
  ```json
  {
    "action": "override",
    "reason": "Manufacturer registered with DCA under central exemption ref 2026/LMA/12",
    "finding_id": 214,
    "human_verdict": "pass",
    "resolution_notes": "Verified exemption documentation presented by store manager"
  }
  ```
  *(To resolve an inspection at the docket level, set `"action": "resolve"` with a mandatory `"reason"` $\ge 10$ characters).*
- **Response (`200 OK`):** Updated finding or inspection adjudication summary with cryptographic audit record.

---

## 6. Smart Recapture API (`/recapture`)

### `GET /recapture/inspections/{inspection_id}/tasks`
Surfaces actionable capture requests generated by assessment gaps.
- **Auth Required:** Bearer Token.
- **Response (`200 OK`):** Array of tasks:
  ```json
  [
    {
      "request_id": "CAP_70_46_back_missing",
      "target_panel": "back",
      "reason": "Panel 'back' not captured. Required for Rule 6(1)(f) consumer care.",
      "priority": "high",
      "status": "pending"
    }
  ]
  ```

### `POST /recapture/inspections/{inspection_id}/reassess`
Re-runs statutory evaluation after new panel photos fulfill pending tasks.
- **Auth Required:** Bearer Token.
- **Request Body:**
  ```json
  {
    "scan_ids": [46]
  }
  ```
- **Response (`200 OK`):** Re-assessment results array.

---

## 7. Enforcement & Reports API (`/enforcement`, `/reports`, `/health`)

### `GET /enforcement/summary/{inspection_id}`
Compiles an enforcement dossier classifying findings under Section 36 limbs.
- **Auth Required:** Bearer Token.
- **Response (`200 OK`):** Summary payload with package counts, Section 36 guidance, and recommended actions.

### `GET /reports/inspections/{inspection_id}/pdf`
Generates cryptographically signed official PDF inspection docket.
- **Auth Required:** Bearer Token.
- **Response (`200 OK`):** Binary `application/pdf` stream.

### `GET /health`
Public system health, database engine status, active statutory rule pack diagnostics, and sanitized OCR operational readiness.
- **Auth Required:** None (Public).
- **Response (`200 OK`):**
  ```json
  {
    "status": "ok",
    "engine_version": "2.0.0",
    "rule_pack_version": "2026.09.v1",
    "active_rule_pack_version": "2026.09.v1",
    "rules_as_at": "2026-09-21",
    "catalog_hash": "38e3fd914522429597343f9afbaf0f6ac2f75a671f74221e6c9457f373ef0e9e",
    "checks_registered": 26,
    "catalog_rule_count": 26,
    "total_rules": 26,
    "executable_check_count": 19,
    "implemented_checks": 19,
    "manual_review_rules": 7,
    "coverage_summary": "26 registered catalog rules, 19 algorithmic checks implemented for automated assessment",
    "db_status": "ok",
    "db_engine": "postgresql",
    "db_error": null,
    "evidence_free_gb": 42.85,
    "evidence_low": false,
    "ocr_diagnostics": {
      "provider_selection_mode": "auto",
      "selected_provider": "auto",
      "configured": true,
      "live_verified": false,
      "readiness": "ready_unverified",
      "verification_status": "configured_not_live_verified",
      "verification_source": "none",
      "fallback_occurred": false,
      "last_error_category": null,
      "last_attempted_providers": [],
      "providers": [
        {"name": "google_vision", "configured": true, "priority": 1, "last_status": "unverified"},
        {"name": "ocr_space", "configured": true, "priority": 2, "last_status": "unverified"},
        {"name": "azure", "configured": false, "priority": 3, "last_status": "unverified"}
      ],
      "coverage_note": "OCR configuration does not guarantee live cloud service reachability"
    }
  }
  ```

> **Operational Health Boundary:** A `200 OK` status from `/health` confirms backend service availability, database connectivity, and configuration presence. It does **not** prove that external cloud OCR, LLM extraction, or legal rules have been verified through live external provider calls. Real external calls are intentionally decoupled to prevent quota exhaustion and require separate manual verification.
