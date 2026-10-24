# Field Inspector & Officer Workflows

**Document Code:** `DOC-03`  
**Applies to:** Field Mobile App (`Frontend_App`) & Web Supervisory Portal (`Frontend_Portal`)  
**Audience:** Field Legal Metrology Officers, Adjudicating Officers, System Evaluators  

---

## 1. Overview of Operational Workflows

NiyamNetra models four distinct real-world inspection workflows encountered during statutory market surveillance:

```
┌────────────────────────────────────────────────────────────────────────┐
│                      CORE OPERATIONAL WORKFLOWS                        │
├────────────────────────────────────────────────────────────────────────┤
│ 1. Standard Compliant Inspection   ──  Fast-path single/multi-panel    │
│ 2. Review-Required & Recapture     ──  Gaps, ambiguities, low contrast │
│ 3. Disconnected Offline Inspection ──  Godowns, mandis, no connectivity│
│ 4. Adjudication & Enforcement      ──  Section 36 dossiers & notices   │
└────────────────────────────────────────────────────────────────────────┘
```

---

## 2. Workflow 1: Standard Compliant Inspection

This is the standard fast-path workflow for a compliant or straightforward pre-packaged commodity.

| Step | Actor / Participant | Action & Data Payload | Technical Invariant |
|:---:|---|---|---|
| **1** | Field Inspector ➔ Mobile App | Enters Employee ID (`LM-TG-XXXX`) & credentials | Authenticates device binding & role authorization |
| **2** | Mobile App ➔ FastAPI Gateway | `POST /auth/login` (with `install_id`) | HTTP 200 OK + Bearer JWT token |
| **3** | Field Inspector ➔ Mobile App | Selects registered store & taps "Start Inspection" | Geofenced proximity check ($\le 100\text{ m}$) |
| **4** | Mobile App ➔ FastAPI Gateway | `POST /inspections` (store ID, GPS coordinates) | HTTP 201 Created (`inspection_id`) |
| **5** | Field Inspector ➔ Mobile App | Points camera at Principal Display Panel (Front) | Immutable RAW photo saved locally with SHA-256 |
| **6** | Mobile App ➔ Edge Quality Gate | Heuristic analysis (blur variance, glare, framing) | Quality Status: `READY` (~38 ms) |
| **7** | Field Inspector ➔ Mobile App | Captures Information Panel (Back / Crimp) | Quality Status: `READY` |
| **8** | Mobile App ➔ FastAPI Gateway | `POST /inspections/{id}/scans` + upload images | HTTP 201 Created; SHA-256 verified on disk |
| **9** | Mobile App ➔ FastAPI Gateway | `POST /scans/{scan_id}/assess` | Triggers Cloud OCR ➔ Cloud LLM ➔ 19 Executable Rules |
| **10** | Rules Engine ➔ Mobile App | Assessment Payload: `COMPLIANT` | All 19 executable statutory checks satisfied |
| **11** | Mobile App ➔ Field Inspector | Renders green "COMPLIANT" banner | Official record anchored in audit ledger |

### Screen Progression:
1. `LoginScreen`: Officer enters official Employee ID and password. Authenticates device binding.
2. `InspectionScreen`: Displays active stores within geofenced proximity. Officer initiates visit.
3. `CameraCaptureScreen`: Live camera viewport with bounding guidelines and optical calibration marker indicators.
4. `QualityFeedbackModal`: Displays non-blocking green confirmation banner when image sharpness and lighting are optimal.
5. `AssessmentResultScreen`: Displays green `COMPLIANT` status with full itemized breakdown (19/19 executable checks passed).

---

## 3. Workflow 2: Review-Required & Smart Recapture Flow

When a package is captured partially, or when declarations suffer from specular glare or optical uncertainty, NiyamNetra automatically initiates the smart recapture loop:

| Step | Actor / Participant | Action & Data Payload | System Outcome |
|:---:|---|---|---|
| **1** | Inspector ➔ Mobile App | Captures Front Panel only & taps "Assess" | Single panel submitted |
| **2** | Mobile App ➔ Backend Gateway | `POST /scans/{id}/assess` | Initial statutory evaluation executed |
| **3** | Backend Gateway ➔ Mobile App | Verdict: `REVIEW_REQUIRED` (Completeness: 11/19 Assessed) | 8 checks `NOT_ASSESSED` (Back Panel missing) |
| **4** | Backend Gateway ➔ Mobile App | Auto-generates Capture Request: `CAP_69_46_back_missing` | Guided recapture task surfaced |
| **5** | Mobile App ➔ Inspector | Alert: *"Review Required: Back Panel Evidence Missing"* | Targeted guidance with camera reticle |
| **6** | Inspector ➔ Mobile App | Re-aligns camera and photographs Back Panel | High-sharpness capture saved |
| **7** | Mobile App ➔ Backend Gateway | `POST /scans/{id}/images` (panel: `back`, `linked_task_id`) | Uploads missing evidence |
| **8** | Mobile App ➔ Backend Gateway | `POST /recapture/inspections/{id}/reassess` | Re-evaluates package with multi-panel context |
| **9** | Backend Gateway ➔ Mobile App | If compliant: `COMPLIANT`. If sticker overprint: `VIOLATION` | Defect attributed to Rule 6(1)(da) |
| **10** | Backend Gateway ➔ Web Portal | Surfaces in Officer Review Queue (`/review/queue`) | Adjudicating officer reviews crops & signs |

### Actionable Capture Tasks:
- If net quantity numeral height cannot be computed due to missing calibration, the app presents:  
  **`CAP_CALIBRATION_REQUIRED`**: *"Place standard ID card or 5 Rupee coin alongside label."*
- If the back panel was omitted during intake, the app presents:  
  **`CAP_BACK_PANEL_MISSING`**: *"Capture back panel showing manufacturer address and consumer care."*

---

## 4. Workflow 3: Disconnected Offline Field Inspection

In godowns, basements, or remote locations with zero cellular coverage, field officers can continue their work uninterrupted:

```
[ Field Officer in Offline Godown ]
               │
               ▼
   1. Offline Local Capture
      - Officer takes photographs of packaging panels
      - App persists original high-res RAW photos to local file system
      - Quality gate runs entirely locally on-device
               │
               ▼
   2. Local SQLite Enqueueing
      - Inspection record saved locally with client UUID
      - Tasks marked with state: QUEUED_OFFLINE
      - Client timestamp recorded with monotonic counter
               │
               ▼
   3. Inspector Exits Godown & Regains Connectivity
      - App background network listener detects active internet
      - Sync engine initiates automated flush
               │
               ▼
   4. Server-Side Idempotent Ingestion
      - Sends POST /inspections and POST /scans with Idempotency-Key
      - Server calculates clock_skew_seconds between device and server
      - Images uploaded, verified via SHA-256, and assessed
               │
               ▼
   5. Notification of Completed Assessment
      - Inspector receives push/haptic notification of final verdict
```

---

## 5. Workflow 4: Adjudication & Enforcement Reporting

When an inspection reveals confirmed statutory non-compliance, the platform assists the officer in building a defensible legal dossier:

1. **Automatic Limb Categorization:**
   - The engine automatically maps findings to Section 36 limbs:
     - **Section 36(1):** Non-standard packaging, missing mandatory declarations, improper font sizes, omission of manufacturing dates.
     - **Section 36(2):** Discrepancies in declared quantity vs actual contents (short weight/volume).
2. **Advisory Action Recommendation:**
   - Based on the Jan Vishwas Act (Act 18 of 2023) tiers:
     - First offense (minor/technical defect): Advisory recommendation to issue **Notice for Rectification**.
     - Substantive violation (deceptive dual pricing, missing origin): Advisory recommendation for **Compounding or Formal Prosecution**.
3. **Report Generation:**
   - Officer triggers report export via `POST /reports/inspections/{id}/pdf`.
   - Backend assembles:
     - Official header with Department seal and jurisdiction.
     - Store premises details and GPS geofence verification summary.
     - Complete itemized checklist with governing rule citations.
     - Annotated high-resolution photo evidence crops.
     - Officer signature block and cryptographic verification QR code.

---

## 6. Workflow 5: Draft Resumption & Inspection Finalization

Field inspections frequently span multiple premises or experience intermittent interruptions. NiyamNetra supports clean session recovery and idempotent finalization:

1. **Draft Discovery & Resumption:**
   - Field officers can list pending drafts via `GET /inspections?status=draft`.
   - The mobile interface allows officers to resume draft visits, add new packaged products, upload additional panels, or resolve recapture tasks without restarting the inspection session.
2. **Atomic Finalization (`POST /inspections/{inspection_id}/submit`):**
   - **Automatic Evaluation of Eligible Scans:** Any scan that has not yet been assessed, or whose evidence was modified since last evaluation, is automatically run through the rules engine before the inspection freezes.
   - **Fresh Assessment Reuse:** Scans with unchanged evidence fingerprints bypass expensive re-computation and reuse fresh findings.
   - **Merchant Refusal Handling:** If a merchant refuses inspection or signature, the inspector marks `signature_status: "refused"` with zero scans. The visit rolls up to a formal `refused` state in audit records rather than generating an empty or false compliant record.
   - **Distributed Worker Lease Protection:** Uses database-backed idempotency leasing (HTTP 102 Processing status) to prevent race conditions from accidental double-tapping or concurrent worker execution. Once committed, retrying returns the persisted record with `finalization_summary`.

---

## 7. Workflow 6: Actionable Review Queue & Dynamic Finding Interface

Supervisory officers access the centralized review queue (`GET /review/queue`) to triage and adjudicate non-routine cases:

1. **Actionable Review Categories:**
   - `unconfirmed_violation`: Algorithmic rule breaches requiring human verification before compounding or notice serving.
   - `technical_recovery`: Transient OCR or provider network timeouts requiring automatic retry or technical reassessment.
   - `recapture_required`: Incomplete panel coverage where mandatory declarations could not be found.
   - `confirmed_violation`: Adjudicated non-compliant items ready for statutory notice generation.
   - `resolved`: Inspections where all findings, remarks, and cross-panel conflicts have been settled.
2. **Dynamic Assessment Presentation in UI:**
   - **Itemized Check Details:** Displays check code (e.g. `CHK01`, `CHK06`), observed value (e.g. "₹45.00"), required statutory value (e.g. "Inclusive of all taxes"), and legal citation (e.g. "Rule 6(1)(e)").
   - **Token Hygiene on Evidence URLs:** Pre-signed evidence image URLs presented in the UI are scrubbed of raw authorization bearer tokens, preventing accidental credential leakage in browser history or exported logs while preserving access-controlled viewing.
   - **Exclusion of Drafts:** Active drafts are strictly excluded from the supervisory queue until formally finalized, preventing unfinished field work from cluttering adjudication workflows.

---

## 8. Evidence Rendering, Assessment States & Counter Contract in the Inspector UI

Following the hardening completed in Modules 8–11, the field inspector interface (`Frontend_App`) implements strict evidence mapping, dynamic state rendering, and arithmetic consistency:

### 8.1 Evidence Thumbnail Rendering & Navigation Isolation
- **Server-Returned Evidence Binding:** Captured packaging images returned by the backend (`ScanImage` records) are bound directly to their corresponding panel slots (`front`, `back`, `side`, `mrp`, `batch`).
- **Stable React Keys:** Thumbnails use deterministic, collision-free element keys (e.g. `evidence-thumb-${image.id || image.sha256 || panel}`) to eliminate thumbnail flicker and prevent incorrect panel swapping during component re-renders.
- **Dynamic Relative URL Resolution:** The mobile client dynamically resolves server-returned relative paths against the configured API root, ensuring consistent image loading across local debugging, staging environments, and production.
- **Authenticated Image Fetching:** Image requests attach authorization headers in flight. Evidence URLs displayed in the UI and network inspection traces never expose raw Bearer JWT tokens in query strings.
- **Loading, Error & Retry States:** The `EvidenceThumbnail` component provides non-blocking loading skeletons, fallback placeholders for corrupted images, and retry capabilities without disrupting the inspector's workflow.
- **Cross-Scan State Isolation:** Switching between scans in a multi-product inspection clears active image buffers and thumbnails, guaranteeing that evidence from Scan 1 never bleeds into Scan 2.
- **Draft Resumption & Restoration:** Resuming a draft inspection restores previously uploaded panel photos, their thumbnail mappings, and verified checksums directly from server records.
- **Physical Device Limitation Note:** While the component logic, thumbnail mapping, and relative URL handling are fully verified by automated unit tests (`test_evidence_thumbnail_rendering.js`), physical Android screen rendering must be verified separately on a connected physical device during release clearance.

### 8.2 Data-Driven Assessment Presentation
- **Backend Response Grounding:** Findings, counter badges, and severity indicators displayed in `ViolationsScreen` and `PassScreen` derive strictly from the backend response (`ScanOut`), completely replacing historical hardcoded mock assessments (such as static 4/3/12 values).
- **Truthful Operational States:** The UI distinguishes:
  - `COMPLIANT`: All applicable checks passed.
  - `VIOLATION`: One or more confirmed statutory breaches detected.
  - `TECHNICAL_RECOVERY`: Transient OCR or provider network outage; surfaces non-punitive retry options.
  - `RECAPTURE_REQUIRED`: Missing packaging panels or uncalibrated geometry; highlights targeted camera guidance.
  - `REVIEW_REQUIRED`: Unresolved ambiguities or cross-panel conflicts requiring officer adjudication.
  - `LOADING` / `NETWORK_FAILURE`: Displays truthful connectivity or timeout indicators without fabricating results.
- **Null-Field Honesty:** Unavailable or unread packaging fields (such as missing batch numbers or unobserved expiry dates) remain explicitly unknown. The client never invents default or dummy product data.
- **In-Flight Response Protection:** When switching scans or re-triggering assessments, session generation tokens ensure obsolete in-flight responses are discarded upon arrival, preventing race conditions from corrupting the active scan view.
- **Evidence-Triggered Re-Evaluation:** Modifying or uploading new packaging evidence invalidates stale assessment badges, prompting the officer to re-assess the latest evidence set.

### 8.3 Standardized Counting Contract
The mobile interface strictly displays the backend arithmetic counting contract:
$$\text{total\_findings} = \text{pass\_count} + \text{fail\_count} + \text{not\_assessed\_count}$$
$$\text{checks\_assessed} = \text{pass\_count} + \text{fail\_count}$$

- **Clear Denominators:** Total findings reflects the actual returned finding count for the scan (up to 19 for a full applicable assessment, or fewer for partially applicable assessments), with the denominator clearly distinguishing evaluated checks from the 26-rule catalog metadata. The interface never displays the incorrect formula `pass + fail + not_assessed = checks_assessed`.
- **Distinct Rule Identities:** Checks evaluating different statutory clauses (e.g. `CHK06` PDP character/numeral height under Rule 7 Table-I vs `CHK06b` Net Quantity specific numeral height under Rule 7) remain itemized as distinct findings in the UI list.
