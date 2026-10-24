# Security, Privacy & Integrity Architecture

**Document Code:** `DOC-08`  
**Security Standard:** Integrity-Verifiable, Tamper-Evident Evidence Architecture  
**Regulatory Position:** Digital Personal Data Protection Act, 2023 (DPDP Act) Compliant  

---

## 1. Security Philosophy & Legal Realism

NiyamNetra is an inspection decision-support system whose outputs may be used by enforcement officers to initiate compounding proceedings or formal criminal complaints under Chapter V of the **Legal Metrology Act, 2009**.

Because these records face rigorous scrutiny in judicial forums, NiyamNetra rejects superficial claims:
- **Integrity-Verifiable, NOT "Tamper-Proof":** No software system is immune to physical device tampering. NiyamNetra implements **tamper-evident cryptographic chains (SHA-256)** such that any alteration of stored evidence or findings is immediately detectable and provable.
- **Decision-Support, NOT Automatic Evidence Admission:** NiyamNetra does not claim pre-ordained court admissibility. Admissibility remains subject to statutory certificates under Section 65B of the Indian Evidence Act, 1872 / Section 63 of the Bharatiya Sakshya Adhiniyam, 2023.

---

## 2. Threat Model & Control Matrix (T1 – T15)

The platform evaluates and mitigates 15 core threat vectors:

| ID | Threat Vector | Attack Scenario | Mitigation Control | Residual Risk |
|---|---|---|---|---|
| **T1** | Inspector Impersonation | Shared or stolen credentials | Passlib bcrypt hashing (cost 12), device-bound `install_id`, token epoch invalidation. | Coerced login on bound device. |
| **T2** | Evidence Substitution | Modifying image files after upload | RAW camera bytes stored untouched; SHA-256 digest recorded in database; verified on every assess. | Staged physical mock packaging prior to camera capture. |
| **T3** | Database Record Tampering | Direct SQL UPDATE on findings or verdicts | Append-only hash-chained audit ledger (`audit_logs`); database-level update triggers. | Complete database compromise (detectable via broken hash chain). |
| **T4** | Location Falsification | Spoofed GPS mock provider on Android | Server-side haversine geofencing against registered store coordinates. | Mock location with physical store proximity; GPS treated as corroborating. |
| **T5** | Image Re-use Across Visits | Same photo submitted for multiple stores | Perceptual DCT hashing (`phash_bands`) and duplicate resolution query. | Visually identical standard product labels in legitimate consecutive inspections. |
| **T6** | Offline Queue Manipulation | Modifying queued records prior to sync | Single-use client capture UUIDs, immutable local SQLite rows, server timestamp anchoring. | Rooted device with memory modification. |
| **T7** | Report Forgery | Fabricating or editing downloaded PDFs | SHA-256 docket digest embedded in verification QR code linking to live HTTPS backend. | Offline recipient unable to access verification URL. |
| **T8** | Privilege Escalation | Inspector accessing admin routes | Server-side FastAPI dependency injection (`require_admin`, `require_inspector`). | Route misconfiguration (guarded by automated route inventory tests). |
| **T9** | Token Interception | Network sniffing on shared Wi-Fi | HTTPS transport encryption, Bearer JWT in memory only (never written to browser LocalStorage). | Compromised client operating system. |
| **T10** | Storage Data Loss | Server filesystem failure | Managed PostgreSQL database backups and mirrored object storage buckets. | Catastrophic cloud outage. |
| **T11** | Server-Side Request Forgery | Operator enters internal IP for listing check | DNS-resolve sandbox: scheme allowlist (HTTPS only), domain allowlist, private IP blocking. | Allowlisted domain serving malicious content. |
| **T12** | Cross-Site Scripting (XSS) | Malicious OCR text injected into UI | React automatic JSX escaping, strict CSP, no `dangerouslySetInnerHTML`. | Injection into non-React output (mitigated by sanitized PDF templates). |
| **T13** | Insecure Direct Object Ref (IDOR) | Guessing another inspector's inspection ID | Query-level tenant scoping (`Inspection.user_id == current_user.id` for inspectors). | Admin role visibility by design. |
| **T14** | Sync Queue Poisoning | Replaying captured batch requests | Cryptographic `Idempotency-Key` tracking with 24-hour cache deduplication. | None. Duplicate requests return cached response. |
| **T15** | Image Decompression DoS | Uploading decompression bombs | Strict `Content-Length` header check, 25MB file size cap, Pillow `MAX_IMAGE_PIXELS` guard. | Distributed bandwidth exhaustion. |

---

## 3. Cryptographic Evidence Integrity Chain

To guarantee tamper-evident integrity from camera shutter to final PDF docket:

```text
┌─────────────────────────────────┐
│      Camera Shutter Event       │
└────────────────┬────────────────┘
                 │ (Raw Byte Stream)
                 ▼
┌─────────────────────────────────┐
│   Compute Client SHA-256 Digest │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Persist Local original_uuid.jpg │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ HTTPS Multipart Ingest to Cloud │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐
│ Recompute Server SHA-256 Digest │
└────────────────┬────────────────┘
                 │
                 ▼
        ┌─────────────────┐
        │  Hashes Match?  │
        └───┬─────────┬───┘
 (Mismatch) │         │ (Confirmed Match)
            ▼         ▼
┌───────────────────┐ ┌─────────────────────────────────────────┐
│ 422 HTTP Abort    │ │ Store RAW Bytes in Supabase S3          │
│ Upload Integrity  │ └────────────────────┬────────────────────┘
│ Error Recorded    │                      │
└───────────────────┘                      ▼
                      ┌─────────────────────────────────────────┐
                      │ Insert scan_images DB Row with SHA-256  │
                      └────────────────────┬────────────────────┘
                                           │
                                           ▼
                      ┌─────────────────────────────────────────┐
                      │ Append Cryptographic Audit Ledger Entry │
                      │ hash_self = SHA256(hash_prev + action)  │
                      └────────────────────┬────────────────────┘
                                           │
                                           ▼
                      ┌─────────────────────────────────────────┐
                      │ Generate Section 36 PDF Docket with QR  │
                      │ Embedded Verification Link (/reports)   │
                      └─────────────────────────────────────────┘
```

1. **Byte-Exact Immutability:** Stored original images are never re-encoded or stripped of EXIF data.
2. **Re-Verification on Demand (`GET /scans/{id}/verify`):** Re-reads all stored evidence files from disk, recomputes SHA-256 digests in real time, and compares them with the database record.
3. **Cryptographic Hash-Chained Audit Ledger:**
   - Every state transition generates an `audit_logs` record.
   - The ledger entry calculates:
     $$\text{hash\_self} = \text{SHA256}(\text{hash\_prev} \parallel \text{user\_id} \parallel \text{action} \parallel \text{timestamp} \parallel \text{payload})$$
   - Any retro-active alteration of previous entries invalidates all subsequent links in the chain.

---

## 4. Secret Management & Zero-Client-Key Mandate

To prevent credential leaks in decompiled mobile packages:

1. **Client Zero-Secret Verification:**
   - The production APK (`NiyamNetra-v1.0.4-release.apk`) contains **zero** Google Cloud Vision keys, Groq tokens, Gemini credentials, Supabase service keys, or JWT signing secrets.
   - All external AI and database operations execute exclusively within the server-side FastAPI environment.
2. **Server-Side Configuration Enforcement:**
   - Backend `config.py` uses `pydantic-settings` configured with `extra="ignore"` (rather than `"forbid"`). This intentional configuration prevents application startup crashes from cloud platform-injected environment variables (such as `PORT`, `RENDER`, and `RENDER_INSTANCE_ID` on Render), while strictly validating all required secrets, types, and constraints.
   - `JWT_SECRET` has no default value and enforces a minimum length of 32 characters, failing fast at startup if missing or weak.
   - Environment files (`.env`) are strictly excluded from version control via `.gitignore`.

### 4.3 Evidence URL Token Hygiene
- **Bearer Token Sanitization:** Raw authorization tokens and sensitive query parameters are strictly scrubbed from evidence-image URLs before persistence and presentation in the inspector interface (`0004_token_hygiene.py`).
- **Security Rationale & Scope:** Scrubbing prevents bearer credentials from leaking into client browser history, intermediary HTTP caches, screenshot captures, or exported docket logs. However, token removal alone does not guarantee total evidence security; access to evidence storage buckets remains protected by authenticated backend API endpoints and S3 pre-signed URL expiration.

### 4.4 Review Queue Authorization Scoping & Offline Cache Isolation
- **Tenant & Role Cache Scoping:** Offline review queue cache keys are strictly isolated by authenticated principal and authorized jurisdiction: `review_queue:{scope}:{query_hash}`.
- **Account & Role Switching Protection:** If an officer logs out and another logs in, or if an officer's role changes (e.g. from `inspector` to `admin`), cache validation rejects the previous session's cached queue pages, preventing cross-account data leakage.
- **In-Flight Race Condition Guard:** Monotonic session generation counters invalidate late in-flight network responses initiated prior to logout, account switching, or jurisdiction change.
- **Offline Revocation Boundary:** An offline device cannot instantaneously detect server-side credential or permission revocations until network connectivity is restored and the authorization scope is re-verified. Cached review entries are bounded by a strict 24-hour TTL and 30-page storage cap.

### 4.5 Provider Credential Redaction & Operational Diagnostic Hardening
- **Diagnostic Text Sanitization (`sanitize_sensitive_text`):** All error strings, exception messages, upstream provider tracebacks, and operational logs are processed through regex-based sanitization. API keys (`apikey=...`, `key=...`), Authorization Bearer tokens, and sensitive query parameters are replaced with `[REDACTED]` before leaving server memory.
- **Safe Operational Readiness (`get_ocr_health_diagnostics`):** The public `/health` endpoint exposes high-level operational readiness (`unconfigured`, `ready_unverified`, `live_verified`, `degraded`, `all_failed`, `unauthorized`, `billing_error`) and provider candidate status without revealing secret values, file paths, or private credentials.
- **Distinction of Unconfigured vs Failed Providers:** Diagnostics strictly separate providers whose required credentials are completely absent (`unconfigured` / `missing_configuration`) from configured providers that failed at runtime (`live_verified = false`, `last_error_category = auth_error | timeout | all_failed`).
- **Required Manual Security Action:** An authorized administrator must manually rotate the previously exposed OCR.space credential in the Render production environment and verify that the rotated key restores un-degraded fallback readiness.
- **Verification Non-Equivalence:** A healthy `200 OK` response from `/health` is **not** evidence that live cloud OCR, LLM extraction, and deterministic rules have executed successfully end-to-end. External paid OCR calls are intentionally blocked in automated regression suites to prevent quota charges.

### 4.6 Repository Secret Hygiene, Git History Scrubbing & Credential Governance
- **Environment File Isolation:** Live `.env` files containing cryptographic signing keys, database connection strings, and cloud credentials must never be tracked by git. `.gitignore` explicitly enforces the exclusion of all `.env` files.
- **Example Template Practice:** Only `.env.example` is committed to version control, containing placeholder variable names and descriptive format comments with zero live credentials or real secrets.
- **Git Commit History Audits:** Commit history must be regularly audited for accidental credential inclusion (e.g. `git log -p -- ...` or automated scanning tools). If an API key or password is found committed in historical commits:
  1. Immediately revoke and rotate the compromised credential in the upstream cloud provider dashboard.
  2. Purge the sensitive artifact from repository commit history using `git-filter-repo` or `BFG Repo-Cleaner`.
  3. Force-push the sanitized tree and ensure all development environments pull the rewritten history.
- **JWT Secret Rotation Protocol:** In the event of a suspected or scheduled `JWT_SECRET` rotation:
  1. Update `JWT_SECRET` in server environment configuration.
  2. Increment the `token_epoch` column across all active users in the database (`UPDATE users SET token_epoch = token_epoch + 1`), which immediately invalidates all outstanding refresh tokens and forces re-authentication across all mobile devices.

---

## 5. Privacy & DPDP Act 2023 Compliance Position

NiyamNetra complies with the principles of the **Digital Personal Data Protection Act, 2023 (DPDP Act)**:

1. **Purpose Limitation:** The system is exclusively utilized for statutory packaged commodity compliance under the Legal Metrology Act, 2009.
2. **Data Minimization:**
   - No consumer personal data, facial imagery, biometric data, or payment credentials are ever captured or processed.
   - Field camera interfaces restrict capture to packaging labels and calibration standards.
   - Trader contact information captured is strictly limited to publicly displayed business signage and statutory consumer care addresses mandated by law.
3. **Storage Limitation:** Inspection archives and evidence records are subject to statutory retention schedules under the Legal Metrology Rules, with automated archiving after statutory limitation periods.

