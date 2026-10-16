# NiyamNetra — Legal Metrology Compliance Platform

**Automated pre-packaged commodity compliance scanner and decision-support system for the Legal Metrology (Packaged Commodities) Rules, 2011.**  
*Smart India Hackathon 2026 · Problem Statement SIH26034 · Ministry of Consumer Affairs, Food & Public Distribution*

[![Backend API](https://img.shields.io/badge/Backend-FastAPI_0.110-009688?logo=fastapi)](https://niyamnetra-backend.onrender.com)
[![Web Portal](https://img.shields.io/badge/Web_Portal-Vercel_SPA-black?logo=vercel)](https://niyamnetra-legal-metrology-rules.vercel.app)
[![Release APK](https://img.shields.io/badge/Release_APK-v1.0.4_(Build_5)-success?logo=android)](release/NiyamNetra-v1.0.4-release.apk)
[![Rules Pack](https://img.shields.io/badge/Statutory_Pack-2026.09.v1_(26_Rules)-blue)](documents/05_LEGAL_COMPLIANCE_ENGINE.md)
[![Backend Tests](https://img.shields.io/badge/Backend_Tests-568_Collected-009688)](documents/10_TESTING_AND_QUALITY.md)
[![Frontend Tests](https://img.shields.io/badge/Frontend_Tests-228_Passed_(8_Suites)-brightgreen)](documents/10_TESTING_AND_QUALITY.md)

> [!IMPORTANT]
> **The Core System Invariant:**  
> **OCR READS → LLM STRUCTURES → RULES DECIDE → AGGREGATION ASSESSES → OFFICER ADJUDICATES**  
> NiyamNetra assists Legal Metrology enforcement officers by replacing manual calipers and paper checklists with sub-2-second optical scanning, cloud OCR, structured entity normalization, and a strictly deterministic 26-rule statutory engine. The AI never decides the law—it prepares evidence for human officer adjudication.

---

## 1. Problem Statement & The Solution (SIH26034)

### 1.1 The Real-World Challenge (Problem Statement)
Under Chapter II of the **Legal Metrology (Packaged Commodities) Rules, 2011**, all pre-packaged goods sold in India must display mandatory consumer declarations: manufacturer/packer identity, net quantity, Maximum Retail Price (MRP), Unit Sale Price (USP), packing date, consumer care info, and minimum numeral font heights under Rule 7 Table-I.

In the field, Legal Metrology enforcement officers face four critical operational challenges:

1. **Manual Measurement Infeasibility:** Measuring 1.0 mm to 6.0 mm font heights on flexible chips pouches, cylindrical bottles, or crinkled labels using physical vernier calipers takes **15 to 20 minutes per package** and produces high subjective measurement error.
2. **Forensic Evidence Contestation in Court:** When traders are prosecuted under **Section 36 of the Legal Metrology Act, 2009**, defense counsels routinely challenge phone photo authenticity, image compression artifacts, and missing chain-of-custody proof.
3. **Pervasive Evasion Practices:** Deceptive trade practices—such as dual pricing across retail vs e-commerce (Rule 6(2A)), non-compliant stickers pasted over manufacturer MRPs (Rule 6(1)(da)), missing consumer care email addresses, and non-standard quantity units (e.g. `gm` instead of `g`)—frequently go undetected during manual raids.
4. **Harsh Offline Godown Realities:** Field raids often take place in basement storage warehouses, rural weekly mandis, and wholesale market godowns where cellular connectivity is intermittent or completely absent.

### 1.2 The Solution: NiyamNetra Platform
**NiyamNetra** provides an end-to-end, offline-resilient inspection decision-support platform designed specifically for state Legal Metrology departments:

- **Sub-2-Second Automated Turnaround:** End-to-end evaluation completes in a mean latency of **1,598 ms** (p50: 1,547 ms, p95: 1,973 ms), reducing inspection time per package by over 85%.
- **100% Deterministic Legal Rule Engine (Rule Pack `2026.09.v1`):** Completely eliminates LLM legal hallucination. Cloud OCR transcribes text; Cloud LLMs (Groq Llama-3.3-70B) structure noisy text into typed JSON; a deterministic Python engine evaluates **26 registered rules: 19 executable checks and 7 manual-review/advisory checks**, incorporating the Fourth Amendment Rules, 2026 (G.S.R. 826(E)) and the Jan Vishwas Act.
- **Dual-Artifact Forensic Chain of Custody:** High-resolution camera original RAW bytes are preserved untouched with an immutable **SHA-256 digest** and recorded in an append-only cryptographic audit ledger, satisfying strict digital forensic standards.
- **Edge Quality Gate (~38 ms):** Real-time client-side heuristic validation checks Laplacian sharpness (threshold $\ge 60$), specular glare ($\le 15\%$), and edge clipping before transmission, preventing blurred or unreadable uploads.
- **Smart Evidence Recapture Loop:** When side or back panels are missing, the system dynamically generates targeted recapture tasks for the inspector instead of failing open or giving false passes.
- **Section 36 Enforcement Dossiers & PDF Dockets:** Generates official violation dossiers categorizing findings under **Section 36(1)** (labeling defects) vs **Section 36(2)** (short quantity), complete with high-resolution annotated crops, officer notes, and a cryptographic QR verification link.

---

## Table of Contents

1. [Problem Statement & The Solution](#1-problem-statement--the-solution-sih26034)
2. [Key Capabilities](#2-key-capabilities)
3. [System Architecture](#3-system-architecture)
4. [How It Works](#4-how-it-works)
5. [Technology Stack](#5-technology-stack)
6. [Repository Structure](#6-repository-structure)
7. [Quick Start](#7-quick-start)
8. [Production Links](#8-production-links)
9. [APK & Release Information](#9-apk--release-information)
10. [Documentation Map](#10-documentation-map)
11. [Testing & Verification](#11-testing--verification)
12. [Operational Boundaries](#12-operational-boundaries)
13. [License](#13-license)

---

## 2. Key Capabilities

- **Sub-2-Second Turnaround:** Mean end-to-end evaluation latency of **1,598 ms** (p50: 1,547 ms, p95: 1,973 ms).
- **Edge Quality Gate:** Heuristic client-side analysis (sharpness, glare, luma, framing) in ~38 ms, preventing unreadable uploads.
- **Fourth Amendment Currency:** Loaded with rule pack `2026.09.v1` incorporating G.S.R. 826(E) dated 21.09.2026 and the Jan Vishwas Act.
- **Smart Recapture Loop:** Automatically surfaces actionable panel capture requests when packaging evidence is incomplete.
- **Offline-First Synchronization:** Local SQLite persistence with cryptographic `Idempotency-Key` headers for duplicate-free sync upon reconnection.
- **Section 36 Enforcement Dossiers:** Categorizes findings under Section 36(1) (packaging defects) vs Section 36(2) (short quantity).
- **Honest Denominators:** Denominators are always stated explicitly (26 registered rules: 19 executable checks and 7 manual-review/advisory checks). Partial assessments are never marked as compliant passes.

---

## 3. System Architecture

### 3.1 End-to-End Architectural Data Flow

The following pure-markdown schematic illustrates NiyamNetra's complete data journey across all 6 operational tiers—from optical capture in the field to tamper-evident docket export:

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
│              (OpenCV Homography · Google Cloud Vision · Groq Llama-3.3-70B)                 │
├───────────────────────────────┬───────────────────────────────┬─────────────────────────────┤
│    PERSPECTIVE RECTIFY        │        CLOUD OCR ENGINE       │     CLOUD LLM STRUCTURING   │
│  - 4-point contour detection  │  - High-precision lines       │  - Zero legal decision role │
│  - Fronto-parallel warp       │  - Word-level bounding boxes  │  - Extracts typed JSON      │
│  - Millimeter scale factor    │  - Optical confidence scores  │  - Strict field provenance  │
└───────────────────────────────┴───────────────┬───────────────┴─────────────────────────────┘
                                                │
                                                ▼
┌─────────────────────────────────────────────────────────────────────────────────────────────┐
│                     TIER 4: DETERMINISTIC STATUTORY RULE ENGINE                             │
│            (Python 3.11+ · Rule Pack 2026.09.v1 · Fourth Amendment G.S.R. 826(E))          │
├─────────────────────────────────────────────────────────────────────────────────────────────┤
│  • 26 Registered Rules: 19 Executable Checks and 7 Manual-Review / Advisory Checks         │
│  • Rule 7 Table-I: Millimeter font height validation based on Area of Principal Display     │
│  • Multi-Panel Synthesis: Cross-panel conflict detection, dual pricing, MRP sticker check   │
└───────────────────────────────────────────────┬─────────────────────────────────────────────┘
                                                │
                     ┌──────────────────────────┼──────────────────────────┐
                     │ (All 26 Pass)            │ (Gaps / Blur)            │ (Confirmed Defect)
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

### 3.2 Step-by-Step Data Flow Pipeline

| Stage | Operational Component | Key Actions & Invariants | Evidentiary Output |
|---|---|---|---|
| **1. Edge Optical Capture** | Android APK (`expo-camera`) | Officer points camera at package panels. Quality gate analyzes blur (Laplacian $\ge 60$) and specular glare ($\le 15\%$) in **~38 ms**. | Byte-exact RAW photo stored with SHA-256 digest; normalized analysis JPEG ($\le 1600\text{ px}$). |
| **2. Transport & Ingestion** | Local SQLite + FastAPI Gateway | Persists to offline FIFO queue with client UUIDv4 and `Idempotency-Key`. Transmits via HTTPS with Bearer JWT. | Zero duplicate visits; exactly-once server ingestion. |
| **3. Perspective & OCR** | OpenCV + Google Vision API | Detects 4-point panel quad; applies planar homography warp. High-precision OCR tokenizes words and polygon coordinates. | Fronto-parallel rectified crop + word-level bounding coordinates. |
| **4. Cognitive Structuring** | Groq Llama-3.3-70B | Normalizes raw OCR fragments into typed Pydantic JSON schema with strict provenance. **Zero legal decisions made.** | Typed declaration DTO with exact bounding box citations. |
| **5. Deterministic Evaluation** | Python Rule Pack `2026.09.v1` | Evaluates 26 registered rules: 19 executable checks and 7 manual-review/advisory checks, Rule 7 Table-I font heights, and multi-panel conflict detection. | Three-state assessment outcome (`COMPLIANT`, `VIOLATION`, `REVIEW_REQUIRED`). |
| **6. Supervisory Enforcement** | React Web Portal + PDF Generator | Generates targeted recapture tasks for missing panels, queues ambiguities for officer review, and creates Section 36 dockets. | Digitally verifiable PDF/DOCX inspection docket with QR verification URL. |

### 3.3 Architectural Tier Matrix

| Tier | Primary Technologies | Key Responsibility | Evidentiary Invariant |
|---|---|---|---|
| **1. Client & Edge** | React Native, Expo SDK 54, OpenCV heuristic algorithms | Captures packaging panels, stores raw originals, checks blur & glare in ~38 ms | RAW camera bytes preserved byte-for-byte with SHA-256 hash |
| **2. Transport** | HTTPS, Bearer JWT, UUIDv4 Idempotency Keys | Authenticates device binding, buffers offline sync queues | Exactly-once ingestion; no duplicate visits or double scans |
| **3. Optical & AI** | Google Cloud Vision / OCR.space, Groq Llama-3.3-70B | Transcribes text, structures declarations into typed JSON | Grounded strictly in OCR text; zero legal decision-making |
| **4. Statutory Engine** | Python 3.11+ (verified 3.11/3.13), Rule Pack `2026.09.v1` | Evaluates 26 registered rules (19 executable checks and 7 manual-review/advisory checks), Rule 7 Table-I font heights | 100% deterministic code; fail-open on optical ambiguity |
| **5. Persistence** | PostgreSQL 15+ (Supabase), S3 Object Storage | Relational data, Alembic migrations 0001–0009, append-only audit trail | Immutable `engine_verdict`; tamper-evident cryptographic hash chain |
| **6. Governance** | React 18, Vite 5, ReportLab PDF generation | Officer review queue, targeted recapture tasks, Section 36 dockets | AI recommends; authorized human officer decides and signs |

---

## 4. How It Works

1. **Capture:** The inspector captures package panels (Front, Back, MRP/Batch) via the mobile app. Original RAW photos are preserved with SHA-256 hashes.
2. **Quality Validation:** The client quality gate checks blur, glare, and exposure. If unreadable, the officer is guided to retake immediately.
3. **Transcription & Structuring:** High-precision Cloud OCR transcribes text; Cloud LLM (Groq Llama-3.3-70B) normalizes declarations into structured JSON with field-level bounding boxes.
4. **Deterministic Evaluation:** The Python statutory engine evaluates 26 rules against declared geometry and optical calibration references.
5. **Review & Adjudication:** Incomplete panels trigger targeted recapture tasks. Ambiguities appear in the Officer Review Queue.
6. **Docket Generation:** The officer signs off, exporting an official PDF inspection docket featuring high-resolution annotated crops and a cryptographic QR verification link.

---

## 5. Technology Stack

- **Mobile Client:** React Native, Expo SDK 54, `expo-camera`, `expo-secure-store`, Standalone Android APK (ARM64/ARMv7, Android 15 16 KB page-size aligned).
- **Web Portal:** React 18, Vite 5, Tailwind CSS, Lucide icons, Recharts, Responsive PWA.
- **Backend API:** FastAPI (Python 3.11+ supported, Python 3.11 container runtime, Python 3.11/3.13 verified locally), SQLAlchemy 2.0 ORM, Pydantic v2, Uvicorn/Gunicorn.
- **Database & Storage:** PostgreSQL 15+ (Supabase connection pooler), Supabase S3-compatible Object Storage for evidence files.
- **Optical & AI:** Cloud OCR (Google Cloud Vision / OCR.space), Cloud LLM (Groq Llama-3.3-70B / Google Gemini fallback).

---

## 6. Repository Structure

```text
├── README.md                      # Primary repository entry point
├── documents/                     # Authoritative comprehensive documentation
│   ├── 00_PROJECT_BRIEF.md        # 2-minute executive brief
│   ├── 01_PRODUCT_SCOPE.md        # Functional boundaries & personas
│   ├── 02_SYSTEM_ARCHITECTURE.md  # Detailed technical architecture
│   ├── 03_INSPECTOR_WORKFLOW.md   # Field officer user journeys
│   ├── 04_IMAGE_OCR_LLM_PIPELINE.md # Dual-artifact & AI pipeline
│   ├── 05_LEGAL_COMPLIANCE_ENGINE.md# 26 statutory rules & Table-I logic
│   ├── 06_DATA_MODEL.md           # 12 SQLAlchemy models & migrations
│   ├── 07_API_AND_INTEGRATION.md  # REST API specification
│   ├── 08_SECURITY_AND_PRIVACY.md # Threat model & tamper-evident chain
│   ├── 09_OFFLINE_SYNC_AND_EVIDENCE.md # Offline queue & idempotency
│   ├── 10_TESTING_AND_QUALITY.md  # Test suites (568 backend collected, 228 frontend assertions passing)
│   ├── 11_DEPLOYMENT_AND_ENVIRONMENT.md # Deployment runbook & env vars
│   ├── 12_RULE_COVERAGE_AND_LIMITATIONS.md # Rule coverage & limits
│   ├── 13_RELEASE_AND_OPERATIONS.md# Release docket & health checks
│   ├── 14_DEMO_AND_PRESENTATION.md# Presentation script & judge Q&A
│   ├── 15_SAFE_DATA_AUDIT.md      # Read-only stale data audit guide
│   ├── 16_FINAL_PRESENTATION_DECK.md # Stage & jury presentation deck (8 slides)
│   └── archive/                   # Historical engineering records and adversarial audits
├── Backend/                       # FastAPI application & statutory engine
├── Frontend_App/                  # React Native / Expo field mobile application
├── Frontend_Portal/               # React 18 / Vite supervisory web dashboard
└── release/                       # Production release APK artifacts

```

---

## 7. Quick Start

Run the entire platform locally across three terminals:

```bash
# Terminal 1: Backend API
cd Backend
python -m venv niyamnetra_venv
.\niyamnetra_venv\Scripts\Activate.ps1  # Linux/macOS: source niyamnetra_venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                  # Configure DATABASE_URL & JWT_SECRET
alembic upgrade head
python seed.py
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Terminal 2: Web Portal
cd Frontend_Portal
npm install
npm run dev                           # Open http://localhost:5173

# Terminal 3: Mobile Field App
cd Frontend_App
npm install
npx expo start                        # Scan QR with Expo Go
```

---

## 8. Production Links

| Resource | URL |
|---|---|
| **Production Backend API** | `https://niyamnetra-backend.onrender.com` |
| **System Health Endpoint** | `https://niyamnetra-backend.onrender.com/health` |
| **Interactive API Documentation** | `https://niyamnetra-backend.onrender.com/docs` |
| **Web Supervisory Portal** | `https://niyamnetra-legal-metrology-rules.vercel.app` |
| **GitHub Source Repository** | `https://github.com/Sanjaysanjay31/NiyamNetra-legal-Metrology-Rules-System-` |

---

## 9. APK & Release Information

| Parameter | Authoritative Release Specification |
|---|---|
| **Release Artifact** | [`release/NiyamNetra-v1.0.4-release.apk`](release/NiyamNetra-v1.0.4-release.apk) |
| **Package Name** | `com.niyamnetra.app` |
| **Version Name / Code** | `1.0.4` / Build `5` |
| **Signing Scheme** | APK Signature Scheme v2 |
| **Certificate Fingerprint** | `fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c` |
| **APK SHA-256 Digest** | `DB6897E8FB05CA062C3E44839C9605B998F0FD533481F9F8F62C3D141BF366BA` |
| **Page-Size Alignment** | Android 15 16 KB Page-Size Aligned (`zipalign` verified) |

---

## 10. Documentation Map

For detailed architectural, statutory, and operational specifications, consult the comprehensive documentation suite in [`documents/`](documents/):

- [**00_PROJECT_BRIEF.md**](documents/00_PROJECT_BRIEF.md): Executive summary & 2-minute project brief.
- [**01_PRODUCT_SCOPE.md**](documents/01_PRODUCT_SCOPE.md): Functional boundary, personas, and statutory requirements.
- [**02_SYSTEM_ARCHITECTURE.md**](documents/02_SYSTEM_ARCHITECTURE.md): Component diagrams, data flows, and design invariants.
- [**03_INSPECTOR_WORKFLOW.md**](documents/03_INSPECTOR_WORKFLOW.md): Step-by-step field officer and review queue journeys.
- [**04_IMAGE_OCR_LLM_PIPELINE.md**](documents/04_IMAGE_OCR_LLM_PIPELINE.md): Dual-artifact evidence, OCR, and LLM structuring.
- [**05_LEGAL_COMPLIANCE_ENGINE.md**](documents/05_LEGAL_COMPLIANCE_ENGINE.md): 26 statutory rules, Table-I font heights, and Fourth Amendment baseline.
- [**06_DATA_MODEL.md**](documents/06_DATA_MODEL.md): 12 SQLAlchemy models, ER diagram, and Alembic migrations.
- [**07_API_AND_INTEGRATION.md**](documents/07_API_AND_INTEGRATION.md): Complete OpenAPI 3.1 endpoint reference.
- [**08_SECURITY_AND_PRIVACY.md**](documents/08_SECURITY_AND_PRIVACY.md): Threat model (T1–T15), cryptographic evidence chain, and DPDP Act.
- [**09_OFFLINE_SYNC_AND_EVIDENCE.md**](documents/09_OFFLINE_SYNC_AND_EVIDENCE.md): Local SQLite queues, idempotency, and clock skew tracking.
- [**10_TESTING_AND_QUALITY.md**](documents/10_TESTING_AND_QUALITY.md): Test methodology & suites (568 backend collected, 428 release baseline, 228 frontend assertions passing across 8 suites).
- [**11_DEPLOYMENT_AND_ENVIRONMENT.md**](documents/11_DEPLOYMENT_AND_ENVIRONMENT.md): Production deployment runbook & sanitized environment variables.
- [**12_RULE_COVERAGE_AND_LIMITATIONS.md**](documents/12_RULE_COVERAGE_AND_LIMITATIONS.md): Exact statutory coverage and physical constraints.
- [**13_RELEASE_AND_OPERATIONS.md**](documents/13_RELEASE_AND_OPERATIONS.md): Operational runbook, health verification, and release gates.
- [**14_DEMO_AND_PRESENTATION.md**](documents/14_DEMO_AND_PRESENTATION.md): Live demo storyline, key talking points, and evaluator Q&A.
- [**15_SAFE_DATA_AUDIT.md**](documents/15_SAFE_DATA_AUDIT.md): Read-only stale records audit, classification taxonomy, and zero-mutation data hygiene.
- [**16_FINAL_PRESENTATION_DECK.md**](documents/16_FINAL_PRESENTATION_DECK.md): Slide-by-slide stage & jury presentation deck with verified statutory metrics.
- [**archive/**](documents/archive/): Historical engineering baselines and adversarial analysis.


---

## 11. Testing & Verification

NiyamNetra maintains rigorous test coverage across backend and mobile edge clients:
- **Backend Test Suite:** **568 automated tests collected** across 35 test files (`pytest --collect-only -q`). Recorded release baseline verified **428 passed, 0 failed** (`pytest -q`), with 140 regression tests subsequently added across Modules 5.1–7.5 (cache validation, review queue subtypes, counting contracts, provider fallback, and safe data audits).
- **Frontend Test Suite:** **228 assertions passed, 0 failed** across all 8 test suites (`node run_all_tests.js` in `Frontend_App`).
- **E2E Integration Scenarios:** 10 canonical scenarios passing (`test_phase6e_e2e_scenarios.py`).
- **Hardware Verified:** Installed and verified on physical Android hardware across clean install and upgrade flows.

---

## 12. Operational Boundaries

- **Decision-Support Only:** NiyamNetra assists enforcement officers; it never issues self-executing legal notices or fines.
- **Labeling Declarations Only:** Packaging compliance under the LM Rules, 2011. Does not conduct chemical food safety analysis (governed separately by FSSAI).
- **Physical Scale Requirement:** Typography height verification under Rule 7 Table-I strictly requires an optical calibration standard (ID-1 card or coin).

---

## 13. License

Developed for the **Smart India Hackathon 2026 (Problem Statement SIH26034)** under the Department of Consumer Affairs, Ministry of Consumer Affairs, Food & Public Distribution, Government of India.
