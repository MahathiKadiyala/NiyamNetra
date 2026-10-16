# Release Operations & Production Runbook

**Document Code:** `DOC-13`  
**Current Production Release:** v1.0.4 (Build Code 5)  
**Release Status:** `RELEASE_READY`  
**Verification Date:** October 2026  

---

## 1. Production Release Identifiers & Artifacts

| Parameter | Authoritative Value |
|---|---|
| **Package ID** | `com.niyamnetra.app` |
| **Version Name** | `1.0.4` |
| **Version Code** | `5` |
| **Signing Scheme** | APK Signature Scheme v2 |
| **Signing Certificate Fingerprint (SHA-256)** | `fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c` |
| **Release APK File Path** | `release/NiyamNetra-v1.0.4-release.apk` |
| **Release APK SHA-256 Digest** | `DB6897E8FB05CA062C3E44839C9605B998F0FD533481F9F8F62C3D141BF366BA` |
| **Android Memory Page-Size Alignment** | 16 KB Page-Size Aligned (`zipalign -c -P 16 -v 4` verified) |
| **Minimum SDK** | Android 7.0 (API Level 24) |
| **Target SDK** | Android 16 (API Level 36) |
| **Supported ABIs** | `arm64-v8a`, `armeabi-v7a` |

---

## 2. Production Service Endpoints & Health Verification

### 2.1 Live Service Endpoints
- **Production Backend API:** `https://niyamnetra-backend.onrender.com`
- **Health Verification Endpoint:** `https://niyamnetra-backend.onrender.com/health`
- **Interactive Swagger Documentation:** `https://niyamnetra-backend.onrender.com/docs`
- **Web Supervisory Portal:** `https://niyamnetra-legal-metrology-rules.vercel.app`
- **Official GitHub Repository:** `https://github.com/Sanjaysanjay31/NiyamNetra-legal-Metrology-Rules-System-`

### 2.2 Health Verification Standard
Executing an HTTP GET against `/health` must return `200 OK` with the exact active production rule pack state:

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
  "evidence_low": false
}
```

---

## 3. Pre-Release Verification Checklist (All 7 Release Gates)

Every production release must satisfy all seven mandatory release gates:

- [x] **Gate 1 — Production Rule Catalog Synchronization:** Live backend `/health` endpoint dynamically reports `2026.09.v1`, `rules_as_at: 2026-09-21`, 26 registered checks (19 executable, 7 manual-review), and catalog hash `38e3fd914522429597343f9afbaf0f6ac2f75a671f74221e6c9457f373ef0e9e`.
- [x] **Gate 2 — Full Regression & Test Inventory Verification:**
  - Backend: **568 automated tests collected** across 35 test files (`pytest --collect-only -q`); recorded release baseline verified **428 passed, 0 failed** (`pytest -q`).
  - Frontend: **228 assertions passed, 0 failed** across all 8 suites (`node run_all_tests.js` in `Frontend_App`).
- [x] **Gate 3 — Live Production Smoke Test:** Automated 12-step smoke test passing against live Render backend.
- [x] **Gate 4 — APK Production Configuration Audit:** Direct bytecode inspection confirms zero localhost URLs, zero LAN IPs, and zero exposed API keys or secrets.
- [x] **Gate 5 — APK Identity & Signature Integrity:** Stable package ID `com.niyamnetra.app`, version `1.0.4`, versionCode `5`, cert fingerprint `fac61745...`, 16 KB page-size alignment verified.
- [x] **Gate 6 — Hardware Install & Upgrade Test:** Clean installation and in-place upgrade verified on physical Android hardware without signature collision or local database loss (see [`Frontend_App/ANDROID_APK_INSTALL.md`](../Frontend_App/ANDROID_APK_INSTALL.md)).
- [x] **Gate 7 — Final Release Verdict:** **`RELEASE_READY`**.

---

## 4. Hardware Installation & In-Place Upgrade Procedures

### 4.1 Fresh Installation via Android Debug Bridge (ADB)
```bash
# Enable USB Debugging on Android device and connect via cable
adb devices

# Install release APK onto clean device
adb install -r release/NiyamNetra-v1.0.4-release.apk

# Launch NiyamNetra application
adb shell monkey -p com.niyamnetra.app -c android.intent.category.LAUNCHER 1
```

### 4.2 In-Place Upgrade Over Previous Versions
```bash
# Verify previous version code
adb shell dumpsys package com.niyamnetra.app | grep versionCode

# Install updated v1.0.4 APK over existing v1.0.x installation
adb install -r release/NiyamNetra-v1.0.4-release.apk
```
*Verification Invariants:*
- Existing local SQLite database must remain intact (no schema reset).
- Queued offline evidence must not be purged.
- Logged-in officer session must remain valid if `token_epoch` has not bumped.

---

## 5. Operational Troubleshooting Runbook

### Issue A: Production `/health` Returns 500 or DB Error
- **Symptom:** `"db_status": "error"` in `/health` payload.
- **Cause:** Supabase connection pooler reached max connection limit, or network latency spike.
- **Remediation:** Verify `DATABASE_URL` uses Supabase session pooler on port 5432 (`?sslmode=require`). Verify connection count in Supabase dashboard.

### Issue B: Officer Mobile App Cannot Authenticate
- **Symptom:** App returns `401 Unauthorized` or network timeout.
- **Cause:** Device installation binding mismatch or expired credentials.
- **Remediation:** Administrator resets device binding via `POST /admin/users/{user_id}/reset-install`. Officer logs in on device to re-bind `install_id`.

### Issue C: Assessment Returns `not_assessed` for Typography Checks
- **Symptom:** Rule 7 Table-I checks not resolving.
- **Cause:** Missing optical calibration reference.
- **Remediation:** Instruct officer to fulfill the generated `CAP_CALIBRATION_REQUIRED` task by photographing the packaging panel with a standard ID card or 5 INR coin in frame.

---

## 6. Live Cloud OCR Provider Smoke Test Runbook

Before distributing release builds or cutting production releases, operations personnel can safely verify outbound cloud OCR connectivity using `Backend/scripts/smoke_ocr_provider.py`:

### 6.1 Prerequisites
- Backend environment dependencies installed (`pip install -r requirements.txt`).
- Active cloud credentials configured in `.env` (or environment variables):
  - `GOOGLE_APPLICATION_CREDENTIALS` or `GOOGLE_VISION_API_KEY`
  - `OCR_SPACE_API_KEY` (optional fallback)
  - `AZURE_VISION_KEY` and `AZURE_VISION_ENDPOINT` (optional fallback)
- A sample packaged product label image (e.g. `test_label.jpg`).

### 6.2 Supported Command Syntax
The smoke test requires the explicit `--live-provider-call` flag to permit external outbound HTTP requests:

```bash
cd Backend
python scripts/smoke_ocr_provider.py <path_to_packaging_image.jpg> --live-provider-call
```

To target a specific provider explicitly:
```bash
python scripts/smoke_ocr_provider.py <path_to_packaging_image.jpg> --live-provider-call --provider google_vision
```

For machine-readable JSON output:
```bash
python scripts/smoke_ocr_provider.py <path_to_packaging_image.jpg> --live-provider-call --json
```

### 6.3 Expected Outcomes & Safety Invariants
- **0 Database Writes:** The smoke test performs zero database inserts or queries; no inspection or scan rows are created.
- **Credential Scrubbing:** API keys and sensitive tokens are strictly scrubbed from terminal outputs.
- **Exit Code 0:** Cloud provider responded, extracted readable text lines, and computed mean optical confidence.
- **Exit Code 1:** Cloud provider chain failed, timed out, or returned zero text.
- **Exit Code 2:** Execution blocked because `--live-provider-call` was omitted.

> **Operational Distinction:** A successful live smoke test validates single-provider image ingestion and line tokenization. It does not certify the downstream LLM entity structuring or deterministic legal rule evaluations, which are verified by the full regression suite.

