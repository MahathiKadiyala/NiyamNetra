# 15. Safe Data Audit: Stale Drafts & Historical Inspection Records

**Authoritative Guide for Module 6.2 Data Hygiene & Advisory Audit**  
**Script Location:** `Backend/audit_stale_records.py`

---

## 1. Overview & Core Invariants

The `audit_stale_records.py` utility provides a strictly **read-only**, database-authoritative audit of inspection records, scans, findings, and evidence artifacts in NiyamNetra.

### Strict Safety Guarantees
1. **Zero Database Modifications:** The default and only execution mode is read-only. SQLAlchemy session events intercept and block any `before_flush` or write calls. In PostgreSQL, `SET TRANSACTION READ ONLY` is explicitly executed.
2. **No Data Purging or State Transitions:** No records are deleted, archived, finalized, or adjudicated. The tool is purely advisory and diagnostic.
3. **No Credential or PII Leaks:** Database URLs have passwords automatically redacted (`sanitize_url`). Personally identifiable information (e.g. inspector employee IDs, phone numbers, notes, raw OCR strings) is excluded from summaries and machine-readable exports.
4. **Authoritative Consistency:** Integrates with the Module 6.1 queue contract (`queries.get_canonical_queue_metrics`) to ensure queue totals, scan identities, and category definitions agree across endpoints and audits.

---

## 2. Local Execution

### Prerequisites
Activate the backend virtual environment:
```bash
cd Backend
.venv\Scripts\activate   # Windows
# or: source .venv/bin/activate  # Linux/macOS
```

### Running the Audit

1. **Standard Audit against Development Database:**
   ```bash
   python audit_stale_records.py
   ```

2. **Custom Database Target (Read-Only):**
   ```bash
   python audit_stale_records.py --db-url "sqlite:///niyamnetra.db"
   ```

3. **Customizing the Stale Draft Age Threshold (e.g., 30 days):**
   ```bash
   python audit_stale_records.py --stale-days 30
   ```
   *Default threshold:* **14 days**.

4. **Exporting Machine-Readable JSON and Tabular CSV Reports:**
   ```bash
   python audit_stale_records.py --json-out out/audit_report.json --csv-out out/audit_classifications.csv
   ```

5. **Fast Mode (Skip Disk Evidence File Existence Checks):**
   ```bash
   python audit_stale_records.py --skip-file-check
   ```

---

## 3. Classification Taxonomy & Semantics

The audit inspects each entity and assigns evidence-backed primary classifications and faceted tags:

| Classification | Target Entity | Trigger Criteria & Semantics | Actionable Guidance |
| :--- | :--- | :--- | :--- |
| `ACTIVE_DRAFT` | Inspection | Draft inspection with scans, aged $\le$ stale threshold (default 14 days). | Resumable by field inspector via mobile app flow (`GET /inspections?status=draft`). |
| `POTENTIALLY_STALE_DRAFT` | Inspection | Draft inspection with scans, aged $>$ stale threshold. Inactive for a prolonged period. | Review by supervisor before archival consideration. Age alone does **not** warrant automatic deletion. |
| `DRAFT_WITHOUT_SCANS` | Inspection | Draft visit created with 0 packages/scans recorded. | Review for archival consideration; inspector exited visit without capturing items. |
| `INCOMPLETE_EVIDENCE` | Scan | Scan where checks were not assessed due to incomplete packaging coverage. | Field revisit or recapture needed if visit is active. |
| `TECHNICAL_ASSESSMENT_FAILURE` | Scan | `overall_result == 'not_assessed'` due to external OCR timeout, provider outage, or technical error. | Safe candidate for automated rules engine retry without field visit. |
| `RECAPTURE_CANDIDATE` | Scan | Scan with 0 attached images in `scan_images`, or severe blur/glare defect notes. | Inspector recapture task via Phase 6B camera flow. |
| `PENDING_OFFICER_REVIEW` | Scan / Insp | Submitted scan with unconfirmed violations (`engine_verdict == 'fail'`, `human_verdict IS NULL`) or unresolved low confidence ($< 0.60$). | Legal Metrology Officer review and adjudication via Phase 5A review queue. |
| `CONFIRMED_VIOLATION_AWAITING_ACTION` | Scan / Insp | Submitted scan with confirmed statutory violations (`human_verdict == 'fail'`). | Enforcement notice issuance / compounding under Section 36. |
| `ALREADY_RESOLVED` | Scan / Insp | Inspection or scan with authoritative terminal resolution in `AuditLog` (`review_resolved`, `review_dismissed`). | Already completed; excluded from active officer review queue. |
| `POSSIBLE_TEST_OR_SEED_DATA` | Scan / Insp | Concrete test markers present (e.g. `@example.test` email, `Seed Brand`, synthetic catalog hash `"c"*64`, test moniker in notes). | Segregate from production analytics. Never classified based solely on age or small ID. |
| `ORPHANED_OR_INCONSISTENT_RECORD` | Record | Inconsistent assessment state (e.g. compliant result with failing findings), broken FK reference, or missing evidence file on disk. | Technical investigation required. |
| `NEEDS_MANUAL_CLASSIFICATION` | Record | Ambiguous provenance or incomplete metadata where evidence is insufficient to conclude automated classification. | Manual legal inspection required before taking any action. |

---

## 4. Maintenance Guidance & Q&A

The generated report directly answers the maintainer's 6 key operational questions:

1. **Which records may be safely reviewed for archival consideration?**
   - Identifies inactive drafts older than threshold (`POTENTIALLY_STALE_DRAFT`), zero-scan drafts (`DRAFT_WITHOUT_SCANS`), already resolved inspections (`ALREADY_RESOLVED`), and confirmed test fixtures (`POSSIBLE_TEST_OR_SEED_DATA`).
   - *Policy:* Archival requires human supervisor approval. Records are never automatically purged.
2. **Which drafts should be resumed by the inspector?**
   - Active draft visits (`ACTIVE_DRAFT`) with recorded items, resumable by the field officer.
3. **Which cases need a technical OCR retry?**
   - Scans classified under `TECHNICAL_ASSESSMENT_FAILURE`.
4. **Which packages require recapture?**
   - Packages with 0 images or severe blur flagged under `RECAPTURE_CANDIDATE`.
5. **Which findings still need officer adjudication?**
   - Unconfirmed violations under `PENDING_OFFICER_REVIEW`.
6. **Which records require manual investigation before any cleanup?**
   - Records tagged `NEEDS_MANUAL_CLASSIFICATION` and `ORPHANED_OR_INCONSISTENT_RECORD`.

---

## 5. Privacy & Sensitive Content Considerations

- **Credential Redaction:** Database URLs are masked before output.
- **No PII Exposure:** User password hashes, auth tokens, shop owner phone numbers, and raw OCR transcription text are omitted.
- **Identifier Stability:** Uses stable record IDs (`inspection_id`, `scan_id`, `finding_id`) rather than copying full database rows into reports.

---

## 6. Test Verification & Cross-References

- **Unit & Regression Test Suite:** [`Backend/tests/test_audit_stale_records.py`](file:///C:/Skills/Projects/NiyamNetra/Backend/tests/test_audit_stale_records.py) (18 automated scenarios covering zero-mutation invariants, transaction isolation, classification accuracy, and SQLite/PostgreSQL URL sanitization).
- **Related Authoritative Specifications:**
  - [06_DATA_MODEL.md](file:///C:/Skills/Projects/NiyamNetra/documents/06_DATA_MODEL.md) — Authoritative schema definitions for `Inspection`, `Scan`, `Finding`, and `AuditLog`.
  - [07_API_AND_INTEGRATION.md](file:///C:/Skills/Projects/NiyamNetra/documents/07_API_AND_INTEGRATION.md) — Review queue query endpoints and draft inspection discovery.
  - [08_SECURITY_AND_PRIVACY.md](file:///C:/Skills/Projects/NiyamNetra/documents/08_SECURITY_AND_PRIVACY.md) — Read-only session guarantees and PII sanitization policies.
  - [10_TESTING_AND_QUALITY.md](file:///C:/Skills/Projects/NiyamNetra/documents/10_TESTING_AND_QUALITY.md) — Automated regression test suite metrics.
