# Offline Synchronization & Evidence Resilience

**Document Code:** `DOC-09`  
**Applies to:** Offline Storage, Idempotent Queueing, Evidence Synchronization, and Conflict Resolution  
**Architecture:** Local-First, Network-Resilient, Append-Only Synchronization  

---

## 1. Offline Architectural Guarantees

In statutory field operations, enforcement officers frequently inspect storage premises, manufacturing basements, and remote markets with intermittent or non-existent cellular coverage. NiyamNetra guarantees:

1. **Local-First Persistence:** Evidence capture and inspector data entry never block on network availability. Every record is committed locally before transmission is attempted.
2. **Authoritative Original Immutability:** Original camera files stored locally are preserved bit-for-bit. Synchronization never compresses, re-encodes, or mutates the source evidence.
3. **Exactly-Once Semantics:** Network dropouts during uploads or flushes never produce duplicate inspections, double scans, or corrupted findings.
4. **Transparent Offline Provenance:** Every record created offline carries an immutable flag (`edited_offline = true`) and records the client-to-server time delta (`clock_skew_seconds`).

---

## 2. Structured Offline Queue State Machine

The client offline engine (`Frontend_App/offline/`) manages task lifecycles through a deterministic state machine:


```text
                      ┌────────────────────────────┐
                      │    User Initiates Action   │
                      └────────────────────────────┘
                                     │
                                     ▼
                      ┌────────────────────────────┐
                      │       QUEUED_OFFLINE       │
                      │  - Saved to local SQLite   │
                      │  - Assigned client UUIDv4  │
                      └────────────────────────────┘
                                     │
                         Network Connection Detected
                                     │
                                     ▼
                      ┌────────────────────────────┐
                      │         UPLOADING          │
                      │  - Lock acquired           │
                      │  - Idempotency-Key sent    │
                      └────────────────────────────┘
                                     │
                     ┌───────────────┴───────────────┐
           HTTP 2xx Success                  Network Error / 5xx
                     │                               │
                     ▼                               ▼
       ┌───────────────────────────┐   ┌───────────────────────────┐
       │          SYNCED           │   │      RETRY_SCHEDULED      │
       │ - Marked complete         │   │ - Exponential backoff     │
       │ - Local cache updated     │   │ - Max 5 attempts          │
       │ - Server ID recorded      │   └───────────────────────────┘
       └───────────────────────────┘                 │
                                        5 Consecutive Failures
                                                     │
                                                     ▼
                                       ┌───────────────────────────┐
                                       │        SYNC_FAILED        │
                                       │ - Requires officer action │
                                       │ - Preserved in local queue│
                                       └───────────────────────────┘
```

### Queue Entry Schema (Local SQLite):
- `queue_id`: Unique client-generated UUIDv4.
- `action_type`: `CREATE_INSPECTION`, `CREATE_SCAN`, `UPLOAD_IMAGE`, `SUBMIT_INSPECTION`, `ADJUDICATE`.
- `payload`: JSON-serialized API request body.
- `evidence_uri`: Local filesystem path to original photo (`file:///...`).
- `idempotency_key`: Stable cryptographic string reused across retries.
- `attempts`: Retry counter (default 0).
- `created_at`: Monotonic local timestamp.
- `status`: `QUEUED_OFFLINE`, `UPLOADING`, `SYNCED`, `RETRY_SCHEDULED`, `SYNC_FAILED`.

---

## 3. Idempotency & Duplicate Prevention Contract

During flaky cellular handover, an HTTP POST request may reach the backend and commit to PostgreSQL, but the subsequent HTTP response may drop before reaching the mobile client. Without strict idempotency, an automated client retry would create a duplicate scan row.

### 3.1 Idempotency Key Handling (`routers/scans.py`, `routers/inspections.py`)
1. **Client Header:** Client sends `Idempotency-Key: <uuid>`.
2. **Server Verification:**
   - The server queries `idempotency_keys` table for `key = <uuid>`.
   - **Case A (Fresh Key):** The server processes the request, commits to PostgreSQL, writes the response status code and JSON payload into `idempotency_keys`, and returns the response.
   - **Case B (Replayed Key):** The server intercepts the duplicate request before database writes, reads the cached status code and payload from `idempotency_keys`, and returns the cached response immediately (`200 OK` / `201 Created`).
3. **TTL Expiration:** Cached idempotency keys are retained for 24 hours.

### 3.2 Idempotent Image Re-upload
If a client re-transmits an image upload that was already ingested:
- The server computes the SHA-256 digest of the incoming bytes.
- It queries `scan_images` for `scan_id == current_scan.id AND sha256 == incoming_sha`.
- If an exact match exists, the server returns the existing `image_id` with `status: 200 OK` and `"replayed": true`, preventing duplicate rows in the immutable evidence ledger.

---

## 4. Clock Skew Tracking & Temporal Integrity

An adversary could attempt to back-date or forward-date inspections by altering their mobile device clock. NiyamNetra protects temporal integrity through dual timestamps:

1. **Client Timestamp:** Recorded from device system clock at moment of capture (`local_created_at`).
2. **Server Arrival Timestamp:** Recorded from authoritative NTP-synchronized server clock (`created_at`).
3. **Clock Skew Calculation:**
   $$\text{clock\_skew\_seconds} = |\text{Server Time} - \text{Client Time}|$$
4. **Auditing Boundary:**
   - If $|\text{clock\_skew\_seconds}| > 300\text{ seconds}$ (5 minutes), the inspection record is flagged in the supervisory review queue as `OFFLINE_TIME_ANOMALY`.
   - The skew is recorded in `inspections.clock_skew_seconds` and printed in the formal inspection report.

---

## 5. Offline Smart Recapture & Reassessment Workflow

When an inspector completes an inspection offline that produces `REVIEW_REQUIRED`:

1. **Offline Task Generation:** The local client engine analyzes assessment findings and surfaces pending capture tasks (e.g. `CAP_BACK_PANEL_MISSING`).
2. **Offline Task Fulfillment:** The officer captures the missing panel while still on-site. The new image is attached locally to the scan.
3. **Unified Batch Sync:** When connectivity returns, the sync manager flushes:
   - Initial inspection visit
   - Initial scan and front panel image
   - Fulfill capture task action with back panel image
   - Reassessment request
4. **Server Reassessment Execution:** The backend runs the authoritative legal compliance engine (19 executable checks across all registered rules) against all aggregated panels, updating the overall result to `COMPLIANT` or `VIOLATION` in a single coherent server transaction.

---

## 6. Offline Review Queue Caching & Bounded Storage

In addition to offline visit enqueueing, NiyamNetra provides an offline review queue cache (`Frontend_App/services/queue.js` and `review_queue_cache.js`) enabling officers to browse, search, and review submitted inspections during network dropouts:

### 6.1 Query-Scoped Key Architecture
Review queue cache keys are strictly isolated by authorized user scope and query parameters:
$$\text{cache\_key} = \text{review\_queue}:\{\text{scope}\}:\{\text{query\_hash}\}$$
Where:
- `scope` embeds `user_id`, `role`, and `authorized_jurisdiction` (e.g. `user_1:inspector:hyd_central`).
- `query_hash` is an MD5/SHA-256 digest of normalized filters: `status_filter`, `reason`, `subtype`, `area`, `page`, and `limit`.
- Summary metrics use distinct user-scoped keys: `review_queue_summary:{scope}:{filter_hash}`.

### 6.2 Bounded Retention & LRU Eviction
To prevent uncontrolled local storage consumption on field devices:
- **Maximum 30 Cached Pages:** The cache enforces a hard ceiling of 30 stored queue pages per device.
- **LRU Eviction:** When the 30-page limit is reached, the least recently accessed page file is automatically evicted.
- **24-Hour TTL Ceiling:** Cache entries older than 24 hours are expired. Entries between 1 hour and 24 hours are flagged `isStale: true` but remain viewable offline until reconnection.

### 6.3 Invalidation & Revision Discipline
The review queue cache maintains strict consistency across local and server mutations:
- **Queue Mutations:** Adjudicating a finding, resolving an inspection, fulfilling a recapture task, finalizing an inspection, or completing background sync flushes affected cached queue pages and advances the local `dataRevision` counter.
- **In-Flight Response Protection:** In-flight network responses initiated before a mutation or logout carry an obsolete revision or session generation; they are safely discarded upon arrival and forbidden from overwriting freshly updated cache.
- **Truthful Offline Miss:** An offline cache miss triggers a clear "Offline — Data Unavailable" state. The client never fabricates empty queues or displays misleading zero-count metrics.

