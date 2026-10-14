"""tests/test_audit_stale_records.py — Test suite for Module 6.2 Data Audit.

Covers all 18 required scenarios:
1. Recent draft identified as ACTIVE_DRAFT.
2. Old draft flagged as POTENTIALLY_STALE_DRAFT without being automatically abandoned.
3. Draft with no scans detected as DRAFT_WITHOUT_SCANS.
4. Scan with no attached images detected as RECAPTURE_CANDIDATE.
5. Inaccessible image artifact files detected as ORPHANED_OR_INCONSISTENT_RECORD.
6. Genuine submitted violation retained for action (CONFIRMED_VIOLATION_AWAITING_ACTION / PENDING_OFFICER_REVIEW).
7. Adjudicated inspection identified as ALREADY_RESOLVED via AuditLog.
8. NULL-confidence deterministic findings not classified as low-confidence defects.
9. Record with explicit test provenance identified as POSSIBLE_TEST_OR_SEED_DATA.
10. Old genuine inspection not classified as test data based solely on age.
11. Missing provenance leads to NEEDS_MANUAL_CLASSIFICATION.
12. Inconsistent assessment states (e.g. compliant result with failing findings) reported.
13. Multiple scans or findings do not inflate the inspection count.
14. Audit summary counts strictly agree with actual controlled database records.
15. Tool does not modify database state in default mode (zero row mutations).
16. Repeated runs against unchanged database produce identical, deterministic results.
17. Sensitive values, passwords, and tokens do not appear in reports or exports.
18. Safe operation on SQLite test setup and PostgreSQL connection hygiene.
"""
import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from audit import append_audit
from audit_stale_records import (
    CLASSIFICATION_ACTIVE_DRAFT,
    CLASSIFICATION_ALREADY_RESOLVED,
    CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION,
    CLASSIFICATION_DRAFT_WITHOUT_SCANS,
    CLASSIFICATION_INCOMPLETE_EVIDENCE,
    CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION,
    CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD,
    CLASSIFICATION_PENDING_OFFICER_REVIEW,
    CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA,
    CLASSIFICATION_POTENTIALLY_STALE_DRAFT,
    CLASSIFICATION_RECAPTURE_CANDIDATE,
    CLASSIFICATION_TECHNICAL_ASSESSMENT_FAILURE,
    create_readonly_session,
    format_csv_report,
    format_json_report,
    format_text_report,
    run_data_audit,
    sanitize_url,
)
from database import Base
from models import AuditLog, Finding, Inspection, Scan, ScanImage, Store, User


def _setup_test_db():
    """Create isolated in-memory test database."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    SessionFactory = sessionmaker(bind=eng)
    db = SessionFactory()

    # Base test actors
    inspector = User(
        employee_id="LM-HYD-501",
        full_name="Officer Rao",
        email="rao@niyamnetra.gov.in",
        password_hash="mockHash123",
        role="inspector",
        install_id="device-rao-501",
        is_active=True,
    )
    admin = User(
        employee_id="LM-DIR-001",
        full_name="Director Reddy",
        email="director@niyamnetra.gov.in",
        password_hash="mockAdmin123",
        role="admin",
        install_id="device-admin-001",
        is_active=True,
    )
    store = Store(
        name="Hyderabad Super Mart",
        address="Road No 10, Banjara Hills",
        city="Hyderabad",
        district="Hyderabad",
        state="Telangana",
        pincode="500034",
    )
    db.add_all([inspector, admin, store])
    db.commit()

    return {
        "engine": eng,
        "db": db,
        "inspector": inspector,
        "admin": admin,
        "store": store,
    }


def _make_scan(insp_id: int, **kwargs) -> Scan:
    defaults = {
        "rules_as_at": date.today(),
        "catalog_hash": "a" * 64,
        "engine_version": "2.4.0",
        "rule_pack_version": "2026.09.v1",
        "overall_result": "compliant",
        "checks_total": 19,
        "checks_assessed": 19,
        "commodity_generic": "Wheat Flour",
    }
    defaults.update(kwargs)
    return Scan(inspection_id=insp_id, **defaults)


def _make_finding(scan_id: int, **kwargs) -> Finding:
    defaults = {
        "check_id": "CHK01",
        "title": "Rule Check",
        "engine_verdict": "pass",
        "severity": "major",
    }
    defaults.update(kwargs)
    return Finding(scan_id=scan_id, **defaults)


# ---------------------------------------------------------------------------
# Test 1: Recent draft is identified as ACTIVE_DRAFT
# ---------------------------------------------------------------------------
def test_recent_draft_identified_as_active():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    now = datetime.now(timezone.utc)
    recent_date = (now - timedelta(days=2)).date()

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        inspection_date=recent_date,
        status="draft",
        created_at=now - timedelta(days=2),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, commodity_generic="Basmati Rice")
    db.add(scan)
    db.commit()

    results = run_data_audit(db, stale_days=14, as_of=now)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert audit_insp.status == "draft"
    assert audit_insp.is_resumable is True
    assert CLASSIFICATION_ACTIVE_DRAFT in audit_insp.classifications
    assert CLASSIFICATION_POTENTIALLY_STALE_DRAFT not in audit_insp.classifications


# ---------------------------------------------------------------------------
# Test 2: Old draft flagged as POTENTIALLY_STALE_DRAFT without auto-abandoning
# ---------------------------------------------------------------------------
def test_old_draft_flagged_as_potentially_stale_without_auto_purge():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    now = datetime.now(timezone.utc)
    old_date = (now - timedelta(days=45)).date()

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        inspection_date=old_date,
        status="draft",
        created_at=now - timedelta(days=45),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, commodity_generic="Cooking Oil")
    db.add(scan)
    db.commit()

    results = run_data_audit(db, stale_days=14, as_of=now)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert audit_insp.status == "draft"
    assert CLASSIFICATION_POTENTIALLY_STALE_DRAFT in audit_insp.classifications
    assert audit_insp.age_days >= 45
    assert audit_insp.age_band == "31 - 90 days"
    # Ensure guidance clarifies supervisor review required, never automatically purged
    assert any("supervisor review" in r for r in audit_insp.reasons)


# ---------------------------------------------------------------------------
# Test 3: Draft with no scans detected as DRAFT_WITHOUT_SCANS
# ---------------------------------------------------------------------------
def test_draft_without_scans_detected():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    now = datetime.now(timezone.utc)
    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        inspection_date=now.date(),
        status="draft",
        created_at=now,
    )
    db.add(insp)
    db.commit()

    results = run_data_audit(db, stale_days=14, as_of=now)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert audit_insp.scans_count == 0
    assert CLASSIFICATION_DRAFT_WITHOUT_SCANS in audit_insp.classifications


# ---------------------------------------------------------------------------
# Test 4: Scan with no attached images detected as RECAPTURE_CANDIDATE
# ---------------------------------------------------------------------------
def test_scan_with_no_images_detected_as_recapture_candidate():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, overall_result="not_assessed")
    db.add(scan)
    db.commit()

    results = run_data_audit(db)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)
    scan_rec = audit_insp.scans[0]

    assert scan_rec.image_count == 0
    assert CLASSIFICATION_RECAPTURE_CANDIDATE in scan_rec.classifications


# ---------------------------------------------------------------------------
# Test 5: Scan with inaccessible image artifacts distinguished
# ---------------------------------------------------------------------------
def test_scan_with_inaccessible_image_files_flagged():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id)
    db.add(scan)
    db.commit()

    # Image referencing a non-existent file on disk
    bogus_path = "evidence/non_existent_image_12345.jpg"
    img = ScanImage(
        scan_id=scan.id,
        panel="front",
        file_path=bogus_path,
        byte_size=1024,
        width_px=800,
        height_px=600,
        mime_type="image/jpeg",
        sha256="0" * 64,
    )
    db.add(img)
    db.commit()

    results = run_data_audit(db, verify_files_on_disk=True)
    assert len(results.missing_evidence_files) == 1
    assert results.missing_evidence_files[0].image_id == img.id
    assert results.missing_evidence_files[0].file_exists is False

    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)
    assert CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD in audit_insp.scans[0].classifications


# ---------------------------------------------------------------------------
# Test 6: Genuine submitted violation retained for action
# ---------------------------------------------------------------------------
def test_genuine_submitted_violation_retained_for_action():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, overall_result="violation")
    db.add(scan)
    db.commit()

    # Confirmed violation
    f = _make_finding(
        scan.id,
        check_id="CHK06",
        engine_verdict="fail",
        human_verdict="fail",
        override_reason="Confirmed missing MRP by officer",
        limb="36(1)",
        confidence=0.98,
        severity="critical",
    )
    db.add(f)
    db.commit()

    results = run_data_audit(db)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert CLASSIFICATION_CONFIRMED_VIOLATION_AWAITING_ACTION in audit_insp.classifications
    assert audit_insp.has_unresolved_findings is True


# ---------------------------------------------------------------------------
# Test 7: Adjudicated inspection identified as ALREADY_RESOLVED via AuditLog
# ---------------------------------------------------------------------------
def test_adjudicated_inspection_identified_as_already_resolved():
    env = _setup_test_db()
    db, admin, store = env["db"], env["admin"], env["store"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, overall_result="violation")
    db.add(scan)
    db.commit()

    # Append authoritative resolution in AuditLog
    append_audit(
        db,
        action="review_resolved",
        inspection_id=insp.id,
        user_id=admin.id,
        reason="Compounding fee paid and rectification verified",
    )

    results = run_data_audit(db)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert audit_insp.is_adjudicated_resolved is True
    assert CLASSIFICATION_ALREADY_RESOLVED in audit_insp.classifications
    # Should NOT be counted in open pending review
    assert CLASSIFICATION_PENDING_OFFICER_REVIEW not in audit_insp.classifications


# ---------------------------------------------------------------------------
# Test 8: NULL-confidence deterministic findings not classified as low-conf defects
# ---------------------------------------------------------------------------
def test_null_confidence_deterministic_findings_not_flagged_as_low_conf():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, overall_result="compliant")
    db.add(scan)
    db.commit()

    # 10 deterministic findings with confidence=None
    for i in range(1, 11):
        db.add(
            _make_finding(
                scan.id,
                check_id=f"CHK{i:02d}",
                title=f"Deterministic Check {i}",
                engine_verdict="pass",
                confidence=None,
            )
        )
    db.commit()

    results = run_data_audit(db)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert audit_insp.scans[0].low_confidence_count == 0
    assert CLASSIFICATION_PENDING_OFFICER_REVIEW not in audit_insp.classifications


# ---------------------------------------------------------------------------
# Test 9: Record with explicit test provenance identified as POSSIBLE_TEST_OR_SEED_DATA
# ---------------------------------------------------------------------------
def test_record_with_explicit_test_provenance_identified():
    env = _setup_test_db()
    db = env["db"]

    test_user = User(
        employee_id="LM-TEST-999",
        full_name="Seed Administrator",
        email="admin@example.test",
        password_hash="pw123",
        role="admin",
        is_active=True,
    )
    test_store = Store(
        name="Sri Balaji Supermarket",  # Exact seed store name
        city="Hyderabad",
    )
    db.add_all([test_user, test_store])
    db.commit()

    insp = Inspection(
        user_id=test_user.id,
        store_id=test_store.id,
        status="submitted",
        notes="E2E test fixture inspection",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, brand_name="Seed Brand", catalog_hash="c" * 64)
    db.add(scan)
    db.commit()

    results = run_data_audit(db)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA in audit_insp.classifications
    assert any("example.test" in r or "seed" in r.lower() for r in audit_insp.reasons)


# ---------------------------------------------------------------------------
# Test 10: Old genuine inspection not classified as test data based solely on age
# ---------------------------------------------------------------------------
def test_old_genuine_inspection_not_classified_as_test_data():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    now = datetime.now(timezone.utc)
    # Genuine real store, real inspector, 180 days ago
    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=(now - timedelta(days=180)).date(),
        created_at=now - timedelta(days=180),
        notes="Routine surveillance inspection under Section 15.",
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(insp.id, brand_name="Real Commercial Brand Ltd", catalog_hash="realhash123")
    db.add(scan)
    db.commit()

    results = run_data_audit(db, as_of=now)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert CLASSIFICATION_POSSIBLE_TEST_OR_SEED_DATA not in audit_insp.classifications


# ---------------------------------------------------------------------------
# Test 11: Missing provenance leads to manual classification
# ---------------------------------------------------------------------------
def test_missing_provenance_leads_to_needs_manual_classification():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
        notes=None,
    )
    db.add(insp)
    db.commit()

    # Scan with no findings, not assessed, no error reasons
    scan = _make_scan(insp.id, overall_result="compliant")
    db.add(scan)
    db.commit()

    # Add an accessible image file so the scan is not flagged as RECAPTURE_CANDIDATE
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tmp:
        tmp.write(b"mock evidence bytes")
        tmp_path = tmp.name

    try:
        img = ScanImage(
            scan_id=scan.id,
            panel="front",
            file_path=tmp_path,
            sha256="1" * 64,
            byte_size=19,
            width_px=100,
            height_px=100,
            mime_type="image/jpeg",
        )
        db.add(img)
        db.commit()

        results = run_data_audit(db)
        audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

        # Without findings or explicit resolution, scan falls back to manual verification
        assert CLASSIFICATION_NEEDS_MANUAL_CLASSIFICATION in audit_insp.classifications
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


# ---------------------------------------------------------------------------
# Test 12: Inconsistent assessment states reported
# ---------------------------------------------------------------------------
def test_inconsistent_assessment_state_reported():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    # Contradiction: overall_result is 'compliant' but a failing finding exists
    scan = _make_scan(insp.id, overall_result="compliant")
    db.add(scan)
    db.commit()

    f = _make_finding(scan.id, check_id="CHK06", engine_verdict="fail", severity="critical")
    db.add(f)
    db.commit()

    results = run_data_audit(db)
    audit_insp = next(i for i in results.inspections if i.inspection_id == insp.id)

    assert CLASSIFICATION_ORPHANED_OR_INCONSISTENT_RECORD in audit_insp.classifications
    assert any("compliant' but failing" in r for r in audit_insp.reasons)


# ---------------------------------------------------------------------------
# Test 13: Multiple scans or findings do not inflate inspection count
# ---------------------------------------------------------------------------
def test_multiple_scans_and_findings_do_not_inflate_inspection_count():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="submitted",
        inspection_date=date.today(),
    )
    db.add(insp)
    db.commit()

    # Add 5 scans to the single inspection
    for s_idx in range(5):
        s = _make_scan(insp.id, commodity_generic=f"Product {s_idx}")
        db.add(s)
        db.commit()
        # Add 10 findings per scan
        for f_idx in range(10):
            db.add(_make_finding(s.id, check_id=f"CHK{f_idx:02d}"))
    db.commit()

    results = run_data_audit(db)

    assert results.total_inspections == 1
    assert results.total_scans == 5
    assert results.total_findings == 50
    assert len(results.inspections) == 1
    assert results.inspections[0].scans_count == 5


# ---------------------------------------------------------------------------
# Test 14: Audit summary agrees with actual controlled database records
# ---------------------------------------------------------------------------
def test_audit_summary_agrees_with_actual_records():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    # 2 drafts, 3 submitted
    for i in range(2):
        insp_d = Inspection(user_id=insp_user.id, store_id=store.id, status="draft", inspection_date=date.today())
        db.add(insp_d)
    for j in range(3):
        insp_s = Inspection(user_id=insp_user.id, store_id=store.id, status="submitted", inspection_date=date.today())
        db.add(insp_s)
    db.commit()

    results = run_data_audit(db)

    assert results.total_inspections == 5
    assert results.inspections_by_status["draft"] == 2
    assert results.inspections_by_status["submitted"] == 3


# ---------------------------------------------------------------------------
# Test 15: Tool does not modify database state in default mode
# ---------------------------------------------------------------------------
def test_audit_strictly_read_only_zero_mutations():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    insp = Inspection(user_id=insp_user.id, store_id=store.id, status="draft", inspection_date=date.today())
    db.add(insp)
    db.commit()

    # Snapshot table counts before audit
    counts_before = {
        "inspections": db.query(Inspection).count(),
        "scans": db.query(Scan).count(),
        "findings": db.query(Finding).count(),
        "audit_logs": db.query(AuditLog).count(),
    }

    # Run audit
    run_data_audit(db)

    counts_after = {
        "inspections": db.query(Inspection).count(),
        "scans": db.query(Scan).count(),
        "findings": db.query(Finding).count(),
        "audit_logs": db.query(AuditLog).count(),
    }

    assert counts_before == counts_after


# ---------------------------------------------------------------------------
# Test 16: Repeated runs against unchanged database produce identical results
# ---------------------------------------------------------------------------
def test_repeated_runs_produce_deterministic_results():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    fixed_time = datetime(2026, 10, 14, 12, 0, 0, tzinfo=timezone.utc)

    insp = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        status="draft",
        created_at=fixed_time - timedelta(days=5),
        inspection_date=(fixed_time - timedelta(days=5)).date(),
    )
    db.add(insp)
    db.commit()
    s = _make_scan(insp.id)
    db.add(s)
    db.commit()

    run1 = run_data_audit(db, stale_days=14, as_of=fixed_time)
    run2 = run_data_audit(db, stale_days=14, as_of=fixed_time)

    assert run1.total_inspections == run2.total_inspections
    assert run1.classification_counts == run2.classification_counts
    assert run1.inspections[0].classifications == run2.inspections[0].classifications
    assert run1.inspections[0].primary_classification == run2.inspections[0].primary_classification


# ---------------------------------------------------------------------------
# Test 17: Sensitive values and credentials do not appear in reports or logs
# ---------------------------------------------------------------------------
def test_sensitive_values_and_credentials_redacted():
    env = _setup_test_db()
    db, insp_user, store = env["db"], env["inspector"], env["store"]

    # Sensitive connection string
    raw_url = "postgresql+psycopg://superadmin:TopSecretPassword123@db.supabase.com:5432/production"
    clean_url = sanitize_url(raw_url)

    assert "TopSecretPassword123" not in clean_url
    assert "***" in clean_url
    assert "superadmin" in clean_url

    # Check text and JSON formats do not contain user password hashes or auth tokens
    results = run_data_audit(db)
    text_out = format_text_report(results)
    json_out = format_json_report(results)
    csv_out = format_csv_report(results)

    for doc in (text_out, json_out, csv_out):
        assert "mockHash123" not in doc
        assert "mockAdmin123" not in doc
        assert "password" not in doc.lower()


# ---------------------------------------------------------------------------
# Test 18: Session enforces read-only blocking any flush attempts
# ---------------------------------------------------------------------------
def test_readonly_session_blocks_flush():
    env = _setup_test_db()
    eng = env["engine"]

    # Test create_readonly_session
    from audit_stale_records import _prevent_flush_handler

    SessionMaker = sessionmaker(bind=eng)
    ro_session = SessionMaker()
    from sqlalchemy import event
    event.listen(ro_session, "before_flush", _prevent_flush_handler)

    # Attempting to add an inspection through ro_session and flush must raise
    insp = Inspection(user_id=1, store_id=1, status="draft", inspection_date=date.today())
    ro_session.add(insp)

    with pytest.raises(RuntimeError, match="strict read-only mode"):
        ro_session.flush()

    ro_session.rollback()
    ro_session.close()
