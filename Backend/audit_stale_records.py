"""audit_stale_records.py — Read-Only Audit of Stale Drafts and Historical Inspection Records.

Module 6.2: Safe, reliable, database-authoritative audit for NiyamNetra.

Identifies potentially stale, incomplete, or inconsistent inspection data without
modifying, archiving, deleting, or altering any database record.

Key Invariants:
1. STRICTLY READ-ONLY: Default and only operation is read-only. Flushes, commits,
   and data modifications are explicitly blocked at the SQLAlchemy session level.
2. NO CREDENTIAL LEAKS: Database connection credentials, bearer tokens, passwords,
   and personally identifiable information are never exposed in reports or logs.
3. DOMAIN CONSISTENCY: Directly integrates with Module 6.1 canonical queue metrics
   without adding disparate entities together or double-counting.
4. EVIDENCE-BASED CLASSIFICATION: Classifications are supported by concrete database
   provenance. Records with inconclusive evidence are marked NEEDS_MANUAL_CLASSIFICATION.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import os
import sys
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Ensure Backend directory is in sys.path and load Backend/.env if present
_backend_dir = Path(__file__).resolve().parent
if str(_backend_dir) not in sys.path:
    sys.path.insert(0, str(_backend_dir))

_env_file = _backend_dir / ".env"
if _env_file.exists():
    try:
        with open(_env_file, "r", encoding="utf-8") as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _v = _line.split("=", 1)
                    _k = _k.strip()
                    _v = _v.strip().strip("'\"")
                    if _k not in os.environ:
                        os.environ[_k] = _v
    except Exception:
        pass

from sqlalchemy import create_engine, event, func, or_, select, text
from sqlalchemy.engine.url import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

# Import domain models and queries
from database import Base
from models import AuditLog, Finding, Inspection, Scan, ScanImage, Store, User
from queries import get_canonical_queue_metrics


# ---------------------------------------------------------------------------
# Constants and Classification Tags
# ---------------------------------------------------------------------------
DEFAULT_STALE_DAYS = 14

CLASSIFICATION_ACTIVE_DRAFT = "ACTIVE_DRAFT"
CLASSIFICATION_POTENTIALLY_STALE_DRAFT = "POTENTIALLY_STALE_DRAFT"
CLASSIFICATION_DRAFT_WITHOUT_SCANS = "DRAFT_WITHOUT_SCANS"
CLASSIFICATION_INCOMPLETE_EVIDENCE = "INCOMPLETE_EVIDENCE"
CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE = "TECHNICAL_ASSESSMENT_FAILURE"
CLASSIFICATION_RECAPTURE_CANDIDATE = "RECAPTURE_CANDIDATE"
CLASSIFICATION_PENDING_OFFICER_REVIEW = "PENDING_OFFICER_REVIEW"
CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION = "CONFIRMED_VIOLATION_AWAITING_ACTION"
CLASSIFICATION_ALREADY_RESOLVED = "ALREADY_RESOLVED"
CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA = "POSSIBLE_TEST_OR_SEED_DATA"
CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD = "ORPHANED_OR_INCONSISTENT_RECORD"
CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION = "NEEDS_MANUAL_CLASSIFICATION"

ALL_CLASSIFICATIONS = [
    CLASSIFICATION_ACTIVE_DRAFT,
    CLASSIFICATION_POTENTIALLY_STALE_DRAFT,
    CLASSIFICATION_DRAFT_WITHOUT_SCANS,
    CLASSIFICATION_INCOMPLETE_EVIDENCE,
    CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE,
    CLASSIFICATION_RECAPTURE_CANDIDATE,
    CLASSIFICATION_PENDING_OFFICER_REVIEW,
    CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION,
    CLASSIFICATION_ALREADY_RESOLVED,
    CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA,
    CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD,
    CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION,
]

KNOWN_SEED_STORE_NAMES = {
    "sri balaji supermarket",
    "anand general store",
    "vasavi wholesale depot",
    "test kirana",
    "test store",
}

KNOWN_SEED_USER_EMAILS = {
    "admin@example.test",
    "inspector1@example.test",
    "inspector2@example.test",
}


# ---------------------------------------------------------------------------
# Safe Session Management (Strictly Read-Only)
# ---------------------------------------------------------------------------
def _prevent_flush_handler(session, flush_context, instances):
    raise RuntimeError(
        "Write attempt blocked: audit_stale_records is running in strict read-only mode."
    )


def create_readonly_session(db_url: str) -> Session:
    """Create a SQLAlchemy session guaranteed to be read-only."""
    is_sqlite = db_url.startswith("sqlite")
    connect_args: dict[str, Any] = {}

    if is_sqlite:
        connect_args["check_same_thread"] = False
        eng = create_engine(
            db_url,
            connect_args=connect_args,
            poolclass=StaticPool if ":memory:" in db_url else None,
        )
    else:
        # PostgreSQL / remote
        connect_args["connect_timeout"] = 10
        eng = create_engine(
            db_url,
            pool_pre_ping=True,
            connect_args=connect_args,
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )

    # Attach event listener to block any flush
    session_factory = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    session = session_factory()
    event.listen(session, "before_flush", _prevent_flush_handler)

    if not is_sqlite:
        try:
            session.execute(text("SET TRANSACTION READ ONLY;"))
        except Exception:
            pass

    return session


def sanitize_url(url_str: str) -> str:
    """Mask password and sensitive credentials from database URLs."""
    if not url_str:
        return "none"
    try:
        u = make_url(url_str)
        return u.render_as_string(hide_password=True)
    except Exception:
        if "@" in url_str:
            prefix, rest = url_str.split("@", 1)
            return f"***:***@{rest}"
        return url_str


# ---------------------------------------------------------------------------
# Data Classes
# ---------------------------------------------------------------------------
@dataclass
class EvidenceFileCheck:
    image_id: int
    scan_id: int
    panel: str
    sha256: str
    file_path: str
    file_exists: bool
    is_accessible: bool
    reason: str | None = None


@dataclass
class ScanAuditRecord:
    scan_id: int
    inspection_id: int
    overall_result: str
    checks_total: int
    checks_assessed: int
    primary_classification: str
    classifications: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    image_count: int = 0
    missing_images: list[int] = field(default_factory=list)
    findings_count: int = 0
    unconfirmed_violations: int = 0
    confirmed_violations: int = 0
    low_confidence_count: int = 0


@dataclass
class InspectionAuditRecord:
    inspection_id: int
    store_id: int | None
    user_id: int | None
    status: str  # draft | submitted
    inspection_date_iso: str
    created_at_iso: str | None
    age_days: int
    age_band: str
    primary_classification: str
    classifications: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    scans_count: int = 0
    is_resumable: bool = False
    has_unresolved_findings: bool = False
    is_adjudicated_resolved: bool = False
    scans: list[ScanAuditRecord] = field(default_factory=list)


@dataclass
class AuditResultSet:
    metadata: dict[str, Any]
    canonical_queue_metrics: dict[str, Any]
    total_inspections: int
    total_scans: int
    total_findings: int
    total_scan_images: int
    inspections_by_status: dict[str, int]
    drafts_by_age_band: dict[str, int]
    classification_counts: dict[str, int]
    scans_classification_counts: dict[str, int]
    inspections: list[InspectionAuditRecord]
    inconsistent_records: list[dict[str, Any]]
    missing_evidence_files: list[EvidenceFileCheck]
    maintenance_qa: dict[str, Any]


# ---------------------------------------------------------------------------
# Core Classification Logic
# ---------------------------------------------------------------------------
def _compute_age_band(age_days: int) -> str:
    if age_days < 1:
        return "< 24 hours"
    elif age_days <= 7:
        return "1 - 7 days"
    elif age_days <= 30:
        return "8 - 30 days"
    elif age_days <= 90:
        return "31 - 90 days"
    else:
        return "> 90 days"


def _is_explicit_test_data(
    insp: Inspection,
    store: Store | None,
    user: User | None,
    scans: list[Scan],
) -> tuple[bool, str]:
    """Identify records with explicit test/seed markers.
    NEVER classifies solely on age or small ID.
    """
    reasons = []

    # 1. User markers
    if user:
        email = (user.email or "").lower()
        full_name = (user.full_name or "").lower()
        emp_id = (user.employee_id or "").upper()
        if email in KNOWN_SEED_USER_EMAILS or email.endswith("@example.test"):
            reasons.append(f"User email '{user.email}' belongs to demo/test domain")
        if "seed administrator" in full_name:
            reasons.append("User full_name indicates seed administrator")
        if emp_id.startswith("TEST-") or emp_id.startswith("MOCK-"):
            reasons.append(f"Employee ID '{user.employee_id}' has test prefix")

    # 2. Store markers
    if store:
        s_name = (store.name or "").lower().strip()
        if s_name in KNOWN_SEED_STORE_NAMES:
            reasons.append(f"Store '{store.name}' matches standard seed fixture")
        elif "test kirana" in s_name or "test store" in s_name or s_name.startswith("test_"):
            reasons.append(f"Store name '{store.name}' has explicit test moniker")

    # 3. Inspection markers
    notes = (insp.notes or "").lower()
    if "test" in notes or "demo" in notes or "e2e scenario" in notes or "seed fixture" in notes:
        reasons.append(f"Inspection notes contain explicit test moniker: '{insp.notes[:40]}...'")
    if getattr(insp, "mock_location", False) and ("test" in notes or "demo" in notes):
        reasons.append("Mock location flag enabled with test context")

    # 4. Scan markers
    for s in scans:
        b_name = (s.brand_name or "").lower()
        c_gen = (s.commodity_generic or "").lower()
        if b_name == "seed brand":
            reasons.append(f"Scan #{s.id} brand is 'Seed Brand'")
        if c_gen.startswith("test_") or c_gen.startswith("demo_"):
            reasons.append(f"Scan #{s.id} commodity generic has test prefix '{s.commodity_generic}'")
        if s.catalog_hash in ("c" * 64, "hash_atta", "hash_salt", "hash_surf", "hash_everest"):
            reasons.append(f"Scan #{s.id} has synthetic test catalog hash")

    if reasons:
        return True, "; ".join(reasons)
    return False, ""


def classify_scan(
    scan: Scan,
    findings: list[Finding],
    images: list[ScanImage],
    is_submitted: bool,
    audit_actions: list[str],
) -> ScanAuditRecord:
    """Classify a single scan and its evidence."""
    tags: list[str] = []
    reasons: list[str] = []

    # Counts
    f_total = len(findings)
    unconfirmed_fails = [f for f in findings if f.engine_verdict == "fail" and f.human_verdict is None]
    confirmed_fails = [f for f in findings if f.human_verdict == "fail"]
    # Only non-null confidence < 0.60 counts as low confidence (Module 6.1 contract)
    low_conf = [f for f in findings if f.confidence is not None and f.confidence < 0.60 and f.human_verdict is None]

    # Inconsistency checks
    has_inconsistency = False
    if scan.overall_result == "compliant" and (unconfirmed_fails or confirmed_fails):
        tags.append(CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD)
        reasons.append("Scan overall_result is 'compliant' but failing findings exist")
        has_inconsistency = True
    if scan.checks_assessed > scan.checks_total:
        tags.append(CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD)
        reasons.append(f"Checks assessed ({scan.checks_assessed}) exceeds total ({scan.checks_total})")
        has_inconsistency = True

    # Image presence checks
    img_count = len(images)
    missing_files: list[int] = []
    if img_count == 0:
        tags.append(CLASSIFICATION_RECAPTURE_CANDIDATE)
        reasons.append("Scan has zero uploaded images in evidence table")
    else:
        # Check file accessibility on filesystem
        for im in images:
            if im.file_path:
                p = Path(im.file_path)
                # If relative, check against cwd or evidence dir
                if not p.is_absolute():
                    p = Path.cwd() / im.file_path
                if not p.exists():
                    missing_files.append(im.id)
        if missing_files:
            tags.append(CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD)
            reasons.append(f"Scan references {len(missing_files)} image files missing from storage")

    # Resolution via audit log
    is_resolved = any(a in ("review_resolved", "review_dismissed") for a in audit_actions)
    if is_resolved:
        tags.append(CLASSIFICATION_ALREADY_RESOLVED)
        reasons.append("Scan or parent inspection marked resolved in audit log")

    # Technical OCR/provider error check
    is_tech_failure = False
    if scan.overall_result == "not_assessed":
        tech_findings = [
            f for f in findings
            if f.engine_verdict == "not_assessed"
            and f.reason
            and any(k in f.reason.lower() for k in ("ocr", "provider", "technical", "timeout", "unavailable", "failed to initialize", "credentials"))
        ]
        if tech_findings:
            tags.append(CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE)
            reasons.append(f"OCR/provider technical failure on {len(tech_findings)} checks")
            is_tech_failure = True
        elif findings:
            tags.append(CLASSIFICATION_INCOMPLETE_EVIDENCE)
            reasons.append(f"Incomplete evidence coverage ({scan.checks_assessed}/{scan.checks_total} checks assessed)")
        else:
            tags.append(CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION)
            reasons.append("Scan is unassessed with zero finding records; requires manual investigation")
    elif scan.overall_result == "compliant" and not findings:
        tags.append(CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION)
        reasons.append("Scan marked compliant with zero finding records; requires manual verification")

    # Violations
    if is_submitted and not is_resolved:
        if confirmed_fails:
            tags.append(CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION)
            reasons.append(f"{len(confirmed_fails)} confirmed statutory violations awaiting legal action")
        elif unconfirmed_fails or scan.overall_result == "violation":
            tags.append(CLASSIFICATION_PENDING_OFFICER_REVIEW)
            reasons.append(f"{len(unconfirmed_fails)} unconfirmed violations awaiting officer adjudication")
        elif low_conf:
            tags.append(CLASSIFICATION_PENDING_OFFICER_REVIEW)
            reasons.append(f"{len(low_conf)} low-confidence findings requiring human review")

    # Primary classification selection
    if CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD in tags:
        primary = CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD
    elif CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION in tags:
        primary = CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION
    elif CLASSIFICATION_PENDING_OFFICER_REVIEW in tags:
        primary = CLASSIFICATION_PENDING_OFFICER_REVIEW
    elif CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE in tags:
        primary = CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE
    elif CLASSIFICATION_RECAPTURE_CANDIDATE in tags:
        primary = CLASSIFICATION_RECAPTURE_CANDIDATE
    elif CLASSIFICATION_ALREADY_RESOLVED in tags:
        primary = CLASSIFICATION_ALREADY_RESOLVED
    elif CLASSIFICATION_INCOMPLETE_EVIDENCE in tags:
        primary = CLASSIFICATION_INCOMPLETE_EVIDENCE
    else:
        primary = CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION
        tags.append(CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION)
        reasons.append("Requires manual verification against legal standards")

    return ScanAuditRecord(
        scan_id=scan.id,
        inspection_id=scan.inspection_id,
        overall_result=scan.overall_result,
        checks_total=scan.checks_total,
        checks_assessed=scan.checks_assessed,
        primary_classification=primary,
        classifications=list(dict.fromkeys(tags)),
        reasons=reasons,
        image_count=img_count,
        missing_images=missing_files,
        findings_count=f_total,
        unconfirmed_violations=len(unconfirmed_fails),
        confirmed_violations=len(confirmed_fails),
        low_confidence_count=len(low_conf),
    )


def audit_inspection(
    insp: Inspection,
    store: Store | None,
    user: User | None,
    scans: list[Scan],
    findings_by_scan: dict[int, list[Finding]],
    images_by_scan: dict[int, list[ScanImage]],
    audit_actions_for_insp: list[str],
    audit_actions_by_scan: dict[int, list[str]],
    stale_days: int = DEFAULT_STALE_DAYS,
    as_of: datetime | None = None,
) -> InspectionAuditRecord:
    """Audit and classify an inspection according to the Module 6.2 rules."""
    now = as_of or datetime.now(timezone.utc)
    created_dt = insp.created_at
    if created_dt and created_dt.tzinfo is None:
        created_dt = created_dt.replace(tzinfo=timezone.utc)

    # Compute age in days
    if created_dt:
        age_days = max(0, (now - created_dt).days)
    elif insp.inspection_date:
        age_days = max(0, (now.date() - insp.inspection_date).days)
    else:
        age_days = 0

    age_band = _compute_age_band(age_days)
    tags: list[str] = []
    reasons: list[str] = []

    # Scan-level classification rollups
    scan_records: list[ScanAuditRecord] = []
    is_submitted = (insp.status == "submitted")
    for s in scans:
        s_findings = findings_by_scan.get(s.id, [])
        s_images = images_by_scan.get(s.id, [])
        combined_audit = audit_actions_for_insp + audit_actions_by_scan.get(s.id, [])
        s_rec = classify_scan(s, s_findings, s_images, is_submitted, combined_audit)
        scan_records.append(s_rec)

    # Rollup scan-level tags and reasons to parent inspection
    for s_rec in scan_records:
        if CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD in s_rec.classifications:
            tags.append(CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD)
            reasons.extend(s_rec.reasons)
        if CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION in s_rec.classifications:
            tags.append(CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION)
            reasons.extend(s_rec.reasons)

    # Check for test / seed provenance
    is_test, test_reason = _is_explicit_test_data(insp, store, user, scans)
    if is_test:
        tags.append(CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA)
        reasons.append(test_reason)

    # Resolution check via AuditLog
    is_resolved = any(a in ("review_resolved", "review_dismissed") for a in audit_actions_for_insp)
    if is_resolved:
        tags.append(CLASSIFICATION_ALREADY_RESOLVED)
        reasons.append("Inspection resolution recorded in AuditLog")

    # Inconsistent / Orphaned check
    for s in scans:
        if s.inspection_id != insp.id:
            tags.append(CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD)
            reasons.append(f"Scan #{s.id} inspection_id mismatch")

    # Lifecycle evaluation
    is_resumable = False
    has_unresolved_findings = False

    if insp.status == "draft":
        if len(scans) == 0:
            tags.append(CLASSIFICATION_DRAFT_WITHOUT_SCANS)
            reasons.append("Draft inspection created with no scans attached")
        else:
            if age_days <= stale_days:
                tags.append(CLASSIFICATION_ACTIVE_DRAFT)
                reasons.append(f"Recent draft ({age_days}d old <= {stale_days}d threshold); resumable by inspector")
                is_resumable = True
            else:
                tags.append(CLASSIFICATION_POTENTIALLY_STALE_DRAFT)
                reasons.append(
                    f"Draft inactive for {age_days} days (> {stale_days}d threshold). "
                    "Flagged for supervisor review before archival consideration."
                )

    elif insp.status == "submitted":
        if not is_resolved:
            # Check for violations and reviews
            for s_rec in scan_records:
                if CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION in s_rec.classifications:
                    tags.append(CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION)
                    has_unresolved_findings = True
                if CLASSIFICATION_PENDING_OFFICER_REVIEW in s_rec.classifications:
                    tags.append(CLASSIFICATION_PENDING_OFFICER_REVIEW)
                    has_unresolved_findings = True
                if CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE in s_rec.classifications:
                    tags.append(CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE)
                if CLASSIFICATION_RECAPTURE_CANDIDATE in s_rec.classifications:
                    tags.append(CLASSIFICATION_RECAPTURE_CANDIDATE)

    # If no tags matched or provenance is ambiguous
    if not tags:
        tags.append(CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION)
        reasons.append("Insufficient or ambiguous provenance; human legal review required")

    # Determine primary classification
    priority_order = [
        CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD,
        CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION,
        CLASSIFICATION_PENDING_OFFICER_REVIEW,
        CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE,
        CLASSIFICATION_RECAPTURE_CANDIDATE,
        CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA,
        CLASSIFICATION_ALREADY_RESOLVED,
        CLASSIFICATION_DRAFT_WITHOUT_SCANS,
        CLASSIFICATION_POTENTIALLY_STALE_DRAFT,
        CLASSIFICATION_ACTIVE_DRAFT,
        CLASSIFICATION_INCOMPLETE_EVIDENCE,
        CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION,
    ]
    primary = CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION
    for p in priority_order:
        if p in tags:
            primary = p
            break

    return InspectionAuditRecord(
        inspection_id=insp.id,
        store_id=insp.store_id,
        user_id=insp.user_id,
        status=insp.status,
        inspection_date_iso=insp.inspection_date.isoformat() if insp.inspection_date else "",
        created_at_iso=created_dt.isoformat() if created_dt else None,
        age_days=age_days,
        age_band=age_band,
        primary_classification=primary,
        classifications=list(dict.fromkeys(tags)),
        reasons=list(dict.fromkeys(reasons)),
        scans_count=len(scans),
        is_resumable=is_resumable,
        has_unresolved_findings=has_unresolved_findings,
        is_adjudicated_resolved=is_resolved,
        scans=scan_records,
    )


# ---------------------------------------------------------------------------
# Main Audit Executor
# ---------------------------------------------------------------------------
def run_data_audit(
    db: Session,
    stale_days: int = DEFAULT_STALE_DAYS,
    as_of: datetime | None = None,
    verify_files_on_disk: bool = True,
) -> AuditResultSet:
    """Execute a comprehensive, strictly read-only data audit.
    Never modifies, inserts, or deletes records.
    """
    now = as_of or datetime.now(timezone.utc)
    metadata = {
        "audit_timestamp": now.isoformat(),
        "stale_days_threshold": stale_days,
        "read_only": True,
        "database_dialect": db.bind.dialect.name if db.bind else "unknown",
    }

    # 1. Fetch Module 6.1 canonical queue metrics for baseline consistency
    try:
        canonical_queue = get_canonical_queue_metrics(db, status_filter="OPEN")
    except Exception as exc:
        canonical_queue = {"error": str(exc), "total": 0}

    # 2. Fetch database rows safely
    inspections = db.query(Inspection).order_by(Inspection.id.asc()).all()
    stores = {s.id: s for s in db.query(Store).all()}
    users = {u.id: u for u in db.query(User).all()}
    scans = db.query(Scan).order_by(Scan.id.asc()).all()
    findings = db.query(Finding).order_by(Finding.id.asc()).all()
    scan_images = db.query(ScanImage).order_by(ScanImage.id.asc()).all()
    audit_logs = db.query(AuditLog).order_by(AuditLog.seq.asc()).all()

    # Organize scans and findings by parent
    scans_by_insp: dict[int, list[Scan]] = {}
    for s in scans:
        scans_by_insp.setdefault(s.inspection_id, []).append(s)

    findings_by_scan: dict[int, list[Finding]] = {}
    for f in findings:
        findings_by_scan.setdefault(f.scan_id, []).append(f)

    images_by_scan: dict[int, list[ScanImage]] = {}
    missing_evidence_files: list[EvidenceFileCheck] = []
    for im in scan_images:
        images_by_scan.setdefault(im.scan_id, []).append(im)
        if verify_files_on_disk and im.file_path:
            p = Path(im.file_path)
            if not p.is_absolute():
                p = Path.cwd() / im.file_path
            f_exists = p.exists()
            if not f_exists:
                missing_evidence_files.append(
                    EvidenceFileCheck(
                        image_id=im.id,
                        scan_id=im.scan_id,
                        panel=im.panel,
                        sha256=im.sha256,
                        file_path=im.file_path,
                        file_exists=False,
                        is_accessible=False,
                        reason="Artifact file not found on evidence storage filesystem",
                    )
                )

    # Organize audit actions
    audit_by_insp: dict[int, list[str]] = {}
    audit_by_scan: dict[int, list[str]] = {}
    for a in audit_logs:
        if a.inspection_id:
            audit_by_insp.setdefault(a.inspection_id, []).append(a.action)
        if a.scan_id:
            audit_by_scan.setdefault(a.scan_id, []).append(a.action)

    # 3. Classify inspections
    inspection_records: list[InspectionAuditRecord] = []
    inspections_by_status = {"draft": 0, "submitted": 0, "other": 0}
    drafts_by_age_band = {
        "< 24 hours": 0,
        "1 - 7 days": 0,
        "8 - 30 days": 0,
        "31 - 90 days": 0,
        "> 90 days": 0,
    }
    classification_counts: dict[str, int] = {c: 0 for c in ALL_CLASSIFICATIONS}
    scans_classification_counts: dict[str, int] = {c: 0 for c in ALL_CLASSIFICATIONS}
    inconsistent_records: list[dict[str, Any]] = []

    # Check for orphaned scans (scans with invalid inspection_id)
    known_insp_ids = {i.id for i in inspections}
    for s in scans:
        if s.inspection_id not in known_insp_ids:
            inconsistent_records.append({
                "type": "orphaned_scan",
                "scan_id": s.id,
                "inspection_id": s.inspection_id,
                "reason": f"Scan #{s.id} references non-existent inspection #{s.inspection_id}",
            })

    # Check for orphaned findings (findings with invalid scan_id)
    known_scan_ids = {s.id for s in scans}
    for f in findings:
        if f.scan_id not in known_scan_ids:
            inconsistent_records.append({
                "type": "orphaned_finding",
                "finding_id": f.id,
                "scan_id": f.scan_id,
                "reason": f"Finding #{f.id} references non-existent scan #{f.scan_id}",
            })

    for insp in inspections:
        st = stores.get(insp.store_id)
        u = users.get(insp.user_id)
        i_scans = scans_by_insp.get(insp.id, [])
        i_actions = audit_by_insp.get(insp.id, [])

        rec = audit_inspection(
            insp=insp,
            store=st,
            user=u,
            scans=i_scans,
            findings_by_scan=findings_by_scan,
            images_by_scan=images_by_scan,
            audit_actions_for_insp=i_actions,
            audit_actions_by_scan=audit_by_scan,
            stale_days=stale_days,
            as_of=now,
        )
        inspection_records.append(rec)

        # Tally status
        if insp.status in inspections_by_status:
            inspections_by_status[insp.status] += 1
        else:
            inspections_by_status["other"] += 1

        # Tally draft age
        if insp.status == "draft":
            drafts_by_age_band[rec.age_band] = drafts_by_age_band.get(rec.age_band, 0) + 1

        # Tally inspection classifications (faceted)
        for c in rec.classifications:
            classification_counts[c] = classification_counts.get(c, 0) + 1

        # Tally scan classifications
        for s_rec in rec.scans:
            for sc in s_rec.classifications:
                scans_classification_counts[sc] = scans_classification_counts.get(sc, 0) + 1

        # Add to inconsistent list if flagged
        if CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD in rec.classifications:
            inconsistent_records.append({
                "type": "inconsistent_inspection",
                "inspection_id": rec.inspection_id,
                "reasons": rec.reasons,
            })

    # 4. Synthesize Maintainer Q&A answers
    resumable_draft_ids = [
        r.inspection_id for r in inspection_records
        if r.status == "draft" and r.is_resumable
    ]
    archival_review_ids = [
        r.inspection_id for r in inspection_records
        if CLASSIFICATION_POTENTIALLY_STALE_DRAFT in r.classifications
        or CLASSIFICATION_DRAFT_WITHOUT_SCANS in r.classifications
        or CLASSIFICATION_ALREADY_RESOLVED in r.classifications
        or CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA in r.classifications
    ]
    technical_retry_scan_ids = [
        s.id for s in scans
        if any(
            f.engine_verdict == "not_assessed"
            and f.reason
            and any(k in f.reason.lower() for k in ("ocr", "provider", "timeout", "unavailable", "credentials"))
            for f in findings_by_scan.get(s.id, [])
        )
    ]
    recapture_scan_ids = [
        s.id for s in scans
        if len(images_by_scan.get(s.id, [])) == 0
        or any("recapture" in (f.reason or "").lower() for f in findings_by_scan.get(s.id, []))
    ]
    pending_adjudication_scan_ids = [
        s.id for s in scans
        if any(f.engine_verdict == "fail" and f.human_verdict is None for f in findings_by_scan.get(s.id, []))
        and not any(a in ("review_resolved", "review_dismissed") for a in audit_by_insp.get(s.inspection_id, []) + audit_by_scan.get(s.id, []))
    ]
    manual_investigation_ids = [
        r.inspection_id for r in inspection_records
        if CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION in r.classifications
        or CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD in r.classifications
    ]

    maintenance_qa = {
        "1_archival_consideration": {
            "question": "Which records may be safely reviewed for archival consideration?",
            "count": len(archival_review_ids),
            "inspection_ids": archival_review_ids,
            "guidance": (
                "Includes inactive drafts older than threshold, zero-scan drafts, and confirmed resolved/seed data. "
                "Age alone does not warrant automatic deletion; human supervisor review is required."
            ),
        },
        "2_resumable_drafts": {
            "question": "Which drafts should be resumed by the inspector?",
            "count": len(resumable_draft_ids),
            "inspection_ids": resumable_draft_ids,
            "guidance": "Active draft visits (< threshold) with recorded commodities, resumable via mobile app.",
        },
        "3_technical_ocr_retry": {
            "question": "Which cases need a technical OCR retry?",
            "count": len(technical_retry_scan_ids),
            "scan_ids": technical_retry_scan_ids,
            "guidance": "Scans that failed solely due to OCR provider timeout or unavailability; safe to re-run automated pipeline.",
        },
        "4_recapture_required": {
            "question": "Which packages require recapture?",
            "count": len(recapture_scan_ids),
            "scan_ids": recapture_scan_ids,
            "guidance": "Packages with zero uploaded images or blur/glare defects requiring field recapture.",
        },
        "5_pending_officer_adjudication": {
            "question": "Which findings still need officer adjudication?",
            "count": len(pending_adjudication_scan_ids),
            "scan_ids": pending_adjudication_scan_ids,
            "guidance": "Unconfirmed statutory violations awaiting authorized officer verdict.",
        },
        "6_manual_investigation_required": {
            "question": "Which records require manual investigation before any cleanup?",
            "count": len(manual_investigation_ids),
            "inspection_ids": manual_investigation_ids,
            "guidance": "Inconsistent states or ambiguous historical provenance requiring manual case inspection.",
        },
    }

    return AuditResultSet(
        metadata=metadata,
        canonical_queue_metrics=canonical_queue,
        total_inspections=len(inspections),
        total_scans=len(scans),
        total_findings=len(findings),
        total_scan_images=len(scan_images),
        inspections_by_status=inspections_by_status,
        drafts_by_age_band=drafts_by_age_band,
        classification_counts=classification_counts,
        scans_classification_counts=scans_classification_counts,
        inspections=inspection_records,
        inconsistent_records=inconsistent_records,
        missing_evidence_files=missing_evidence_files,
        maintenance_qa=maintenance_qa,
    )


# ---------------------------------------------------------------------------
# Formatting and Reporting (JSON / CSV / Terminal)
# ---------------------------------------------------------------------------
def format_json_report(result: AuditResultSet, indent: int = 2) -> str:
    """Serialize the audit result set to a safe, machine-readable JSON string."""
    payload = {
        "metadata": result.metadata,
        "canonical_queue_metrics": result.canonical_queue_metrics,
        "summary": {
            "total_inspections": result.total_inspections,
            "total_scans": result.total_scans,
            "total_findings": result.total_findings,
            "total_scan_images": result.total_scan_images,
            "inspections_by_status": result.inspections_by_status,
            "drafts_by_age_band": result.drafts_by_age_band,
            "inspection_classifications": result.classification_counts,
            "scan_classifications": result.scans_classification_counts,
            "inconsistent_records_count": len(result.inconsistent_records),
            "missing_evidence_files_count": len(result.missing_evidence_files),
        },
        "maintenance_qa": result.maintenance_qa,
        "inconsistent_records": result.inconsistent_records,
        "missing_evidence_files": [asdict(f) for f in result.missing_evidence_files],
        "inspections": [
            {
                "inspection_id": i.inspection_id,
                "status": i.status,
                "age_days": i.age_days,
                "age_band": i.age_band,
                "primary_classification": i.primary_classification,
                "classifications": i.classifications,
                "reasons": i.reasons,
                "scans_count": i.scans_count,
                "is_resumable": i.is_resumable,
            }
            for i in result.inspections
        ],
    }
    return json.dumps(payload, indent=indent, ensure_ascii=False)


def format_csv_report(result: AuditResultSet) -> str:
    """Format inspection-level and scan-level classifications as CSV."""
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "Entity", "ID", "Parent_ID", "Status", "Age_Days",
        "Primary_Classification", "Faceted_Tags", "Reasons"
    ])
    for i in result.inspections:
        writer.writerow([
            "Inspection",
            i.inspection_id,
            "",
            i.status,
            i.age_days,
            i.primary_classification,
            "|".join(i.classifications),
            "; ".join(i.reasons),
        ])
        for s in i.scans:
            writer.writerow([
                "Scan",
                s.scan_id,
                s.inspection_id,
                s.overall_result,
                i.age_days,
                s.primary_classification,
                "|".join(s.classifications),
                "; ".join(s.reasons),
            ])
    return out.getvalue()


def format_text_report(result: AuditResultSet) -> str:
    """Render a human-readable advisory report with clean ASCII tables."""
    lines: list[str] = []
    lines.append("=" * 80)
    lines.append("NIYAMNETRA DATA AUDIT: STALE DRAFTS & HISTORICAL INSPECTION RECORDS")
    lines.append("=" * 80)
    lines.append(f"Execution Time : {result.metadata['audit_timestamp']}")
    lines.append(f"Stale Threshold: {result.metadata['stale_days_threshold']} days")
    lines.append(f"Operation Mode : READ-ONLY (Zero DB mutations, no records deleted or altered)")
    lines.append(f"Database Engine: {result.metadata['database_dialect']}")
    lines.append("-" * 80)

    lines.append("\n[1] DATABASE TOTALS & LIFECYCLE BREAKDOWN")
    lines.append(f"  Total Inspections : {result.total_inspections}")
    lines.append(f"    - Draft Visits  : {result.inspections_by_status.get('draft', 0)}")
    lines.append(f"    - Submitted     : {result.inspections_by_status.get('submitted', 0)}")
    lines.append(f"  Total Scans       : {result.total_scans}")
    lines.append(f"  Total Findings    : {result.total_findings}")
    lines.append(f"  Total Scan Images : {result.total_scan_images}")
    lines.append(f"  Open Review Queue : {result.canonical_queue_metrics.get('total', 0)} distinct actionable scans")

    lines.append("\n[2] DRAFT INSPECTIONS BY AGE BAND")
    for band, count in result.drafts_by_age_band.items():
        bar = "#" * min(30, count)
        lines.append(f"  {band:<15} : {count:>4} {bar}")

    lines.append("\n[3] INSPECTION CLASSIFICATIONS (Faceted Counts)")
    for c in ALL_CLASSIFICATIONS:
        cnt = result.classification_counts.get(c, 0)
        if cnt > 0:
            lines.append(f"  {c:<35} : {cnt:>4}")

    lines.append("\n[4] SCAN CLASSIFICATIONS (Faceted Counts)")
    for c in ALL_CLASSIFICATIONS:
        cnt = result.scans_classification_counts.get(c, 0)
        if cnt > 0:
            lines.append(f"  {c:<35} : {cnt:>4}")

    if result.inconsistent_records:
        lines.append(f"\n[!] INCONSISTENT / ORPHANED RECORDS DETECTED: {len(result.inconsistent_records)}")
        for rec in result.inconsistent_records[:10]:
            lines.append(f"  - [{rec.get('type')}] ID={rec.get('scan_id') or rec.get('inspection_id') or rec.get('finding_id')}: {rec.get('reason') or rec.get('reasons')}")
        if len(result.inconsistent_records) > 10:
            lines.append(f"  ... and {len(result.inconsistent_records) - 10} more.")

    if result.missing_evidence_files:
        lines.append(f"\n[!] MISSING EVIDENCE FILES ON DISK: {len(result.missing_evidence_files)}")
        for mf in result.missing_evidence_files[:5]:
            lines.append(f"  - Image #{mf.image_id} (Scan #{mf.scan_id}, panel={mf.panel}): {mf.reason}")

    lines.append("\n[5] ACTIONABLE MAINTENANCE GUIDANCE (Q&A)")
    for key, q_data in sorted(result.maintenance_qa.items()):
        lines.append(f"\n  Q: {q_data['question']}")
        lines.append(f"  Actionable Records Count: {q_data['count']}")
        lines.append(f"  Guidance: {q_data['guidance']}")

    lines.append("\n" + "=" * 80)
    lines.append("END OF ADVISORY AUDIT REPORT — No data was modified.")
    lines.append("=" * 80)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI Entrypoint
# ---------------------------------------------------------------------------
def main():
    parser = argparse.ArgumentParser(
        description="NiyamNetra Read-Only Audit of Stale Drafts & Historical Inspection Records"
    )
    parser.add_argument(
        "--db-url",
        type=str,
        default=None,
        help="Database URL (default reads from settings.DATABASE_URL). Strictly read-only.",
    )
    parser.add_argument(
        "--stale-days",
        type=int,
        default=DEFAULT_STALE_DAYS,
        help=f"Stale threshold in days for draft visits (default: {DEFAULT_STALE_DAYS})",
    )
    parser.add_argument(
        "--json-out",
        type=str,
        default=None,
        help="Path to export machine-readable JSON report",
    )
    parser.add_argument(
        "--csv-out",
        type=str,
        default=None,
        help="Path to export tabular CSV classifications",
    )
    parser.add_argument(
        "--skip-file-check",
        action="store_true",
        help="Skip filesystem existence checks for attached image files",
    )
    args = parser.parse_args()

    # Determine database URL safely
    db_url = args.db_url
    if not db_url:
        from config import settings
        db_url = settings.DATABASE_URL

    clean_target = sanitize_url(db_url)
    print(f"Connecting to database (Read-Only Mode): {clean_target}...")

    session = create_readonly_session(db_url)
    try:
        results = run_data_audit(
            db=session,
            stale_days=args.stale_days,
            verify_files_on_disk=not args.skip_file_check,
        )

        # Print human-readable report to stdout
        text_report = format_text_report(results)
        print(text_report)

        # Export JSON if requested
        if args.json_out:
            p_json = Path(args.json_out)
            p_json.parent.mkdir(parents=True, exist_ok=True)
            p_json.write_text(format_json_report(results), encoding="utf-8")
            print(f"JSON audit report successfully exported to: {p_json.resolve()}")

        # Export CSV if requested
        if args.csv_out:
            p_csv = Path(args.csv_out)
            p_csv.parent.mkdir(parents=True, exist_ok=True)
            p_csv.write_text(format_csv_report(results), encoding="utf-8")
            print(f"CSV classification table successfully exported to: {p_csv.resolve()}")

    finally:
        session.rollback()
        session.close()


if __name__ == "__main__":
    main()
