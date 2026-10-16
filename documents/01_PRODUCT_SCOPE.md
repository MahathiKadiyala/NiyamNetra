# Product Scope & Functional Boundary

**Document Code:** `DOC-01`  
**Applies to:** NiyamNetra (SIH26034) Platform  
**Statutory Framework:** Legal Metrology Act, 2009 & Legal Metrology (Packaged Commodities) Rules, 2011  

---

## 1. Statutory Problem Context (SIH26034)

Under Section 18 of the **Legal Metrology Act, 2009** read with the **Legal Metrology (Packaged Commodities) Rules, 2011**, no person may manufacture, pack, sell, distribute, deliver, offer, expose, or store for sale any pre-packaged commodity unless the package bears all mandatory consumer declarations prescribed by Chapter II.

Problem Statement SIH26034 tasks developers with building a software system capable of:
1. Scanning images of pre-packaged commodities and extracting label declarations.
2. Checking completeness, correctness, placement, and numeral dimensions of declarations.
3. Detecting non-compliant, deceptive, or missing mandatory declarations.
4. Verifying font size, readability, and contrast requirements under Rule 7 Table-I.
5. Generating standardized digital compliance reports and violation summaries.
6. Maintaining an immutable repository of inspection histories, scanned products, and evidence.
7. Providing role-based administrative dashboards for state and central enforcement monitoring.

---

## 2. Target User Personas & Operational Contexts

```
                      ┌─────────────────────────────────┐
                      │    NiyamNetra User Hierarchy    │
                      └─────────────────────────────────┘
                                       │
        ┌──────────────────────────────┼──────────────────────────────┐
        │                              │                              │
        ▼                              ▼                              ▼
┌──────────────────┐          ┌──────────────────┐          ┌──────────────────┐
│ Field Inspector  │          │ Reviewing Officer│          │ Central Admin    │
│ (LM-TG-XXXX)     │          │ (Controller / DA)│          │ (State Director) │
└──────────────────┘          └──────────────────┘          └──────────────────┘
  - Physical Visits             - Review Queue                - District Analytics
  - Camera Capture              - Adjudication Overrides      - Officer Management
  - Quality Validation          - Formal Notice Sign-Off      - Rule Pack Deployment
  - Offline Enqueue             - Prosecution Recomm.         - Audit Chain Verify
```

### 2.1 Field Enforcement Inspector
- **Operating Environment:** Physical retail shops, supermarkets, manufacturing warehouses, transport godowns. Often in noisy, poorly lit locations with unstable or non-existent cellular connectivity.
- **Primary Tasks:** Authenticate into device-bound mobile app, register shop visit with GPS location, photograph multi-panel packaging under guidance, review real-time OCR and rule evaluations, fulfill targeted recapture tasks when evidence is incomplete, and queue inspections for upload.
- **Pain Solved:** Eliminates manual measurement of small font numerals (1.0 mm – 6.0 mm) with calipers, prevents overlooking required non-PDP declarations, and automates tedious paperwork.

### 2.2 Authorized Adjudicating Officer
- **Operating Environment:** Desk-based or tablet workstation reviewing inspection files, notices, and contested evidence.
- **Primary Tasks:** Access the **Officer Review Queue**, inspect highlighted ambiguities or cross-panel conflicts, evaluate the automated `engine_verdict`, record statutory adjudications (`human_verdict`) with mandatory justification, and approve draft Section 36 violation dossiers.
- **Pain Solved:** Guarantees that evidence presented for compounding or prosecution is backed by un-tampered photographic proofs, exact statutory citations, and an immutable audit trail.

### 2.3 Central / State Administrator
- **Operating Environment:** Directorate of Legal Metrology central dashboard.
- **Primary Tasks:** Monitor inspection velocity, observe top failed checks across districts, detect repeat-offender brands or manufacturers, audit device installation bindings, manage officer credentials, and monitor rule pack version adoption.

---

## 3. System Inputs

NiyamNetra ingests strictly validated, structured inputs from two distinct channels:

### 3.1 Physical Package Evidence (Primary Channel)
1. **Multi-Panel Photographic Evidence:**
   - Principal Display Panel (Front / PDP)
   - Back Information Panel
   - Side / Gusset Panels
   - Top / Crimp / Base Panels (often carrying batch codes or MRP)
2. **Package Geometry & Physical Scale Reference:**
   - Package shape (rectangular, cylindrical, blown-moulded, blister pack)
   - Measured or declared dimensions (height, width, diameter in mm)
   - Optical calibration reference: Standard ID-1 card (85.60 mm × 53.98 mm), standard Indian 5 Rupee coin (23.00 mm diameter), or physical dimension scale
3. **Inspector-Declared Transaction Context:**
   - Transaction classification: `retail_sale`, `wholesale`, `industrial`, `institutional`, `packed_in_presence`, or `export`
   - Commodity generic classification and perishable flag
   - Medical device or tobacco product flags (activating statutory exemptions or cross-regulatory rules)
   - Dual-pricing / sticker flags (Rule 6(2A) and Rule 6(1)(da))

### 3.2 Digital E-Commerce Listing Evidence (Secondary Channel)
1. **E-Commerce Product URLs:** Verified marketplace links (Amazon.in, Flipkart.com, etc.) evaluated for digital declaration compliance under Rule 6(10) and Rule 6(10A).
2. **HTML / API Text Payloads:** Extracted listing title, manufacturer details, country of origin, net quantity, MRP, and consumer care links.

---

## 4. System Outputs

All outputs generated by NiyamNetra adhere to strict statutory schemas:

### 4.1 Structured Declarations
Extracted label text normalized into typed schemas with exact provenance:
- Observed values, normalized unit equivalents, bounding box coordinates, source image panel, and extraction confidence score.

### 4.2 Three-State Compliance Findings
Every evaluated check produces one of three unambiguous findings:
- `pass`: Observed declaration satisfies statutory requirements and numerical thresholds.
- `fail`: Observed declaration explicitly breaches statutory requirements or is confirmed missing despite complete panel coverage.
- `not_assessed`: The system cannot verify compliance due to missing panels, uncalibrated scale, or unreadable text.

### 4.3 Four-State Inspection Scans
Aggregated package-level outcomes:
- `compliant`: Every applicable check produced a verified `pass`. Zero `not_assessed` findings allowed.
- `violation`: At least one applicable statutory check produced a confirmed `fail`.
- `not_assessed`: Essential evidence is missing or uncalibrated; requires targeted recapture.
- `out_of_scope`: Package is statutorily exempt (e.g. package net weight ≤ 10 g / 10 ml under Rule 26(a), or industrial consumer package).

### 4.4 Draft Resumption & Inspection Finalization
- **Draft Session Discovery & Resumption:** Field officers can discover and resume unfinished draft inspection sessions (`GET /inspections?status=draft`) across app restarts or multi-stage shop visits.
- **Atomic Finalization Lifecycle (`POST /inspections/{id}/submit`):**
  - Automatically assesses any unassessed eligible scans or scans with updated evidence before freezing the inspection record.
  - Reuses fresh assessment results when evidence fingerprints are unchanged.
  - Accommodates zero-scan merchant refusal (`signature_status: "refused"`), recording a formal refusal audit trail without generating false compliance verdicts.
  - Distributed worker lease locking prevents race conditions and duplicate concurrent finalizations.

### 4.5 Actionable Officer Review Queue & Subtype Categorization
Submitted inspections requiring supervisory verification enter the centralized Officer Review Queue (`GET /review/queue`). Drafts are strictly excluded until finalized. Items are grouped into actionable categories:
- `unconfirmed_violation`: Algorithmic rule breaches awaiting officer adjudication and statutory sign-off.
- `technical_recovery`: Optical or provider outages (network timeout, rate limit) requiring automated retry or technical reassessment without penalizing the merchant.
- `recapture_required`: Scans with incomplete panel coverage or missing scale reference requiring targeted recapture.
- `confirmed_violation`: Violations formally confirmed by officer adjudication awaiting notice issuance.
- `resolved`: Adjudicated inspections where all findings and conflicts have been settled.

Server-side subtype filtering (`technical_recovery`, `recapture_required`, `incomplete_coverage`, `unconfirmed_violation`, `confirmed_violation`, `low_confidence`, `offline_edit`) applies before pagination, ensuring stable offsets, consistent queue metrics, and accurate summary totals.

### 4.6 Enforcement Dossiers & Statutory Inspection Reports
- **Violation Dossier:** Structured summary citing the specific statutory limb of Section 36 (Section 36(1) for packaging/label defects; Section 36(2) for short quantity).
- **Inspection Docket (PDF & DOCX):** Timestamped inspection summary with embedded high-resolution annotated crops, officer notes, cryptographic SHA-256 evidence digests, and an online verification QR code.

---

## 5. Strict Product Boundaries & Non-Goals

To maintain rigorous credibility with regulatory authorities and judicial forums, NiyamNetra explicitly defines what it **does NOT do**:

```
┌────────────────────────────────────────────────────────────────────────┐
│                   EXPLICIT PRODUCT NON-GOALS                           │
├────────────────────────────────────────────────────────────────────────┤
│ ❌ NO Automatic Legal Prosecution or Self-Executing Fines             │
│    The platform is a decision-support tool. Only the authorized human  │
│    officer can issue compounding notices or file court complaints.    │
│                                                                        │
│ ❌ NO Food Quality, Adulteration, or Safety Testing                   │
│    NiyamNetra verifies labeling under the LM Rules, 2011. It does not │
│    test chemical composition, purity, or food safety (FSSAI ambit).    │
│                                                                        │
│ ❌ NO LLM-Generated Legal Verdicts                                     │
│    Cloud LLMs are restricted strictly to text structuring and entity   │
│    normalization. All compliance decisions are 100% deterministic code.│
│                                                                        │
│ ❌ NO Uncalibrated Font Height Enforcement                             │
│    The engine will never flag a typography violation (Rule 7 Table-I) │
│    without a mathematically verified physical calibration standard.    │
│                                                                        │
│ ❌ NO "Tamper-Proof" or Court Admissibility Pre-Judgments              │
│    Evidence is integrity-verifiable and tamper-evident (SHA-256), but  │
│    court admissibility remains subject to the Indian Evidence Act §65B.│
└────────────────────────────────────────────────────────────────────────┘
```
