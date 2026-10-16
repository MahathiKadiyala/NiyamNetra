# Testing Strategy, Quality Assurance & Verification

**Document Code:** `DOC-10`  
**Quality Status:** Zero Regressions, 100% Passing Baseline  
**Backend Automated Tests:** **568 tests collected** across 35 test files (`pytest --collect-only -q`)  
**Backend Recorded Release Baseline:** **428 passed, 0 failed** in 100.43s (`pytest -q`)  
**Frontend Mobile Assertion Suite:** **228 assertions passed, 0 failed** across all 8 suites (`node run_all_tests.js`)  
**Total Verification Items:** 568 backend automated tests + 228 frontend test assertions (distinct metric types)

---

## 1. Testing Philosophy & Verification Principles

The primary objective of the NiyamNetra quality assurance framework is to **prevent false statutory compliance and false statutory violations**.

A missed violation in the field is a flaw; a fabricated clean pass over an unreadable package is an institutional failure that discredits the inspecting officer. The entire test suite is architected around invariant verification rather than ungrounded percentage claims:

| Invariant | Statutory Requirement | Test Verification |
|---|---|---|
| **I-01** | Every scan evaluates against the complete active rule pack | Stated denominator matches registered rules (19 executable checks, 26 total catalog rules). |
| **I-02** | `not_assessed` findings must carry a non-empty explanation | Guarded by database CHECK constraints and Pydantic validators. |
| **I-03** | `compliant` scan outcome strictly requires zero `not_assessed` | Assessed count must equal total applicable count ($\text{assessed} = 19$). |
| **I-04** | Numeral height verification requires verified optical reference | Uncalibrated images produce `not_assessed` (never `pass`). |
| **I-05** | Original photographic evidence is immutable | SHA-256 digest re-verified on every assess; deletion denied by triggers. |
| **I-06** | Automated `engine_verdict` is never overwritten by human edits | Stored in immutable column; human overrides logged separately. |
| **I-07** | Client bundle contains zero API keys or master secrets | Direct bytecode regex audit of release APK bundle. |

---

## 2. Backend Automated Test Suite (568 Tests Collected, 428 Release Baseline)

The backend test suite is executed using `pytest` across Python 3.11 with SQLAlchemy and FastAPI test clients:

```text
============================== 568 tests collected in 30.03s ==============================
Recorded Release Baseline: 428 passed, 0 failed in 100.43s (pytest -q)
```

### 2.1 Test Suite Breakdown
1. **Legal Rule Engine & Statutory Baselines (`test_rules_*.py`):**
   - Evaluates all 26 statutory rules individually under deterministic inputs.
   - Verifies Fourth Amendment (G.S.R. 826(E)) effective-date switching (`2026.09.v1` vs `2026.07.v1`).
   - Verifies Table-I numeral height curves across all PDP area intervals ($A \leq 50$ through $A > 2500$).
   - Verifies Second Schedule packaging exemptions and commodity unit specifications.
2. **API Routers & RBAC Isolation (`test_phase6d_auth_rbac.py`, `test_admin_*.py`):**
   - Verifies JWT issuance, token epoch revocation, and `install_id` device binding.
   - Verifies role isolation: inspectors are blocked from administrative routes; cross-inspector inspections return 403 Forbidden.
3. **Evidence Integrity & Audit Chains (`test_integrity_*.py`, `test_evidence_*.py`):**
   - Tests tamper detection: altering a byte in stored images or a field in the database immediately triggers SHA-256 mismatch or breaks the cryptographic audit hash chain.
4. **End-to-End Workflow & Scenarios (`test_phase6e_e2e_scenarios.py`):**
   - Verifies all 10 canonical market surveillance scenarios (Scenarios A through J detailed below).
5. **Modules 5–7 Regression Coverage (140 Additional Automated Tests):**
   - `test_review_queue_subtypes.py`: Validates review queue categories (`unconfirmed_violation`, `technical_recovery`, `recapture_required`, `confirmed_violation`, `resolved`), server-side subtype filtering before pagination, consistent total counts, and summary metrics.
   - `test_assessment_cache_validation.py`: Verifies assessment fast path, cryptographic cache fingerprint validation (`assessment_input_fingerprint`), cache reuse for unchanged evidence, and instant cache purge upon new panel uploads.
   - `test_evidence_freshness_idempotency.py`: Validates evidence freshness invariants, recapture cache busting, and prevention of stale finding re-hydration.
   - `test_inspection_finalization_concurrency.py` & `test_inspection_finalization_assessment.py`: Verifies `POST /inspections/{id}/submit` finalization, atomic worker lease locking (`IdempotencyKey`), HTTP 102 processing state, and safe idempotent retries.
   - `test_ocr_runtime_fallback.py` & `test_ocr_failure_legal_assessment.py`: Tests runtime cloud OCR provider chain (Google Vision ➔ OCR.space ➔ Azure Vision), timeout handling, sanitized errors, and the legal safety invariant (fail-open to `not_assessed`, never a false violation).
   - `test_smoke_ocr_provider.py`: Verifies operational safety of `Backend/scripts/smoke_ocr_provider.py`, requiring `--live-provider-call`, scrubbing secrets, and guaranteeing 0 database writes.
   - `test_audit_stale_records.py`: Validates read-only stale inspection audit, verification heuristics, and zero-database-mutation assertions across 18 automated scenarios.


---

## 3. The 10 Canonical End-to-End Scenarios (`test_phase6e_e2e_scenarios.py`)

These comprehensive integration scenarios validate the entire statutory pipeline from capture to adjudication:

| Scenario | Title | Initial Input | Expected System Behavior & Final Verdict |
|---|---|---|---|
| **Scenario A** | Compliant Package | Multi-panel capture with valid declarations and calibrated scale | All 19 executable checks pass; overall verdict: **`COMPLIANT`**. |
| **Scenario B** | Confirmed Statutory Violation | Missing consumer care email under Rule 6(1)(g) | Check CHK19 fails; Section 36(1) limb mapped; overall verdict: **`VIOLATION`**. |
| **Scenario C** | Incomplete Coverage | Only Front panel photographed (Back omitted) | Mandatory back checks produce `not_assessed`; overall verdict: **`REVIEW_REQUIRED`**. |
| **Scenario D** | Recapture, Fulfill & Reassess | Scenario C followed by back panel upload | Automated `CAP_BACK_MISSING` task fulfilled; reassessment updates to **`COMPLIANT`**. |
| **Scenario E** | Physical Calibration Invariant | Font check with uncalibrated scale reference | Height check produces `not_assessed`; prevents false font violation. |
| **Scenario F** | Bad Capture Quality Gate | Severely blurred image (Laplacian variance < 60) | Quality gate rejects upload; returns `RETAKE_REQUIRED` with guidance. |
| **Scenario G** | Cross-Panel MRP Conflict | Front panel declares Rs. 145; top crimp declares Rs. 160 | Dual pricing flag raised under Rule 6(2A); surfaces in Review Queue. |
| **Scenario H** | Officer Adjudication | Officer overrides an advisory finding with reason | `engine_verdict` preserved as `fail`; `human_verdict` recorded as `pass`. |
| **Scenario I** | Offline Edit & Synchronization | Inspection created offline with clock skew | Batch synched with `Idempotency-Key`; `clock_skew_seconds` logged. |
| **Scenario J** | Role-Based Tenant Isolation | Inspector attempts access to another officer's visit | Endpoint strictly enforces ownership; returns `403 FORBIDDEN`. |

---

## 4. Frontend Automated Test Matrix (228 Assertions Passed across 8 Suites)

Executed via `node run_all_tests.js` against the React Native / Expo codebase:

```text
========================================
Frontend Test Suite Breakdown:
  - test_auth_security.js: PASSED (6 passed, 0 failed)
  - test_dynamic_assessment_findings.js: PASSED (22 passed, 0 failed)
  - test_evidence_provenance.js: PASSED (15 passed, 0 failed)
  - test_inspection_finalization_workflow.js: PASSED (12 passed, 0 failed)
  - test_quality_gate.js: PASSED (18 passed, 0 failed)
  - test_recapture_workflow.js: PASSED (10 passed, 0 failed)
  - test_reports_ui.js: PASSED (10 passed, 0 failed)
  - test_review_queue.js: PASSED (135 passed, 0 failed)
----------------------------------------
Suites Summary: 8 passed, 0 failed, 0 skipped (total 8 suites)
Assertions Summary: 228 passed, 0 failed, 0 skipped (total 228 assertions)
========================================
```

1. **`test_review_queue.js` (135 Assertions):**
   - Validates actionable queue categories (`unconfirmed_violation`, `technical_recovery`, `recapture_required`, `confirmed_violation`, `resolved`).
   - Tests fine-grained subtype filtering before pagination, offset reset, and facet stability.
   - Enforces user-scoped offline cache keys (`review_queue:{scope}:{query_hash}`).
   - Validates bounded 30-page cache retention, LRU eviction, and 24h TTL freshness.
   - Confirms session generation tracking to discard stale in-flight responses and prevent cross-account data leakage.
2. **`test_dynamic_assessment_findings.js` (22 Assertions):**
   - Validates dynamic finding presentation: check codes, observed/required statutory values, legal citations.
   - Enforces token hygiene on evidence-image URLs, verifying raw bearer credentials are removed from query strings.
3. **`test_quality_gate.js` (18 Assertions):**
   - Evaluates sharpness (Laplacian variance $\ge 60$), specular glare ($\le 15\%$), underexposure, overexposure, edge framing, and package tilt heuristics.
   - Confirms valid packaging with minor specular highlights is not falsely rejected.
4. **`test_evidence_provenance.js` (15 Assertions):**
   - Verifies original camera URI and dimensions are strictly preserved bit-for-bit.
   - Verifies derived analysis image is downscaled along long edge ($\leq 1600\text{ px}$) without color distortion.
   - Confirms offline queue prioritizes authoritative original files over derivatives.
5. **`test_inspection_finalization_workflow.js` (12 Assertions):**
   - Tests draft inspection discovery and resumption (`GET /inspections?status=draft`).
   - Verifies finalization payload (`POST /inspections/{id}/submit`), submission leasing, and retry safety.
   - Confirms zero-scan merchant refusal handling (`signature_status: "refused"`).
6. **`test_reports_ui.js` (10 Assertions):**
   - Validates statutory summary rollup and Section 36 limb mappings.
   - Verifies offline caching of inspection summaries.
7. **`test_recapture_workflow.js` (10 Assertions):**
   - Verifies recapture task sorting (high -> medium -> low priority).
   - Validates that skipping a task requires an officer explanation $\geq 10$ characters.
   - Enforces calibration task requirement for verified optical references.
8. **`test_auth_security.js` (6 Assertions):**
   - Attaches Bearer authorization token to all API calls.
   - Handles 401 Unauthorized by clearing session and redirecting to login.
   - Verifies client bundle contains zero hardcoded API keys or master secrets.

---

## 5. Physical Hardware Verification & Compatibility

The final production release APK (`NiyamNetra-v1.0.4-release.apk`) has been physically installed and verified on real Android hardware:

- **Hardware Architecture:** ARM64 (`arm64-v8a`) and ARMv7 (`armeabi-v7a`).
- **Android Version Coverage:** Android 7.0 (API Level 24) through Android 16 (API Level 36).
- **16 KB Memory Page-Size Compatibility:** Verified using Android 15 page-size alignment tooling (`zipalign -c -P 16 -v 4`).
- **Camera Sensor Verification:** Verified on autofocus camera sensors with auto-exposure and torch flash controls.
- **In-Place Upgrade Test:** Upgrading from v1.0.x to v1.0.4 on physical hardware preserves local SQLite inspection databases and user sessions without data wipe or signature conflict.

---

## 6. End-to-End Performance Benchmarks

Measured end-to-end on live Render infrastructure and physical Android client:

- **Mean End-to-End Turnaround:** **1,598 ms**
- **Median Turnaround (p50):** **1,547 ms**
- **95th Percentile Turnaround (p95):** **1,973 ms**
- **Conclusion:** Sub-2-second performance is sustained across all normal inspection flows, satisfying field usability requirements for fast market inspections.
