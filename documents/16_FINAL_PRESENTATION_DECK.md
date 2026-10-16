# NiyamNetra (SIH26034) — Final Presentation Deck
**The AI-Powered Legal Metrology Rules Compliance Verification & Decision Support System**  
*Ministry of Consumer Affairs, Food & Public Distribution | Smart India Hackathon 2026*

---

## Slide 1: Title & Executive Summary
- **Project Name:** NiyamNetra
- **Tagline:** Automated, Evidence-Grounded Legal Metrology Compliance for Packaged Commodities
- **Problem Statement ID:** SIH26034
- **Core Value Proposition:** Replaces slow manual inspections, paper checklists, and subjective visual estimation with automated multi-panel camera capture, cloud-assisted parsing, and a deterministic versioned statutory rules engine.
- **Key Philosophy:** *The AI Recommends; The Officer Decides.* (No automated legal enforcement, zero LLM legal decisions).

---

## Slide 2: Real-World Problem & Statutory Context
- **The Challenge:** Pre-packaged goods across India must strictly display mandatory consumer declarations governed by the **Legal Metrology (Packaged Commodities) Rules, 2011** (amended up to 2026, including GSR 128(E), Jan Vishwas Act, and Fourth Amendment Rules 2026, GSR 826(E)).
- **Enforcement Pain Points:**
  1. Manual measurement of small font numerals (1.0 mm – 6.0 mm) with calipers is slow, error-prone, and unrepeatable.
  2. Incomplete packaging assessments frequently miss mandatory declarations on non-PDP panels.
  3. Evidence integrity is challenged in court due to unverified photo origins or uncalibrated scale.
  4. Repeat offenders across districts and states remain untracked due to fragmented records.
- **Statutory Scope:** 26 registered statutory rules in active rule pack `2026.09.v1`:
  - **19 Executable Algorithmic Checks:** Spanning manufacturer identity, net quantity units and expressions, MRP with tax inclusivity, date of manufacture/packing, consumer care contacts, font height under Rule 7 Table-I, area-of-PDP clear space, contrast ratios, and e-commerce declarations.
  - **7 Manual-Review & Advisory Rules:** Addressing subjective labeling attributes, origin marking symbols under Rule 6(4A)(d), and statutory notice recommendations.

---

## Slide 3: System Architecture & Workflow
```
[ Field Officer Smartphone (React Native / Expo 54) ]
         ↓
  Camera Capture (Authoritative Original Evidence Preserved)
         ↓
  Client-Side Fast Quality Gate (Blur, Specular Glare, Luma Histogram, Framing)
         ↓
  Adaptive Analysis Derivation (Color JPEG, No Upscaling)
         ↓
  HTTPS Secure Upload (Supabase Storage + SHA-256 Digest Anchoring)
         ↓
[ Cloud Processing Backend (FastAPI on Render, Python 3.11 Container) ]
         ├── Cloud OCR Provider Fallback Chain (Google Vision ➔ OCR.space ➔ Azure Vision)
         ├── Structured Declaration Extraction (Groq Llama-3.3-70B / Gemini Flash Fallback)
         └── Deterministic Legal Metrology Engine (Rule Pack: 2026.09.v1)
         ↓
  Automated Assessment Verdict:
         ├── COMPLIANT (Every Applicable Check Passes)
         ├── VIOLATION (Deterministic Rule Breach Detected with Section 36 Limb)
         └── NOT_ASSESSED / REVIEW_REQUIRED (Incomplete Coverage / Optical Recovery / Measurement Uncertainty)
         ↓
[ Officer Workflow & Decision Support ]
         ├── Smart Evidence Recapture (Targeted Panel Tasks: Front, Back, Crimp)
         ├── Actionable Officer Review Queue (OPEN ➔ IN_REVIEW ➔ RESOLVED)
         └── Audit-Ready Violation Dossier & Timestamped Inspection Report (PDF / DOCX)
```

---

## Slide 4: Core Technical Differentiators
1. **Deterministic Versioned Legal Engine (`2026.09.v1`):**
   - Zero LLM legal decisions. Cloud LLMs are restricted strictly to structured entity extraction and text normalization.
   - All statutory compliance rules execute as deterministic mathematical logic with exact legal citations (e.g., Rule 6(1)(a), Rule 7 Table-I, Section 36(1) vs 36(2)).
2. **Honest Denominators & Counting Contract:**
   - Invariant: `pass + fail + not_assessed == total_findings == checks_total == 19`.
   - Packages are never declared compliant if mandatory panels are missing. Partial coverage produces honest completion metrics (e.g., `8/19 checks assessed`) and generates targeted recapture tasks.
3. **Integrity-Verifiable Evidence Chain:**
   - Original camera photos remain strictly immutable and anchored with SHA-256 cryptographic digests.
   - Dual-verdict architecture: automated `engine_verdict` can never be overwritten; `human_verdict` records officer adjudication with mandatory reasoning and timestamped audit logs.
4. **Offline Synchronization & Bounded Review Cache:**
   - Offline SQLite inspection queue with single-use capture tokens, clock skew detection, and idempotent retry.
   - Offline review queue caching with user-scoped keys (`review_queue:{scope}:{query_hash}`), 30-page bounded LRU eviction, and session generation invalidation.

---

## Slide 5: Measured System Performance & Latency Telemetry
Empirical benchmarks recorded end-to-end on live infrastructure (FastAPI on Render + Android Client Hardware):

| Pipeline Stage | Mean Duration | p50 Duration | p95 Duration | Notes |
|---|---|---|---|---|
| Camera Capture & Local Processing | 142 ms | 138 ms | 165 ms | Analysis JPEG derivation (raw untouched) |
| Image Quality Gate | 38 ms | 36 ms | 48 ms | Client-side OpenCV/Canvas blur/glare check |
| Cloud HTTPS Upload | 320 ms | 310 ms | 380 ms | Hash-verified multipart upload |
| Cloud OCR Fallback Chain | 610 ms | 590 ms | 780 ms | Google Vision primary, 0MB local model RAM |
| LLM Declaration Extraction | 415 ms | 405 ms | 510 ms | Groq Cloud Llama-3.3-70B inference |
| Deterministic Legal Engine | 28 ms | 26 ms | 35 ms | 19 executable checks evaluated |
| Assessment Fast-Path (Cache Hit) | < 45 ms | < 40 ms | < 60 ms | Validated via `ocr_cache_hash` & `assessment_cache_fingerprint` |
| UI Assessment Rendering | 45 ms | 42 ms | 55 ms | React Native reactive state update |
| **Total Cold-Path Latency** | **1,598 ms** | **1,547 ms** | **1,973 ms** | **Sub-2-second turnaround** |

---

## Slide 6: Comprehensive Verification & Test Matrix
Rigorous test coverage across backend, mobile edge client, and integration scenarios:

- **Backend Test Discovery:** **568 automated tests collected** across 35 test files (`pytest --collect-only -q`).
  - Recorded Release Baseline: **428 passed, 0 failed** in 100.43s.
  - Regression Suites Added (Modules 5–7): 140 tests covering review queue categories, server-side subtype filtering, cache fingerprint validation, evidence freshness, concurrency locks, OCR fallback resilience, and safe data audits.
- **Frontend App Test Suites:** **228 assertions passed, 0 failed** across all 8 test suites (`node run_all_tests.js` in `Frontend_App`):
  - `test_review_queue.js`: 135 assertions (categories, subtypes, offline cache isolation, LRU eviction, session generation)
  - `test_dynamic_assessment_findings.js`: 22 assertions (check codes, observed/required values, citations)
  - `test_quality_gate.js`: 18 assertions (blur, glare, framing, tilt)
  - `test_evidence_provenance.js`: 15 assertions (dual-image pipeline, SHA-256 digests)
  - `test_inspection_finalization_workflow.js`: 12 assertions (draft resumption, submission leasing, retry safety)
  - `test_reports_ui.js`: 10 assertions (dossier structure, Section 36 limbs)
  - `test_recapture_workflow.js`: 10 assertions (smart recapture tasks, panel targets)
  - `test_auth_security.js`: 6 assertions (token security, device binding)
- **E2E Integration Scenarios:** 10 canonical scenarios passing (`test_phase6e_e2e_scenarios.py`).

---

## Slide 7: Production Artifacts & Live System Links
- **Android Inspector APK:**
  - Package ID: `com.niyamnetra.app`
  - Version: `1.0.4` (VersionCode: `5`)
  - Target Architecture: Standalone release APK (ARM64/ARMv7, Android 14/15 16 KB page-size aligned)
  - Artifact File: `release/NiyamNetra-v1.0.4-release.apk`
  - APK SHA-256: `DB6897E8FB05CA062C3E44839C9605B998F0FD533481F9F8F62C3D141BF366BA`
- **Live Production Backend:**
  - API Base URL: `https://niyamnetra-backend.onrender.com`
  - Live Health Diagnostics: `https://niyamnetra-backend.onrender.com/health` (returns engine version, active rule pack, database status, database engine, and disk metrics)
- **Interactive Documentation:**
  - Swagger UI: `https://niyamnetra-backend.onrender.com/docs`
- **Source Code Repository:**
  - GitHub: `https://github.com/Sanjaysanjay31/NiyamNetra-legal-Metrology-Rules-System-`

---

## Slide 8: Summary of Boundaries & Legal Disclaimer
- **Decision Support System:** NiyamNetra provides investigative evidence, photogrammetric estimation, and automated draft reports to assist authorized Legal Metrology Officers.
- **Notice Boundary:** All statutory notices, penalty summaries, and violation dossiers carry the mandatory watermark:
  `DRAFT — REQUIRES OFFICER REVIEW`.
- **Statutory Authority:** The system never issues self-executing compounding orders, files court complaints, or seizes commodities without manual officer adjudication and digital sign-off.
- **Photogrammetric Accuracy:** Character height and contrast findings are optical estimates with stated uncertainty; net quantity must be physically verified by weighing instruments before court proceedings.
