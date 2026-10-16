# NiyamNetra — Executive Project Brief

**Project Name:** NiyamNetra  
**Problem Statement Identifier:** SIH26034  
**Title:** Software System to check compliance of Packaged Commodities under Legal Metrology (Packaged Commodities) Rules, 2011 by scanning products, images and labels  
**Organization:** Ministry of Consumer Affairs, Food & Public Distribution  
**Department:** Department of Consumer Affairs (Legal Metrology Division)  
**Current Release Status:** `RELEASE_READY` (v1.0.4, Build Code 5)  

---

## 1. Executive Summary

NiyamNetra is a cloud-assisted, offline-resilient inspection decision-support platform designed for Legal Metrology field officers and central enforcement administrators. It automates the verification of pre-packaged commodities and e-commerce listings against the **Legal Metrology (Packaged Commodities) Rules, 2011** (as amended up to the Fourth Amendment Rules, 2026 under G.S.R. 826(E) dated 21.09.2026 and the Jan Vishwas Act).

By replacing slow manual calipers, paper checklists, and subjective visual estimates with high-precision optical capture, cloud OCR, structured schema normalization, and a strictly deterministic statutory rule engine, NiyamNetra reduces inspection turnaround time from 15+ minutes per package to **under 2 seconds**.


```text
┌──────────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│ 1. Mobile Camera     │ ──> │ 2. Edge Quality Gate │ ──> │ 3. Cloud OCR         │
│ Immutable RAW SHA-256│     │ Blur & Glare Filter  │     │ Google Vision API    │
└──────────────────────┘     └──────────────────────┘     └──────────────────────┘
                                                                     │
                                                                     ▼
┌──────────────────────┐     ┌──────────────────────┐     ┌──────────────────────┐
│ 6. Officer & Docket  │ <── │ 5. Rule Engine       │ <── │ 4. Cloud LLM         │
│ PDF Docket + QR Link │     │ 26 Rules (19+7)      │     │ Groq Llama-3.3-70B   │
└──────────────────────┘     └──────────────────────┘     └──────────────────────┘
```

---

## 2. Problem Statement & Real-World Need

Pre-packaged goods across India are governed by Chapter II of the Legal Metrology (Packaged Commodities) Rules, 2011. Every retail package must display mandatory declarations:
- Manufacturer, packer, or importer identity and complete address
- Generic or common commodity name
- Net quantity (standard metric units conforming to Rules 12 & 13)
- Maximum Retail Price (MRP) inclusive of all taxes and unit sale price (USP)
- Month and year of manufacture, packing, or import
- Consumer care details (name, address, telephone, email)
- Principal Display Panel (PDP) numeral height adhering strictly to Rule 7 Table-I

### Key Challenges in the Field:
1. **Measurement Difficulty:** Field officers cannot accurately measure 1.0 mm to 6.0 mm font heights on curved, flexible, or glossy packages with physical calipers.
2. **Incomplete Coverage:** Many packaging violations occur on side, back, or crimp panels that single-photo systems fail to capture.
3. **Evidence Authenticity:** In prosecutions under Section 36 of the Legal Metrology Act, 2009, traders frequently contest evidence origin and measurement validity.
4. **Offline Conditions:** Enforcement inspections often take place in basement godowns, wholesale mandis, and rural kirana stores with zero cellular connectivity.

---

## 3. Core Capabilities & Architecture

| Capability | Technical Realization | Architectural Benefit |
|---|---|---|
| **Integrity-Preserving Capture** | Dual-image pipeline: Authoritative camera original preserved untouched with SHA-256 hash; lightweight derived analysis JPEG used for inference | Eliminates evidence tampering claims; satisfies forensic authenticity standards |
| **Edge Quality Gate** | Client-side OpenCV/Canvas heuristic analysis (Laplacian blur, specular glare, luma histogram, edge clipping) | Prevents unreadable images from wasting cloud bandwidth or producing false findings |
| **Separation of Concerns** | **OCR Reads → LLM Structures → Rules Decide → Aggregation Assesses** | Zero LLM legal hallucination; statutory logic is 100% deterministic, auditable, and versioned |
| **Deterministic Rule Engine** | 26 registered rules: 19 executable checks and 7 manual-review/advisory checks evaluated mathematically against declared packaging geometry and physical calibration | Transparent legal citations, exact statutory limb attribution (Section 36(1) vs 36(2)) |
| **Dual-Verdict Governance** | Automated `engine_verdict` is permanently immutable; `human_verdict` records officer adjudication with mandatory reasons | Preserves algorithmic objectivity while maintaining officer authority |
| **Smart Recapture Loop** | Automated generation of targeted panel tasks (e.g., "Recapture Back Panel for Consumer Care") | Guides inspectors to resolve missing evidence without restarting visits |
| **Offline Synchronization** | Local SQLite/IndexedDB queue with idempotency keys, single-use capture tokens, and clock skew correction | Full functionality without network; seamless auto-sync upon reconnection |

---

## 4. Key Technology Stack

- **Mobile Client (Field App):** React Native, Expo SDK 54, `expo-camera`, `expo-secure-store`, Android standalone release APK (ARM64/ARMv7, Android 14/15 16 KB page-size aligned).
- **Web Portal (Admin & Supervisory):** React 18, Vite 5, Tailwind CSS, Lucide icons, Recharts dashboard, Responsive PWA.
- **Backend Service:** FastAPI (Python 3.11+ supported, Python 3.11 container runtime, Python 3.11/3.13 verified locally), SQLAlchemy 2.0 ORM, Pydantic v2 validation, Uvicorn/Gunicorn.
- **Database & Storage:** PostgreSQL 15+ (Supabase connection pooler), Supabase S3-compatible Object Storage for evidence files.
- **Optical & AI Services:** Cloud OCR (Google Cloud Vision / OCR.space), Cloud LLM (Groq Llama-3.3-70B / Google Gemini fallback for structured extraction).

---

## 5. Current Production Baseline & Links

| Asset | Current Value / Production URL |
|---|---|
| **Production Backend URL** | `https://niyamnetra-backend.onrender.com` |
| **Production Health Endpoint** | `https://niyamnetra-backend.onrender.com/health` |
| **Web Portal URL** | `https://niyamnetra-legal-metrology-rules.vercel.app` |
| **GitHub Repository** | `https://github.com/Sanjaysanjay31/NiyamNetra-legal-Metrology-Rules-System-` |
| **Active Statutory Rule Pack** | `2026.09.v1` (Fourth Amendment G.S.R. 826(E) baseline) |
| **Statutory Rules As At** | `2026-09-21` |
| **Registered Statutory Rules** | 26 registered rules: 19 executable checks and 7 manual-review/advisory checks |
| **Backend Test Suite** | **568 automated tests collected** across 35 test files (`pytest --collect-only -q`); recorded release baseline verified **428 passed, 0 failed** |
| **Frontend Test Suite** | **228 assertions passed, 0 failed** across all 8 test suites (`node run_all_tests.js`) |
| **Release APK Version** | v1.0.4 (VersionCode: 5) |
| **Release APK File** | `release/NiyamNetra-v1.0.4-release.apk` |
| **APK SHA-256 Digest** | `DB6897E8FB05CA062C3E44839C9605B998F0FD533481F9F8F62C3D141BF366BA` |
| **Signing Cert Fingerprint** | `fac61745dc0903786fb9ede62a962b399f7348f0bb6f899b8332667591033b9c` |

---

## 6. System Boundaries & Non-Goals

To maintain rigorous statutory integrity, NiyamNetra enforces strict boundaries:
- **Decision-Support, Not Automated Prosecution:** NiyamNetra never issues automatic legal notices, compounds offenses, or levies fines. The platform generates evidence-backed draft violation dossiers; the authorized human officer retains sole statutory authority under the Act.
- **Packaging Declarations Only:** The system verifies labeling declarations under the LM Rules, 2011. It does **not** conduct chemical laboratory testing, adulteration checks, or food safety analysis (governed separately by FSSAI).
- **Physical Scale Requirement:** Font height verification under Rule 7 Table-I requires a physical calibration reference (standard ID-1 card, standard coin, or officer-verified panel measurement). Uncalibrated images yield `not_assessed` with a calibration capture request, preventing false violations.
- **Fail-Safe Legal Interpretation:** In ambiguous statutory provisions (such as the Fourth Amendment Rule 6(4A)(d) origin-marking clause), missing evidence produces `REVIEW_REQUIRED` / `not_assessed` rather than false algorithmic failure.
