# System Architecture & Technical Design

**Document Code:** `DOC-02`  
**Applies to:** Complete NiyamNetra Platform  
**Architecture Classification:** Cloud-Assisted, Edge-Gated, Offline-Resilient Decision Support System  

---

## 1. Architectural Philosophy & Core Invariant

NiyamNetra is engineered around a strict separation of concerns designed to eliminate AI hallucination, ensure evidentiary integrity, and maintain statutory defensibility.

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                           THE CORE SYSTEM INVARIANT                          │
│                                                                              │
│    OCR READS  →  LLM STRUCTURES  →  RULES DECIDE  →  AGGREGATION ASSESSES   │
│                                                                              │
│                           →  OFFICER ADJUDICATES                             │
└──────────────────────────────────────────────────────────────────────────────┘
```

1. **OCR Reads:** Optical Character Recognition engines transcribe raw text and word bounding boxes without interpreting legal meaning.
2. **LLM Structures:** High-speed cloud language models parse noisy OCR fragments into typed declaration schemas with strict provenance tracking. The LLM never decides compliance.
3. **Rules Decide:** A deterministic statutory rules engine evaluates mathematical, spatial, and boolean logic against statutory thresholds.
4. **Aggregation Assesses:** Package-level logic synthesizes individual checks across multiple photographed panels, computing overall result and actionable capture requests.
5. **Officer Adjudicates:** The authorized human officer reviews the evidence, resolves ambiguities, and signs off on the official record.

---

## 2. End-to-End Pipeline Data Flow

### 2.1 Architectural Flow at a Glance

```text
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│  1. FIELD MOBILE APP    │ ──> │  2. SECURE API GATEWAY  │ ──> │  3. OPTICAL & AI CLOUD  │
│  - Expo SDK 54 Android  │     │  - FastAPI (Python 3.11)│     │  - Cloud Vision OCR     │
│  - Edge Quality Gate    │     │  - Bearer JWT & Devices │     │  - Groq Llama-3.3-70B   │
│  - RAW SHA-256 Storage  │     │  - Idempotent Sync Queue│     │  - Structured JSON DTO  │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
                                                                             │
                                                                             ▼
┌─────────────────────────┐     ┌─────────────────────────┐     ┌─────────────────────────┐
│  6. SUPERVISORY PORTAL  │ <── │  5. PERSISTENCE & AUDIT │ <── │  4. STATUTORY ENGINE    │
│  - Web Review Queue     │     │  - PostgreSQL (Supabase)│     │  - Rule Pack 2026.09.v1 │
│  - Officer Adjudication │     │  - Supabase S3 Storage  │     │  - 26 Statutory Checks  │
│  - Section 36 PDF Docket│     │  - SHA-256 Audit Ledger │     │  - Table-I Font Heights │
└─────────────────────────┘     └─────────────────────────┘     └─────────────────────────┘
```

### 2.2 Detailed Multi-Tier Architectural Diagram

```text
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                             TIER 1: FIELD MOBILE EDGE CLIENT                                │
│                     (React Native · Expo SDK 54 · Standalone Android APK)                   │
├───────────────────────────────┬───────────────────────────────┬─────────────────────────────┤
│      CAMERA VIEWPORT          │     FORENSIC STORAGE          │      EDGE QUALITY GATE      │
│  - Live optical guidance      │  - Raw image bit-for-bit      │  - Blur: Laplacian (>= 60)  │
│  - Multi-panel capture        │  - SHA-256 digest anchor      │  - Glare: Luma <= 15%       │
│  - Reference scale marker     │  - Adaptive JPEG (<=1600px)   │  - Auto-retake guidance     │
└───────────────┬───────────────┴───────────────┬───────────────┴───────────────▲─────────────┘
                │                               │                               │ (Retake)
                ▼                               ▼                               │
┌───────────────────────────────────────────────────────────────────────────────┴─────────────┐
│                          TIER 2: TRANSPORT & OFFLINE RESILIENCE                             │
│                  (Local SQLite Queue · HTTPS Multipart · Render Gateway)                    │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  • Offline SQLite FIFO Queue (UUIDv4 tracking)   • Bearer JWT & Device install_id binding   │
│  • Idempotency-Key header (zero duplicate scans)  • Automatic retry on network reconnection │
└───────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                         TIER 3: OPTICAL & COGNITIVE AI PROCESSING                           │
│     (OpenCV Homography · Cloud OCR Fallback Chain · Groq Llama-3.3-70B / Gemini Flash)      │
├───────────────────────────────┬───────────────────────────────┬─────────────────────────────┤
│    PERSPECTIVE RECTIFY        │   OCR PROVIDER FALLBACK CHAIN │     CLOUD LLM STRUCTURING   │
│  - 4-point contour detection  │  1. Google Vision (Primary)   │  - Zero legal decision role │
│  - Fronto-parallel warp       │  2. OCR.space (Secondary)     │  - Extracts typed JSON      │
│  - Millimeter scale factor    │  3. Azure Vision (Tertiary)   │  - Strict field provenance  │
│  - Derived JPEG analysis crop │  - Outage -> not_assessed     │  - Cached via llm_cache_hash│
└───────────────────────────────┴───────────────┬───────────────┴─────────────────────────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                     TIER 4: DETERMINISTIC STATUTORY RULE ENGINE                             │
│             (Python 3.11 · Rule Pack 2026.09.v1 · Fourth Amendment G.S.R. 826(E))           │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  • 26 Statutory Rules: 19 executable algorithmic checks + 7 manual-review/advisory checks   │
│  • Denominator Contract: checks_total = 19; pass + fail + not_assessed == total == 19       │
│  • Rule 7 Table-I: Millimeter font height validation based on Area of Principal Display     │
│  • Multi-Panel Synthesis: Cross-panel conflict detection, dual pricing, MRP sticker check   │
└───────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                │
                     ┌──────────────────────────┼──────────────────────────┐
                     │ (All 19 Executable Pass) │ (Gaps / Blur / Outage)   │ (Confirmed Defect)
                     ▼                          ▼                          ▼
            ┌─────────────────┐       ┌───────────────────┐       ┌───────────────────┐
            │    COMPLIANT    │       │  REVIEW_REQUIRED  │       │     VIOLATION     │
            │   (Green Path)  │       │   (Amber Path)    │       │    (Red Path)     │
            └────────┬────────┘       └─────────┬─────────┘       └─────────┬─────────┘
                     │                          │                           │
                     │                 ┌────────┴────────┐                  │
                     │                 ▼                 ▼                  │
                     │        ┌─────────────────┐ ┌───────────────┐         │
                     │        │ RECAPTURE TASK  │ │ OFFICER QUEUE │         │
                     │        │ (Missing Panel) │ │ (Web Portal)  │         │
                     │        └────────┬────────┘ └───────┬───────┘         │
                     │                 │                  │                 │
                     │                 ▼                  ▼                 │
                     │          (Feeds Tier 1)    (Officer Verdict)         │
                     │                                    │                 │
                     │                                    ▼                 ▼
┌────────────────────┴──────────────────────────────────────────────────────┴─────────────────┐
│                      TIER 5 & 6: PERSISTENCE, GOVERNANCE & ENFORCEMENT                      │
│                (PostgreSQL · Supabase S3 · Cryptographic Ledger · PDF Docket)               │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  • Immutable engine_verdict + auditable human_verdict with mandatory override justification │
│  • Append-Only Cryptographic Audit Ledger (SHA-256 hash-chain across all state events)      │
│  • Section 36 Violation Dossier: Limb 36(1) Labeling Defects vs Limb 36(2) Short Quantity    │
│  • Official Inspection Docket: High-res PDF/DOCX with QR code linking to verification URL   │
└─────────────────────────────────────────────────────────────────────────────────────────────┘
```

### 2.3 Step-by-Step Data Flow Pipeline

| Stage | Operational Component | Key Actions & Invariants | Evidentiary Output |
|---|---|---|---|
| **1. Edge Optical Capture** | Android APK (`expo-camera`) | Officer points camera at package panels. Quality gate analyzes blur (Laplacian $\ge 60$) and specular glare ($\le 15\%$) in **~38 ms**. | Byte-exact RAW photo stored with SHA-256 digest; normalized analysis JPEG ($\le 1600\text{ px}$). |
| **2. Transport & Ingestion** | Local SQLite + FastAPI Gateway | Persists to offline FIFO queue with client UUIDv4 and `Idempotency-Key`. Transmits via HTTPS with Bearer JWT. | Zero duplicate visits; exactly-once server ingestion. |
| **3. Perspective & OCR** | OpenCV + Google Vision API | Detects 4-point panel quad; applies planar homography warp. High-precision OCR tokenizes words and polygon coordinates. | Fronto-parallel rectified crop + word-level bounding coordinates. |
| **4. Cognitive Structuring** | Groq Llama-3.3-70B | Normalizes raw OCR fragments into typed Pydantic JSON schema with strict provenance. **Zero legal decisions made.** | Typed declaration DTO with exact bounding box citations. |
| **5. Deterministic Evaluation** | Python Rule Pack `2026.09.v1` | Evaluates 26 statutory checks, Rule 7 Table-I millimeter font heights, and multi-panel conflict detection. | Three-state assessment outcome (`COMPLIANT`, `VIOLATION`, `REVIEW_REQUIRED`). |
| **6. Supervisory Enforcement** | React Web Portal + PDF Generator | Generates targeted recapture tasks for missing panels, queues ambiguities for officer review, and creates Section 36 dockets. | Digitally verifiable PDF/DOCX inspection docket with QR verification URL. |

---

## 3. Tiered System Architecture

### 3.1 Client Tier (Tier 1)

#### 1. Inspector Field Mobile App (`Frontend_App`)
- **Framework:** React Native with Expo SDK 54, packaged as a standalone Android release APK (`NiyamNetra-v1.0.4-release.apk`).
- **Target OS:** Android 7.0 (API 24) through Android 16 (API 36); fully aligned for Android 15 16 KB memory page sizes.
- **Hardware Integration:** Direct `expo-camera` access (no gallery picker to eliminate pre-staged photo fraud), `expo-location` for store geofencing, `expo-secure-store` for cryptographic token storage.
- **Client Processing:** Performs instant heuristic quality analysis (blur, glare, luma histogram) and generates the derived analysis JPEG prior to transmission.
- **Offline Engine:** Local SQLite storage with automatic background synchronization via an idempotent retry queue.

#### 2. Supervisory Web Portal (`Frontend_Portal`)
- **Framework:** React 18, Vite 5, Tailwind CSS, Lucide icons, Recharts data visualization.
- **Deployment:** Vercel edge network as a static Single Page Application (PWA).
- **Functionality:** Central dashboard for State Controllers and District Officers. Surfaces raid logs, inspector activity, district-wise violation tallies, the Officer Review Queue, and digital report generation.

---

### 3.2 Application Tier (Tier 2) — FastAPI Backend

- **Runtime:** Python 3.11 running under Uvicorn/Gunicorn on Render web services.
- **Architecture:** Modular FastAPI application with structured routers:
  - `routers/auth.py`: JWT issuance, device installation binding, token rotation.
  - `routers/inspections.py`: Store directory, inspection lifecycle, geofence verification.
  - `routers/scans.py`: Multi-panel image upload, image verification, scan assessment.
  - `routers/review.py`: Officer review queue, finding overrides, cross-panel conflict resolution.
  - `routers/recapture.py`: Generation of targeted panel tasks, fulfillment tracking, reassessment.
  - `routers/enforcement.py`: Section 36 violation dossiers, advisory action recommendations.
  - `routers/reports.py`: Cryptographically signed PDF/DOCX generation with QR verification.
  - `routers/admin.py`: User management, device unbinding, audit chain verification.

---

### 3.3 Data & Storage Tier (Tier 3) — Supabase Infrastructure

- **Database Engine:** PostgreSQL 15+ managed via Supabase AWS session pooler (port 5432).
- **ORM & Migrations:** SQLAlchemy 2.0 with Alembic migration versioning (Migrations 0001 through 0009).
- **Object Storage:** S3-compatible Supabase Storage bucket (`NiyamNetra_Images`) storing raw original photos and derived rectified panel crops.
- **Audit Ledger:** Append-only database table (`audit_logs`) maintaining a SHA-256 hash-chained cryptographic ledger of every scan creation, assessment, override, and sync event.

---

## 4. Optical & AI Pipeline Separation

To ensure absolute reliability in field conditions, the optical and extraction pipeline is divided into clear functional boundaries:

| Layer | Technology | Responsibility | Invariant |
|---|---|---|---|
| **Image Rectification** | OpenCV (`cv2`) | Detects panel contour quadrilateral, calculates homography matrix, rectifies perspective distortion | Deterministic matrix transform; no pixels synthesized |
| **Cloud OCR** | Google Cloud Vision / OCR.space | High-precision optical character recognition, returning line text and word bounding boxes | Raw transcription only; zero legal filtering |
| **Cloud LLM Extraction** | Groq Llama-3.3-70B (Gemini Flash fallback) | Ingests transcribed text blocks and outputs structured JSON conforming to `StructuredDeclarationResult` | Extraction grounded strictly in OCR text; zero legal adjudication |
| **Deterministic Rule Engine** | Python 3.11 (`rules_engine.py` + `rules/`) | Evaluates 26 statutory rules using mathematical, boolean, and geometry logic | 100% reproducible; identical inputs always produce identical findings |

---

## 5. Dual-Verdict & Governance Architecture

A foundational architectural requirement of NiyamNetra is that **automated AI findings can never overwrite human authority, and human decisions can never rewrite algorithmic findings**.

```
                           ┌──────────────────────────┐
                           │   Algorithmic Engine     │
                           └──────────────────────────┘
                                        │
                                        ▼
                           ┌──────────────────────────┐
                           │   engine_verdict (C11)   │
                           │ (pass / fail / not_assessed)
                           │   IMMUTABLE DB RECORD    │
                           └──────────────────────────┘
                                        │
                    ┌───────────────────┴───────────────────┐
                    │                                       │
     Inspector Accepts Finding              Officer Overrides Finding
                    │                                       │
                    ▼                                       ▼
       ┌─────────────────────────┐             ┌─────────────────────────┐
       │   human_verdict = NULL  │             │   human_verdict = pass  │
       │  effective = engine     │             │  effective = human      │
       └─────────────────────────┘             │  mandatory officer_note │
                                               │  cryptographic audit log│
                                               └─────────────────────────┘
```

1. **`engine_verdict`:** Stamped by the deterministic legal rule engine into the `findings` table. Protected by database-level update constraints and code-level immutability.
2. **`human_verdict`:** Stored in a separate column (`findings.human_verdict`) alongside `findings.override_reason` and `findings.overridden_by`.
3. **Effective Verdict:** Dynamically resolved as `effective_verdict = human_verdict if human_verdict is not None else engine_verdict`. Both values are printed transparently in every inspection dossier.

---

## 6. Offline-First Synchronization Architecture

Field officers frequently operate in environments without cellular connectivity. NiyamNetra implements an offline-first architecture with the following guarantees:

1. **Local Authoritative Evidence:** All original images are persisted directly to local storage (`expo-file-system`) and indexed in a local SQLite database before any network call.
2. **Deterministic Enqueueing:** Visits, captures, and adjudications are queued with a cryptographically secure client-generated `Idempotency-Key` (UUIDv4).
3. **Safe Replay:** When network connectivity is restored, the queue worker flushes pending actions in chronological order. The server idempotency cache (`idempotency_keys` table) ensures network retries never create duplicate inspections or scans.
4. **Clock Skew Tracking:** Client timestamps are recorded alongside server arrival timestamps (`clock_skew_seconds`), ensuring offline inspection timelines remain audit-verifiable.

---

## 7. Assessment Performance, Validated Caching & Concurrency

NiyamNetra incorporates multi-tiered caching, derived artifact reuse, and strict concurrency controls developed across Modules 7.1–7.5:

### 7.1 Multi-Tier Assessment Caching & Derived Artifacts
- **Derived Analysis JPEGs:** Heavy image decoding and homography transforms execute once during upload. Downstream OCR and LLM extraction consume the normalized derived JPEG without redundant disk reads.
- **OCR Cache (`ocr_cache_hash`, `ocr_cache`):** Computed as the SHA-256 digest of sorted image records `(panel, sha256)`. If the evidence set is unchanged upon re-assessment, raw cloud OCR calls are skipped and cached token lines are rehydrated in **< 10 ms**.
- **LLM Structured Extraction Cache (`llm_cache_hash`, `llm_structured_data`):** Caches parsed Pydantic entity declarations keyed by OCR text hash and prompt schema version.
- **Assessment Input Fingerprint (`assessment_input_fingerprint`):** Re-assessment fast path verifies complete cryptographic alignment across:
  - Active Rule Pack version (`rule_pack_version`)
  - Prompt and schema versions (`PROMPT_VERSION`, `SCHEMA_VERSION`)
  - Configured OCR provider and fallback chain (`OCR_PROVIDER`, `OCR_PROVIDER_CHAIN`)
  - Configured LLM provider and model name (`LLM_PROVIDER`, `GROQ_MODEL` / `GEMINI_MODEL`)
  - E-commerce listing URL / state
  - Transaction scope flags (imported, perishable, medical device, tobacco, sticker)
  - Physical geometry and optical scale reference (`panel_shape`, dimensions, calibration scale)
  - Sorted multi-panel evidence image IDs, panels, and SHA-256 digests (`{id}:{panel}:{sha256}`)
- **Cache Invalidation Guarantee:** Uploading a new panel image, replacing scale reference, editing scope flags, or modifying provider/rule configuration instantly changes the fingerprint, purges cached findings, and forces fresh evaluation. Failed OCR or LLM results are strictly excluded from persistent success caches.
- **Durable Columns vs In-Memory Fingerprints:** Note that while earlier documentation audit reports informally referenced `ocr_cache_fingerprint` and `assessment_cache_fingerprint`, the actual database columns defined in SQLAlchemy and Alembic migrations (0007 & 0009) on `scans` are `ocr_cache_hash` (String 64) and `llm_cache_hash` (String 64). The end-to-end composite fingerprint is `assessment_input_fingerprint`, computed dynamically via `compute_assessment_fingerprint(scan, images)` and tracked in `_ASSESSMENT_FINGERPRINTS`.


### 7.2 Concurrency Control & Worker Lease Locking
- **In-Flight Assessment Semaphore (`_ASSESS_SEMAPHORE`):** Caps concurrent heavy optical assessments across backend worker threads to prevent out-of-memory (OOM) termination on resource-bounded containers (e.g. Render 512 MB RAM ceiling).
- **Process-Local Concurrency Guard:** `_INSPECTION_SUBMIT_IN_FLIGHT` and `_INSPECTION_SUBMIT_LOCK` prevent duplicate processing of the same inspection across concurrent request threads.
- **Distributed Finalization Lease (`IdempotencyKey`):** Atomically acquires an HTTP 102 (Processing) lease token in the database (`submit_claim_insp_{id}`). Concurrent worker requests poll up to 6.0 seconds for completion or safely return the committed record, preventing race conditions and duplicate audit entries.

---

## 8. Rule Pack Architecture & Arithmetic Counting Contract

### 8.1 Rule Pack Hierarchy (`2026.09.v1`)
The active rule pack baseline (`rules/rule_packs/lmpc_2026_09_v1.json`) incorporates the Legal Metrology (Packaged Commodities) Rules, 2011 amended up to the Fourth Amendment Rules 2026 (G.S.R. 826(E) dated 21.09.2026):
- **26 Registered Catalog Rules:** Total statutory rules cataloged in the versioned rule pack.
- **19 Executable Algorithmic Checks:** Checks executed programmatically during every automated scan assessment (`CHK01` through `CHK18`).
- **7 Manual-Review & Advisory Rules:** Rules addressing discretionary or policy-based checks (such as cosmetic origin symbol enforcement under Rule 6(4A)(d) where statutory enforceability under Section 36(1) is subject to administrative adjudication).

### 8.2 Invariant Counting Contract
The platform maintains strict arithmetic relationships across every scan and inspection verdict:
$$\text{checks\_total} = 19$$
$$\text{checks\_assessed} = \text{passed} + \text{failed}$$
$$\text{not\_assessed} = \text{checks\_total} - \text{checks\_assessed}$$
$$\text{total\_findings} = \text{passed} + \text{failed} + \text{not\_assessed} = \text{checks\_total} = 19$$

A package is stamped `compliant` if and only if:
$$\text{checks\_assessed} = 19 \quad \land \quad \text{failed} = 0 \quad \land \quad \text{not\_assessed} = 0$$

Any missing panel, uncalibrated scale, or optical failure forces $\text{not\_assessed} > 0$, preventing false compliance declarations.

