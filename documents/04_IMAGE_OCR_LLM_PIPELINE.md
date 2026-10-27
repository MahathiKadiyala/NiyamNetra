# Optical, Vision & Language Pipeline

**Document Code:** `DOC-04`  
**Applies to:** Image Ingestion, Quality Assessment, Geometry, Cloud OCR, and LLM Structuring  
**Performance Baseline:** Sub-2-Second End-to-End Latency Profile  

---

## 1. Dual-Artifact Evidentiary Guarantee

To eliminate evidence-tampering vulnerabilities in judicial proceedings, NiyamNetra enforces a strict dual-artifact separation between authoritative forensic evidence and operational analysis derivatives.

```
                  ┌──────────────────────────────────────────────┐
                  │            Camera Capture Event              │
                  └──────────────────────────────────────────────┘
                                         │
                    ┌────────────────────┴────────────────────┐
                    ▼                                         ▼
   ┌─────────────────────────────────┐       ┌─────────────────────────────────┐
   │    AUTHORITATIVE ORIGINAL       │       │    DERIVED ANALYSIS ARTIFACT    │
   ├─────────────────────────────────┤       ├─────────────────────────────────┤
   │ - Full camera resolution        │       │ - Max 1600 px along long edge   │
   │ - 100% byte-exact preservation  │       │ - Standardized 75% quality JPEG │
   │ - Never cropped, scaled, warped │       │ - Normalised EXIF orientation   │
   │ - Cryptographic SHA-256 digest  │       │ - Used strictly for OCR / LLM   │
   │ - Immutable audit anchor        │       │ - Distinct file path & URI      │
   └─────────────────────────────────┘       └─────────────────────────────────┘
```

1. **Authoritative Original Evidence:** Stored untouched at `data/images/{scan_id}/original_{uuid}.jpg`. The SHA-256 digest is calculated immediately upon byte ingestion and recorded in `scan_images.sha256`. It is never overwritten, compressed, or warped.
2. **Derived Analysis Artifact:** Generated adaptively on the client or server. If the original image width or height exceeds 1600 px, it is downscaled along the long edge while strictly preserving aspect ratio. Grayscale conversion is forbidden to ensure color-coded indicators (such as veg/non-veg dots under Rule 6(4A)(d)) remain assessable.

### End-to-End Processing Flow

```text
┌─────────────────────────────────┐
│   Live Mobile Optical Capture   │
└────────────────┬────────────────┘
                 │
                 ▼
┌─────────────────────────────────┐       ┌─────────────────────────────────┐
│      Original RAW Image         │       │    Adaptive Analysis Derivative │
│  (Byte-Exact SHA-256 Digest)    │       │     (max 1600 px Color JPEG)    │
└─────────────────────────────────┘       └──────────────┬──────────────────┘
                                                         │
                                                         ▼
                                          ┌─────────────────────────────────┐
                                          │     Client Edge Quality Gate    │
                                          │  - Blur: Laplacian (>= 60)      │
                                          │  - Glare: Luma (<= 15%)         │
                                          └──────┬───────────────────▲──────┘
                                                 │                   │ (Retake Guidance)
                                 (Pass: Optimal) │                   │
                                                 ▼                   │
                                          ┌──────────────────────────┴──────┐
                                          │     HTTPS Multipart Upload      │
                                          └──────────────┬──────────────────┘
                                                         │
                                                         ▼
                                          ┌─────────────────────────────────┐
                                          │   4-Point Perspective Warp      │
                                          │  (Fronto-Parallel Planar Plane) │
                                          └──────────────┬──────────────────┘
                                                         │
                                                         ▼
                                          ┌─────────────────────────────────┐
                                          │       Cloud OCR Engine          │
                                          │ (Google Cloud Vision/OCR.space) │
                                          └──────────────┬──────────────────┘
                                                         │
                                                         ▼
                                          ┌─────────────────────────────────┐
                                          │    Cloud LLM Schema Structuring │
                                          │    (Groq Llama-3.3-70B DTO)     │
                                          └──────────────┬──────────────────┘
                                                         │
                                                         ▼
                                          ┌─────────────────────────────────┐
                                          │ Deterministic Statutory Engine  │
                                          │ (26 Legal Checks - Rule 2026.09)│
                                          └─────────────────────────────────┘
```

---

## 2. Fast Client-Side Quality Gate

Before transmitting megabytes of imagery over cellular networks, the client evaluates a fast heuristic quality gate (runtime: ~38 ms):

| Quality Metric | Evaluation Algorithm | Rejection Threshold | Remediation Guidance Surfaced |
|---|---|---|---|
| **Sharpness / Blur** | Variance of Laplacian operator | Score < 60.0 (Severe Blur) | *"Hold camera steady; focus on label text"* |
| **Specular Glare** | Percentage of pixels with Luma > 245 | Glare Area > 15.0% | *"Tilt package slightly to eliminate reflection"* |
| **Exposure (Underexposure)** | Mean Luma channel | Mean Luma < 35.0 | *"Move to brighter lighting or turn on torch"* |
| **Exposure (Overexposure)** | Mean Luma channel | Mean Luma > 240.0 | *"Reduce direct overhead lighting"* |
| **Edge Framing** | Bounding contour proximity to sensor border | Margin < 2% of frame | *"Move camera back; package edges clipped"* |
| **Package Tilt** | Minimum bounding box orientation angle | Absolute Tilt > 25.0° | *"Hold camera parallel to package panel"* |

**Quality States:**
- `READY`: All metrics within optimal bands. Auto-capture or instant upload allowed.
- `READY_WITH_WARNINGS`: Mild glare (5–15%) or mild blur (60–100). Proceed with advisory notification.
- `RETAKE_REQUIRED`: Image exceeds rejection thresholds. Upload blocked until retaken.

---

## 3. Geometry Detection & Perspective Rectification

Packages captured in the field frequently exhibit perspective skew due to viewing angles or non-planar surfaces.

```text
┌──────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│  Raw Panel Image │ ──> │ Canny Edge & Dilate │ ──> │ 4-Point Quad Contour│
└──────────────────┘     └─────────────────────┘     └──────────┬──────────┘
                                                                │
                                                                ▼
┌──────────────────┐     ┌─────────────────────┐     ┌─────────────────────┐
│ Rectified Plane  │ <── │ Perspective Warp    │ <── │ Homography Matrix H │
└──────────────────┘     └─────────────────────┘     └─────────────────────┘
```

1. **Panel Quad Detection (`detect_panel_quad`):** Identifies the quadrilateral bounding the package display panel.
2. **Perspective Rectification (`rectify`):** Applies a four-point perspective homography warp, transforming angled display surfaces into a flattened, fronto-parallel coordinate plane.
3. **Principal Display Panel (PDP) Calculation:**
   - **Rectangular packages:** Area = Height × Width
   - **Cylindrical containers:** Area = 40% of (Height × Circumference) = $0.40 \times H \times (\pi \times D)$
   - **Blown-moulded / Special shapes:** Area calculated based on total surface area formula under Rule 7 Table-I.
4. **Physical Scale Computation (`compute_scale`):**
   - Matches detected reference objects (ID-1 card = 85.60 mm width; 5 INR coin = 23.00 mm diameter).
   - Computes millimeter-per-pixel factor: $\text{mm\_per\_pixel} = \frac{\text{Known Dimension (mm)}}{\text{Detected Bounding Pixels}}$.

---

## 4. Cloud OCR Pipeline Abstraction & Fallback Resilience

NiyamNetra abstracts OCR behind a pluggable interface (`ocr/` and `ocr_engine.py`) designed for zero-RAM local footprint, resilient cloud fallback, and leak-free operational diagnostics:

### 4.1 Provider Selection & Fallback Order
- **Selection Modes (`OCR_PROVIDER`):**
  - `auto` (Default): Evaluates candidate providers through `FallbackOCRProvider` in strict sequence:
    1. **Google Cloud Vision (Primary):** Highest accuracy for Latin and Devanagari script, word-level bounding polygons, and dense numeral typography.
    2. **OCR.space (Secondary):** Cloud API fallback triggered upon Google Vision quota exhaustion, network timeout, or HTTP 5xx errors.
    3. **Azure Vision (Tertiary):** Enterprise cloud fallback for multi-provider resilience.
  - `explicit`: Targets a specific single provider (e.g. `google_vision`, `ocr_space`, `azure`). If the named provider is unsupported or invalid, diagnostics flag `readiness: invalid_configuration` and `verification_status: unsupported_provider`.
- **Credential Pre-Flight Checks:** If a configured candidate's required API key or service account is absent, the fallback provider skips it safely without unhandled crashes.

### 4.2 Failure Classification, Timeout Discipline & Sanitized Errors
- **Connection & Read Timeouts:** Outbound provider HTTP requests enforce strict timeouts (default 10.0s connect, 25.0s read) via `httpx`.
- **Diagnostic Error Categorization (`classify_provider_error`):** Structured error classification distinguishes failure modes:
  - `auth_error`: HTTP 401/403, invalid credentials, or missing authentication.
  - `billing_error`: Cloud billing account disabled, suspended, or payment failure.
  - `rate_limited`: HTTP 429 quota exhaustion or concurrency rate throttling.
  - `timeout`: Network connection or read timeout.
  - `network_error`: DNS resolution failure, connection drop, or unreachable gateway.
  - `all_failed`: All candidates in the fallback chain exhausted without a valid result.
  - `provider_error`: Upstream 5xx internal server errors.
- **Credential & Secret Scrubbing:** Error messages, exception tracebacks, and operational logs pass through `sanitize_sensitive_text()`. Regex scrubbers replace API keys (`apikey=...`, `key=...`), Authorization Bearer tokens, and sensitive query parameters with `[REDACTED]`.
- **Safe Telemetry in `/health` (`get_ocr_health_diagnostics`):** Exposes operational readiness (`unconfigured`, `ready_unverified`, `live_verified`, `degraded`, `all_failed`, `unauthorized`, `billing_error`) and provider priority list without exposing secret values.
- **Operational Boundary:** A healthy `/health` endpoint proves backend service responsiveness; it does **not** certify that full live OCR ➔ LLM ➔ deterministic rules processing has succeeded on live external APIs.
- **Pending Credential Rotation:** The OCR.space credential previously exposed in historical commits requires authorized manual rotation in the Render environment configuration.

### 4.3 The Legal Safety Invariant
> **Critical Statutory Principle:** An optical character recognition failure or cloud provider outage must **NEVER** be converted into a false legal violation against a merchant.

When all OCR providers fail, time out, or return unreadable text:
- The scan outcome is stamped `not_assessed` (never `violation`).
- Dependent checks are stamped `not_assessed` with explicit reasons (e.g. *"Technical OCR/provider error: All candidate OCR providers failed"*).
- The scan is routed to the Officer Review Queue under the `technical_recovery` subtype.
- Officers can trigger technical reassessment or upload a clearer capture without initiating prosecution notices.
- Unaffected panels or independent evidence sources still support the rules that genuinely can be evaluated.

### 4.4 Evidence Cache Invalidation Invariant
- **Cache Fingerprint Gate (`assessment_input_fingerprint`):** The assessment engine checks evidence set integrity before reusing cached OCR or LLM results.
- **Automatic Purge:** Uploading a new panel image, replacing an existing panel, or updating geometry parameters purges `ocr_cache_hash` and invalidates cached findings.
- **Failed Invalidation:** Failed OCR or LLM results are strictly prohibited from entering persistent success caches; they cannot be reused as successful assessments.

### 4.5 Output Schema (`OcrResult`)
- `full_text`: Concatenated plain text of all recognized lines.
- `lines`: Array of line objects (`OcrLine`) with normalized text and polygon coordinates.
- `words`: Array of individual words (`OcrWord`) with bounding boxes `[ymin, xmin, ymax, xmax]` normalized to image dimensions.
- `mean_confidence`: Average optical recognition confidence (0.0 to 1.0).

### 4.6 Runtime Dependency Profiles & Docker Deployment
- **Cloud-First Render Profile (`requirements-render.txt`):**
  - Optimized for 512 MB RAM footprint constraints on cloud container tiers.
  - Excludes heavy native vision and local OCR binaries (`paddlepaddle`, `tesseract-ocr`, `pyzbar`).
  - Employs lightweight HTTP client and headless image libraries (`httpx`, `opencv-python-headless`).
  - *Cloud Key Mandate:* At least one cloud provider API key (`GOOGLE_VISION_API_KEY`, `OCR_SPACE_API_KEY`, or `AZURE_VISION_KEY` / `AZURE_VISION_ENDPOINT`) must be defined in the hosting environment (`.env` or Render dashboard). If no cloud provider key is configured, the pipeline safely falls back to honest `not_assessed` rather than triggering runtime crashes or false violations.
- **Docker Container Local OCR Profile (`Backend/Dockerfile`):**
  - For standalone container deployments requiring local OCR without outbound cloud dependencies:
    - Base system packages: `apt-get install -y tesseract-ocr libzbar0`
    - Python runtime libraries: `pytesseract` and `pyzbar` for local multi-lingual OCR (`eng+hin`) and 1D/2D barcode symbol decoding.
- **Provider Smoke Testing Tooling:**
  - Verification CLI: `python scripts/smoke_ocr_provider.py <image.jpg> --live-provider-call` (detailed in Section 8) confirms provider reachability, credentials, and normalized token coordinates against live endpoints.

---

## 5. Evidence-Grounded Cloud LLM Structuring

The output of optical character recognition on real-world packaging is frequently noisy, disjointed, or fragmented across lines. NiyamNetra utilizes an evidence-grounded Cloud LLM (Groq Llama-3.3-70B with Google Gemini Flash fallback) strictly for **entity extraction and semantic normalization**.

### 5.1 Strict Extraction Invariant
The LLM is prompted with strict JSON schema constraints. It is forbidden from guessing, completing missing numbers, or asserting legality.

```json
{
  "commodity_name": "Refined Sunflower Oil",
  "brand_name": "Fortune",
  "manufacturer_name": "Adani Wilmar Limited",
  "manufacturer_address": "Fortune House, Near Navrangpura Railway Crossing, Ahmedabad 380009",
  "net_quantity": {
    "value": 1.0,
    "unit": "L",
    "raw_text": "1 Litre / 910 g"
  },
  "mrp": {
    "currency": "INR",
    "amount": 145.0,
    "raw_text": "MRP Rs. 145.00 (incl. of all taxes)"
  },
  "dates": {
    "mfg_date": "2026-08-15",
    "expiry_date": "2027-05-15"
  },
  "consumer_care": {
    "phone": "1800-233-9999",
    "email": "customercare@adaniwilmar.in"
  },
  "country_of_origin": "India"
}
```

### 5.2 Field Provenance & Grounding
Every extracted entity carries an explicit `FieldProvenance` record:
- `source_line`: Exact OCR line from which the value was extracted.
- `panel`: Source panel (`front`, `back`, `mrp`, `batch`).
- `confidence`: Optical and extraction confidence score.
- `bbox`: Bounding box coordinates on the derived analysis image.

---

## 6. Security & Credential Isolation

To prevent credential leakage:
1. **Zero Client-Side AI Keys:** The mobile app bundle (`release/NiyamNetra-v1.0.5-release-candidate.apk`) contains zero OCR API keys, Groq tokens, or Gemini credentials. All AI calls execute strictly server-side within the protected Render backend environment.
2. **SSRF-Guarded Outbound Fetching:** When evaluating e-commerce product links (Rules 6(10) & 6(10A)), the backend resolves DNS names through an IP-validation sandbox, blocking private IP ranges (`10.0.0.0/8`, `127.0.0.0/8`, `169.254.169.254`) and preventing Server-Side Request Forgery.

---

## 7. Measured Production Latency Telemetry

Measured across 10 repeated full-pipeline evaluations on live Render FastAPI infrastructure and physical Android hardware:

| Stage | Operation | Mean Duration | p50 Duration | p95 Duration |
|---|---|---|---|---|
| 1 | Camera Capture & Analysis Derivation | 142 ms | 138 ms | 165 ms |
| 2 | Fast Quality Gate Evaluation | 38 ms | 36 ms | 48 ms |
| 3 | HTTPS Upload & SHA-256 Storage | 320 ms | 310 ms | 380 ms |
| 4 | Cloud OCR Pipeline | 610 ms | 590 ms | 780 ms |
| 5 | Cloud LLM Structuring (Groq) | 415 ms | 405 ms | 510 ms |
| 6 | Deterministic Statutory Rule Engine | 28 ms | 26 ms | 35 ms |
| 7 | UI Reactive State & Rendering | 45 ms | 42 ms | 55 ms |
| **Total (Cold-Path)** | **Full Pipeline Execution** | **1,598 ms** | **1,547 ms** | **1,973 ms** |
| **Fast-Path (Cache Hit)** | **Validated Re-Assessment** | **< 45 ms** | **< 40 ms** | **< 60 ms** |

*Result:* Cold-path turnaround is consistently sub-2-second. When evidence remains unchanged, the validated fast path rehydrates cached OCR and LLM findings in under 45 ms, enabling instantaneous re-checks.

---

## 8. Real-Provider OCR Smoke-Test Procedure

To safely verify configured cloud OCR provider credentials and external network egress without executing full end-to-end statutory assessments or creating database records, NiyamNetra provides an operational smoke-test utility (`Backend/scripts/smoke_ocr_provider.py`):

### 8.1 Command Syntax & Explicit Opt-In
To prevent accidental consumption of paid cloud quotas or uncontrolled network calls during automated test suites, the script strictly requires the `--live-provider-call` flag to permit external API calls:

```bash
cd Backend
python scripts/smoke_ocr_provider.py <path_to_packaging_image.jpg> --live-provider-call
```

Optional arguments:
- `--provider [auto|google_vision|ocr_space|azure]`: Override provider selection.
- `--json`: Output machine-readable JSON metrics.

### 8.2 Operational Safety Boundaries
- **Zero Database Writes:** The smoke test interacts solely with the OCR provider abstraction layer. It performs zero SQL queries, creates no inspections or scans, and inserts no finding rows.
- **Credential Scrubbing:** API keys, authorization tokens, and raw extracted text are strictly redacted from stdout/stderr, reporting only high-level metadata (line count, mean confidence, candidate trace).
- **Exit Code Contract:**
  - `0`: Successful text extraction by configured provider.
  - `1`: Failure (all candidates failed or zero text recognized).
  - `2`: Blocked (execution attempted without explicit `--live-provider-call` flag).

> **Verification Boundary:** A successful smoke test confirms that the cloud OCR provider can receive an image and return word bounding boxes. It does not replace full end-to-end integration testing of the OCR ➔ LLM ➔ deterministic legal rule engine pipeline.

