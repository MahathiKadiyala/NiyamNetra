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
  "evidence_low": false,
  "ocr_diagnostics": {
    "provider_mode": "auto",
    "configured_chain": ["google_vision", "ocr_space", "azure"],
    "primary_provider": "google_vision",
    "fallback_providers": ["ocr_space", "azure"],
    "readiness": "ready",
    "providers": {
      "google_vision": {"configured": true, "auth_type": "service_account"},
      "ocr_space": {"configured": true, "auth_type": "api_key"},
      "azure": {"configured": true, "auth_type": "endpoint_key"}
    }
  }
}
```

> [!WARNING]
> **Health Probe Boundary:** A green status on `/health` confirms server liveness, database connectivity, and presence of provider credential definitions. It **does not** prove that external cloud OCR recognition or LLM extraction succeeded on live images.

---

## 3. Pre-Release Verification Checklist (All 7 Release Gates)

Every production release must satisfy all seven mandatory release gates:

- [x] **Gate 1 — Production Rule Catalog Synchronization:** Live backend `/health` endpoint dynamically reports `2026.09.v1`, `rules_as_at: 2026-09-21`, 26 registered checks (19 executable, 7 manual-review), and catalog hash `38e3fd914522429597343f9afbaf0f6ac2f75a671f74221e6c9457f373ef0e9e`.
- [x] **Gate 2 — Full Regression & Test Inventory Verification:**
  - Backend Collection: **598 automated tests collected** across 48 test modules (`pytest --collect-only -q`).
  - Module 11 Targeted Regression: **145 passed, 0 failed, 0 skipped** across 15 critical workflow modules in 96.09s (`pytest -q Backend/tests/...`).
  - Backend Historical Baseline: **428 passed, 0 failed** in 100.43s.
  - Frontend Mobile App: **254 assertions passed, 0 failed** across all 9 suites (`node run_all_tests.js` in `Frontend_App`).
- [x] **Gate 3 — Live Production Smoke Test:** Automated 12-step smoke test passing against live Render backend.
- [x] **Gate 4 — APK Production Configuration Audit:** Direct bytecode inspection confirms zero localhost URLs, zero LAN IPs, and zero exposed API keys or secrets.
- [x] **Gate 5 — APK Identity & Signature Integrity:** Stable package ID `com.niyamnetra.app`, version `1.0.4`, versionCode `5`, cert fingerprint `fac61745...`, 16 KB page-size alignment verified.
- [ ] **Gate 6 — Physical Hardware Install & Upgrade Test:** Packaging configuration verified for ARM64/ARMv7, Android 7.0–16, and 16 KB pages. Physical Android handset execution was **not performed** during automated regression due to no connected physical device (`adb devices` empty). Physical device verification is required prior to field deployment.
- [ ] **Gate 7 — Final Release Verdict:** **`CONDITIONALLY_READY`** (Pending manual OCR.space credential rotation on Render and physical Android device validation).

---

## 4. Hardware Installation, APK Packaging & In-Place Upgrade Procedures

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

### 4.3 EAS Build Pipeline & Supported Release Profiles
The supported build pipeline utilizes Expo Application Services (EAS):
```bash
cd Frontend_App
npx eas build --platform android --profile preview
```
- Both `preview` and `production` profiles in `Frontend_App/eas.json` are configured with `"buildType": "apk"`.
- `cli.appVersionSource` is set to `remote` with `autoIncrement: true`, guaranteeing that every EAS build produces a strictly higher `versionCode` (e.g. version `1.0.4`, `versionCode 5`), preventing Android version downgrade blocks.
- Post-install verification: Navigate to **Settings → Apps → NiyamNetra** to confirm version `1.0.4` (`versionCode 5`).

### 4.4 The Five Root Causes of Android Installation Refusal ("App Not Installed")
When distributing standalone APKs to field devices, installation refusals stem from five concrete root causes:

| # | Cause | Observed Symptom | Remediation |
|---|---|---|---|
| 1 | **Signing Certificate Mismatch** | "App not installed" immediately on devices that already have a prior build installed | Uninstall the existing build first (`adb uninstall com.niyamnetra.app` or via Settings → Apps → Uninstall). Mixing local debug-signed APKs with EAS release-signed APKs causes signature collisions. |
| 2 | **APK Size / Storage Constraints** | "App not installed" on budget handsets with low free flash storage | Enforced ABI filtering in `plugins/withAndroidAbis.js` restricts native binaries to `arm64-v8a` and `armeabi-v7a`, shedding ~44 MB of unnecessary x86/x86_64 emulator binaries and reducing artifact size to ~49 MB. |
| 3 | **Android OS Version Incompatibility** | "App not installed" or "App not compatible with this device" | Expo SDK 54 and React Native 0.81 require **`minSdkVersion: 24` (Android 7.0)**. Devices running Android 6.0 or older cannot run the app. |
| 4 | **OEM Install Lock & Play Protect** | "Install blocked", Play Protect warning banner, or silent installation failure | Enable "Install unknown apps" for the distributing app (e.g. Files, Chrome, WhatsApp). On Xiaomi/MIUI devices, disable "MIUI optimization" if installation hangs; on Vivo/Oppo devices, enable "Install via USB" and allow third-party sources. |
| 5 | **Incomplete File Transfer** | "Package appears to be invalid" or parse error | Re-copy APK and verify cryptographic integrity before distributing to field officers. |

### 4.5 Keystore Management & APK Signature Schemes
- **EAS vs Local Builds:** Local release builds without `NN_RELEASE_*` remain unsigned (or signed with the authoritative keystore when `NN_RELEASE_*` is provided). Release builds NEVER silently fall back to Android's debug keystore. EAS cloud builds sign with the project's managed production keystore. Because Android rejects APK updates whose signing certificate changes, field handsets must receive only builds signed with the authoritative release keystore. Note that `release/NiyamNetra-v1.0.4-release.apk` was legacy debug-signed (`CN=Android Debug`, SHA-256 `fac61745...`); upgrading an existing device from that debug-signed APK to a production-signed APK requires uninstalling the prior build first (`adb uninstall com.niyamnetra.app`).
- **Local Release Signing Configuration:** To sign local Gradle builds with the official release key, configure environment variables:
  ```powershell
  $env:NN_RELEASE_KEYSTORE="C:\path\to\keystore.jks"
  $env:NN_RELEASE_KEYSTORE_PASSWORD="<password>"
  $env:NN_RELEASE_KEY_ALIAS="<alias>"
  $env:NN_RELEASE_KEY_PASSWORD="<password>"
  cd Frontend_App\android; .\gradlew.bat :app:assembleRelease
  ```
- **Signature Schemes (v2 Only):** With `minSdkVersion: 24`, Android Gradle Plugin (AGP) 8.11 signs release APKs using the **v2 scheme only**. Android 7.0+ natively supports v2 verification. If a legacy OEM installer requires v1 JAR signatures, re-sign using Android SDK `apksigner`:
  ```bash
  apksigner sign --ks release.jks --ks-key-alias <alias> --v1-signing-enabled true --v2-signing-enabled true --out app-release-v1signed.apk app-release.apk
  ```

### 4.6 Pre-Distribution APK Verification Runbook
Prior to releasing an APK to field officers, execute these verification commands using Android SDK build-tools:
```bash
# 1. Verify package identity, SDK levels, and native ABIs
aapt2 dump badging release/NiyamNetra-v1.0.4-release.apk | grep -E 'package:|sdkVersion|native-code'
# Expected: package: name='com.niyamnetra.app' versionCode='5' versionName='1.0.4'
# Expected: sdkVersion:'24', targetSdkVersion:'35', native-code: 'arm64-v8a' 'armeabi-v7a'

# 2. Verify certificate and signature scheme integrity
apksigner verify --print-certs -v release/NiyamNetra-v1.0.4-release.apk
# Expected: Verified using v2 scheme: true, Verification successful

# 3. Verify 16 KB page-size alignment (required for Android 15+)
zipalign -c -P 16 -v 4 release/NiyamNetra-v1.0.4-release.apk
# Expected: Verification successful
```

### 4.7 Diagnostic Troubleshooting for Failed ADB Installations
If an Android handset refuses an APK installation over ADB:
```bash
# Query currently installed package version and signatures on handset
adb shell dumpsys package com.niyamnetra.app | grep -E 'versionCode|signatures'

# If refused with INSTALL_FAILED_UPDATE_INCOMPATIBLE (signature collision):
adb uninstall com.niyamnetra.app
adb install -r release/NiyamNetra-v1.0.4-release.apk

# If refused with INSTALL_FAILED_VERSION_DOWNGRADE:
adb install -r -d release/NiyamNetra-v1.0.4-release.apk
```


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

---

## 7. Manual Release Verification Checklist for Packaged Releases

Before cutting a public production release or deploying to enforcement field officers, the release engineering team must execute and sign off on this manual checklist:

| Item | Area | Required Verification Action | Verification Status |
| :--- | :--- | :--- | :--- |
| **M-01** | **Evidence Panel Mapping** | Capture packaging with 3+ panels (`front`, `back`, `mrp_panel`). Verify evidence thumbnails render cleanly in mobile UI, panel badges match, and tapping opens high-resolution modal. | Automated Verified / Hardware Manual Pending |
| **M-02** | **Draft Resumption & Evidence Preservation** | Start draft inspection, upload images, background/kill app, resume from draft list (`GET /inspections?status=draft`). Verify all evidence thumbnails restore without loss. | Automated Verified / Hardware Manual Pending |
| **M-03** | **Live Cloud OCR Connectivity** | Run `python scripts/smoke_ocr_provider.py sample_label.jpg --live-provider-call` with active Google Cloud Vision and OCR.space keys. Confirm exit code 0 and non-empty recognized text. | Manual Verification Required |
| **M-04** | **OCR Fallback & Unreadable Evidence** | Test upload with completely blank/black image. Verify pipeline transitions to `ocr_failed`, findings evaluate honestly to `not_assessed`, and UI displays technical recapture requirement (no false violations). | Automated Verified (`test_e2e_inspection_workflow_regression.py`) |
| **M-05** | **Counter Contract Consistency** | Inspect assessment results on mobile screen. Confirm $\text{total\_findings} = \text{pass} + \text{fail} + \text{not\_assessed}$, and $\text{checks\_assessed} = \text{pass} + \text{fail}$. Confirm findings total reflects applicable checks (up to 19 for full assessments, fewer for partial applicability) and catalog count (26) is never confused with per-scan findings denominator. | Automated Verified (`test_assessment_counting_contract.py`) |
| **M-06** | **Inspection Submission & Idempotency** | Submit inspection via `POST /inspections/{id}/submit`. Verify status transitions to `submitted`. Re-send submission payload; verify server returns cached submission without duplicate database records. | Automated Verified (`test_e2e_inspection_workflow_regression.py`) |
| **M-07** | **Review Queue Adjudication** | Open Portal review queue. Verify unconfirmed violation appears under correct classification (`unconfirmed_violation`). Adjudicate violation via `POST /review/inspections/{id}/adjudicate`. Verify audit log recorded. | Automated Verified (`test_e2e_inspection_workflow_regression.py`) |
| **M-08** | **Render Credential Rotation** | Authorized administrator logs into Render Dashboard, updates `OCR_SPACE_API_KEY` with freshly issued key, and verifies previous exposed key is revoked. | **Pending Administrator Confirmation** |
| **M-09** | **Production APK Signing** | Build release APK using production keystore via EAS (`eas build --platform android --profile release --local`) or local build with `NN_RELEASE_*`. Note: legacy staged APK v1.0.4 carried debug fingerprint `fac61745...`; production builds must use the official production release certificate. | Packaging Configured / Production Build Pending |
| **M-10** | **Physical Android Handset Install** | Connect physical Android handset via ADB (`adb devices`). Install release APK. Verify camera autofocus, auto-exposure, and torch flash operate reliably on packaging in low light. | **Pending Connected Physical Device** |
| **M-11** | **Physical In-Place Upgrade** | Install release APK over an existing v1.0.x installation with local offline SQLite data. Confirm app starts, session remains valid, and offline draft records are preserved. | **Pending Connected Physical Device** |

---

## 8. Database Hygiene & Safe Stale-Record Audit Runbook (`audit_stale_records.py`)

The `Backend/audit_stale_records.py` CLI utility provides an evidence-backed, read-only audit of historical inspection sessions, scans, findings, and evidence artifacts:

### 8.1 Strict Non-Mutating Safety Guarantees
1. **Zero Database Modifications:** Default and only execution mode is strictly read-only. In PostgreSQL, `SET TRANSACTION READ ONLY` is executed; SQLAlchemy session events intercept and block any `before_flush` or write attempts.
2. **No Automatic Purging:** No draft or historical records are automatically deleted, archived, or transitioned. The utility is diagnostic and advisory. Archival decisions require explicit human supervisor approval.
3. **No Credential or PII Exposure:** Connection strings have sensitive passwords masked (`sanitize_url`). Personally identifiable information (e.g. phone numbers, officer notes, raw OCR text dumps) is strictly excluded from output summaries and exported reports.
4. **Authoritative Consistency:** Integrates directly with canonical review queue metrics (`queries.get_canonical_queue_metrics`) to ensure consistent category definitions across endpoints and audits.

### 8.2 Execution Runbook & Command Syntax
Run the audit from the `Backend` directory within the activated virtual environment:

1. **Standard Audit against Local Database:**
   ```bash
   cd Backend
   python audit_stale_records.py
   ```

2. **Audit against Explicit Database Target (Read-Only):**
   ```bash
   python audit_stale_records.py --db-url "sqlite:///niyamnetra.db"
   ```

3. **Customizing Inactive Draft Age Threshold:**
   ```bash
   python audit_stale_records.py --stale-days 30
   ```
   *Default threshold:* **14 days**.

4. **Generating Machine-Readable JSON & Tabular CSV Reports:**
   ```bash
   python audit_stale_records.py --json-out out/audit_report.json --csv-out out/audit_classifications.csv
   ```

5. **Fast Mode (Skip Filesystem Evidence File Checks):**
   ```bash
   python audit_stale_records.py --skip-file-check
   ```

### 8.3 Classification Taxonomy & Operational Semantics
The audit assigns an evidence-backed classification to each inspected record:

| Classification | Target Entity | Criteria & Meaning | Actionable Operational Guidance |
|---|---|---|---|
| `ACTIVE_DRAFT` | Inspection | Draft visit with scans, aged $\le$ stale threshold (default 14 days). | Resumable by field officer via mobile app (`GET /inspections?status=draft`). |
| `POTENTIALLY_STALE_DRAFT` | Inspection | Draft visit with scans, aged $>$ stale threshold without recent activity. | Supervisor review for archival consideration. Age alone does **not** trigger automatic deletion. |
| `DRAFT_WITHOUT_SCANS` | Inspection | Draft visit initialized with 0 packages/scans recorded. | Review for archival; officer created visit but exited without inspecting commodities. |
| `INCOMPLETE_EVIDENCE` | Scan | Scan where checks were unassessed due to missing panels (e.g. back panel missing). | Field revisit or targeted recapture task if inspection remains active. |
| `TECHNICAL_ASSESSMENT_FAILURE` | Scan | `overall_result == 'not_assessed'` due to external OCR provider outage or timeout. | Safe candidate for automated re-assessment retry without field revisit. |
| `RECAPTURE_CANDIDATE` | Scan | Scan with 0 attached images or severe blur/glare defect flags. | Officer targeted recapture task via mobile camera flow. |
| `PENDING_OFFICER_REVIEW` | Scan / Insp | Submitted scan with unconfirmed violations (`engine_verdict == 'fail'`, `human_verdict IS NULL`). | Officer adjudication via supervisory review queue. |
| `CONFIRMED_VIOLATION_AWAITING_ACTION` | Scan / Insp | Submitted scan with confirmed statutory violation (`human_verdict == 'fail'`). | Statutory enforcement notice issuance or compounding under Section 36. |
| `ALREADY_RESOLVED` | Scan / Insp | Terminal resolution recorded in `AuditLog` (`review_resolved`, `review_dismissed`). | Fully resolved; excluded from active officer review queue. |
| `POSSIBLE_TEST_OR_SEED_DATA` | Scan / Insp | Explicit test markers present (e.g. `@example.test` email, `Seed Brand`, synthetic catalog hash `"c"*64`). | Segregate from production analytics. Never classified based solely on record age or ID. |
| `ORPHANED_OR_INCONSISTENT_RECORD` | Record | Inconsistent assessment state (e.g. compliant result with failing checks) or missing disk evidence. | Technical investigation required before taking any action. |
| `NEEDS_MANUAL_CLASSIFICATION` | Record | Incomplete metadata or ambiguous provenance where evidence is insufficient for automated tagging. | Manual legal inspection required prior to disposition. |

### 8.4 Automated Test Suite Verification
The audit tool is verified by an automated test suite:
- **Test Suite Location:** [`Backend/tests/test_audit_stale_records.py`](file:///c:/Skills/Projects/NiyamNetra/Backend/tests/test_audit_stale_records.py)
- **Scenarios Covered:** 18 automated test scenarios verifying read-only database invariants, transaction rollbacks on attempted flush, classification accuracy, URL credential sanitization, and report generation integrity.
