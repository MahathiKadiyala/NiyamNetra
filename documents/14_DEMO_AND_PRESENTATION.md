# Demonstration Script, Presentation Guide & Evaluator Q&A

**Document Code:** `DOC-14`  
**Purpose:** Presentation Support Guide for Smart India Hackathon (SIH26034)  
**Primary Audience:** Hackathon Evaluators, Legal Metrology Officials, Technical Judges  

---

## 1. Structured 9-Stage Live Demonstration Script

Follow this sequential storyline during live system evaluation to demonstrate end-to-end functionality across both the Android mobile client and the web supervisory portal:

```
[ Stage 1: Officer Login & Device Binding ]
                    │
[ Stage 2: Store Check-in & GPS Geofence ]
                    │
[ Stage 3: Multi-Panel Camera Capture & Quality Gate ]
                    │
[ Stage 4: Sub-2-Second Cloud Assessment (Compliant Pack) ]
                    │
[ Stage 5: Incomplete Package & Review-Required Detection ]
                    │
[ Stage 6: Smart Evidence Recapture Task ]
                    │
[ Stage 7: Real-Time Reassessment ]
                    │
[ Stage 8: Officer Adjudication in Supervisory Portal ]
                    │
[ Stage 9: Tamper-Evident Report & QR Verification ]
```

### Stage 1: Officer Authentication & Device Binding
- **Action:** Open `NiyamNetra` on the Android device. Sign in as Field Inspector (`LM-TG-1042`).
- **Talking Point:** *"Notice that authentication binds the session to this physical phone using a unique 32-byte cryptographic install identifier. A compromised token cannot be replayed on an unauthorized phone."*

### Stage 2: Store Check-in & Geofencing
- **Action:** Select a registered retail store from the directory. View the geofence perimeter indicator. Tap "Begin Inspection".
- **Talking Point:** *"The inspection is geofenced against the registered store coordinates using server-side haversine validation, establishing verified physical presence."*

### Stage 3: Multi-Panel Camera Capture & Edge Quality Gate
- **Action:** Position a compliant FMCG product (e.g. Fortune Sunflower Oil pouch) under the camera. Intentionally shake the camera once to trigger the blur indicator, then steady it to observe the green `READY` badge. Capture Front and Back panels.
- **Talking Point:** *"The edge quality gate evaluates sharpness and glare directly on the phone in 38 milliseconds. It rejects unreadable photos before wasting cloud network bandwidth or producing flawed findings."*

### Stage 4: Sub-2-Second Statutory Assessment (Compliant Package)
- **Action:** Tap "Assess Package". Observe instant turnaround (under 2 seconds).
- **Talking Point:** *"Notice the turnaround time: 1.5 seconds end-to-end on cold path, and under 45 ms on cached fast path. The system runs Cloud OCR with automatic provider fallback, structures text with Groq Llama-3.3-70B, and mathematically evaluates 19 executable statutory checks across all 26 registered rules. Every check is green, with verified legal citations to Rule 6(1) and Rule 7 Table-I."*

### Stage 5: Incomplete Package Capture (Review-Required Case)
- **Action:** Scan a second product, but capture **only the front panel**, leaving the back panel un-photographed. Tap "Assess".
- **Talking Point:** *"Notice our core safety principle: The system never assumes compliance. Because the back panel is missing, consumer care and address checks return `not_assessed`, and the overall verdict is `REVIEW_REQUIRED`, with an honest denominator: 11 of 19 checks assessed."*

### Stage 6: Smart Evidence Recapture Loop
- **Action:** Tap the alerted capture task: `CAP_BACK_PANEL_MISSING`. Aim camera at the back panel and capture.
- **Talking Point:** *"Rather than failing the trader or requiring the officer to re-enter all data, the platform generates a targeted recapture task guiding the officer to capture the exact missing evidence."*

### Stage 7: Real-Time Reassessment
- **Action:** Tap "Submit Recapture & Reassess".
- **Talking Point:** *"The engine updates the assessment in real time across both panels. The package now evaluates to fully compliant."*

### Stage 8: Officer Adjudication in Supervisory Web Portal
- **Action:** Switch to the desktop laptop running the Web Portal. Open the **Officer Review Queue**. Show a flagged sticker overprint issue. Enter an official override justification and sign off.
- **Talking Point:** *"This highlights our governance model: The AI recommends, the officer decides. The automated `engine_verdict` remains permanently immutable in the database, while the officer's `human_verdict` is recorded with mandatory legal justification and timestamped in the audit chain."*

### Stage 9: Tamper-Evident Report & QR Verification
- **Action:** Click "Export Inspection Docket (PDF)". Open the generated PDF and scan its embedded QR code using a phone camera.
- **Talking Point:** *"The generated docket features high-resolution evidence crops, statutory citations, and a SHA-256 cryptographic verification QR code pointing directly to the live government backend. Anyone scanning the paper report can verify that the digital record has not been altered."*

---

## 2. Key Technical Differentiators for Evaluators

1. **Separation of Concerns:** Zero LLM legal decisions. The LLM structures evidence; 100% deterministic code evaluates the law.
2. **True Statutory Currency:** Up to date with the **Fourth Amendment Rules, 2026 (G.S.R. 826(E) dated 21.09.2026)** and the Jan Vishwas Act.
3. **Honest Denominators:** Denominators are always stated explicitly (19 executable checks per scan; 26 registered catalog rules). Partial assessments are never hidden behind vague percentages.
4. **Offline Resilience:** Functions seamlessly in low-connectivity market environments with local SQLite persistence, bounded 30-page review caching, and idempotent replay protection.
5. **Tamper-Evident Evidence Chain:** Immutable original camera files, SHA-256 cryptographic digests, and append-only database ledgers.

---

## 3. Evaluator Q&A Preparation

### Q1: "How do you prevent the LLM from hallucinating legal compliance?"
**Answer:**  
*"The LLM is completely isolated from compliance evaluation. In our pipeline, the LLM is used strictly as a structured entity parser to transform noisy OCR text into a typed JSON schema (`StructuredDeclarationResult`). All legal evaluations—such as font height calculations under Rule 7 Table-I, metric unit validation, and sticker legality—are executed by deterministic Python algorithms in our versioned legal rule engine."*

### Q2: "How does the system measure font height in millimeters from a 2D photograph?"
**Answer:**  
*"Physical measurement requires an optical calibration standard. When an officer places a standard reference object—such as an ID-1 card (85.60 mm) or a standard 5 Rupee coin (23.00 mm)—alongside the packaging, our OpenCV geometry engine detects the reference bounding box, computes the precise millimeter-per-pixel ratio, and calculates letter height. If no calibration standard is present, the engine refuses to guess and safely returns `not_assessed` with a calibration capture request."*

### Q3: "What happens if an inspector is working in a basement godown with zero internet?"
**Answer:**  
*"NiyamNetra is built local-first. The mobile app saves original camera photos to the local filesystem and queues inspections in a local SQLite database. The officer conducts the entire inspection offline. When connectivity is restored, our background sync engine flushes pending tasks to the server using unique UUID `Idempotency-Key` headers, guaranteeing exactly-once processing with zero duplicate records."*

### Q4: "Can an officer corrupt or delete evidence to favor a trader?"
**Answer:**  
*"No. The original camera photo is stored with its SHA-256 cryptographic hash immediately upon upload, and database-level triggers prevent deletion or modification of evidence rows. Furthermore, the automated `engine_verdict` is permanently immutable. If an officer disagrees with the algorithmic verdict, their override is recorded as a separate `human_verdict` requiring a mandatory justification string, and the event is written to an append-only cryptographic audit chain."*
