"""tests/test_review_queue_categories.py — Module 6.1 Canonical Review Queue Categories and Counts.

Verifies the canonical review queue contract and database-authoritative counting:
1. Genuine confirmed violation awaiting officer enforcement.
2. Scan requiring human review because of an unconfirmed finding.
3. Technical OCR / provider failure recovery.
4. Scan requiring recapture (zero images or missing panel guidance).
5. Draft inspection awaiting inspector continuation (excluded from officer review queue).
6. Finalized (submitted) inspection with unresolved findings included in queue.
7. Closed / resolved records excluded from OPEN status.
8. Single scan with multiple findings creates exactly ONE queue item.
9. Duplicate finding records and joins do NOT inflate queue totals.
10. Historical passing records with NULL confidence do NOT trigger low_confidence review.
11. Absolute consistency between queue summary counts and actual query results.
12. RBAC isolation (Inspector sees own assigned items, Admin sees all).
"""
import os
from datetime import date, datetime, timezone

os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audit import append_audit
from database import Base, get_db
from jwt_handler import create_access_token
from main import app
from models import AuditLog, Finding, Inspection, Scan, ScanImage, Store, User
from password_handler import hash_password
from queries import get_canonical_queue_metrics, review_queue_size
from rbac import get_current_user, require_admin, require_inspector


def _setup_queue_test_env():
    """Create a fresh in-memory database with test client and isolated roles."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    db = Session()

    # Create users
    inspector1 = User(
        employee_id="LM-INSP-101",
        full_name="Officer Sharma",
        password_hash=hash_password("inspPass123!"),
        role="inspector",
        install_id="device-insp-101",
        is_active=True,
    )
    inspector2 = User(
        employee_id="LM-INSP-102",
        full_name="Officer Verma",
        password_hash=hash_password("inspPass123!"),
        role="inspector",
        install_id="device-insp-102",
        is_active=True,
    )
    admin = User(
        employee_id="LM-ADM-999",
        full_name="Director Rao",
        password_hash=hash_password("adminPass123!"),
        role="admin",
        install_id="device-admin-999",
        is_active=True,
    )
    store1 = Store(
        name="Fresh Mart",
        address="Banjara Hills Rd 1",
        city="Hyderabad",
        district="Hyderabad",
        state="Telangana",
        pincode="500034",
    )
    store2 = Store(
        name="Super Bazaar",
        address="MG Road",
        city="Vijayawada",
        district="Krishna",
        state="Andhra Pradesh",
        pincode="520001",
    )
    db.add_all([inspector1, inspector2, admin, store1, store2])
    db.commit()

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides.pop(require_admin, None)
    app.dependency_overrides.pop(require_inspector, None)
    app.dependency_overrides.pop(get_current_user, None)

    client = TestClient(app)
    return {
        "db": db,
        "client": client,
        "insp1": inspector1,
        "insp2": inspector2,
        "admin": admin,
        "store1": store1,
        "store2": store2,
    }


def _auth(user: User) -> dict:
    token = create_access_token(user.id, user.role, user.install_id)
    return {"Authorization": f"Bearer {token}"}


def _make_scan(inspection_id: int, **kwargs) -> Scan:
    defaults = {
        "rules_as_at": date.today(),
        "catalog_hash": "c" * 64,
        "engine_version": "2.4.0",
        "rule_pack_version": "2026.09.v1",
        "overall_result": "violation",
        "checks_total": 5,
        "checks_assessed": 5,
        "commodity_generic": "Sample Item",
    }
    defaults.update(kwargs)
    return Scan(inspection_id=inspection_id, **defaults)


def _make_finding(scan_id: int, **kwargs) -> Finding:
    defaults = {
        "check_id": "CHK01",
        "title": "Rule Check",
        "engine_verdict": "fail",
        "severity": "major",
    }
    defaults.update(kwargs)
    return Finding(scan_id=scan_id, **defaults)


# ---------------------------------------------------------------------------
# Test 1: Genuine confirmed violation awaiting enforcement action
# ---------------------------------------------------------------------------
def test_confirmed_violation_awaiting_enforcement():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(
        insp.id,
        commodity_generic="Mustard Oil",
        brand_name="Dhara",
        overall_result="violation",
    )
    db.add(scan)
    db.commit()

    # Confirmed violation: human_verdict is fail with override_reason
    f = _make_finding(
        scan.id,
        check_id="CHK06",
        title="MRP Declaration missing",
        engine_verdict="fail",
        human_verdict="fail",
        override_reason="Officer inspected package and confirmed mandatory MRP declaration is missing",
        limb="36(1)",
        confidence=0.95,
        severity="critical",
    )
    db.add(f)
    db.commit()

    headers = _auth(admin)
    summary_resp = client.get("/review/queue/summary", headers=headers)
    assert summary_resp.status_code == 200
    summary = summary_resp.json()
    assert summary["total"] == 1
    assert summary["violations_pending"] == 1
    assert summary["breakdown"]["confirmed_violations"] == 1
    assert summary["breakdown"]["unconfirmed_violations"] == 0

    queue_resp = client.get("/review/queue", headers=headers)
    assert queue_resp.status_code == 200
    queue_data = queue_resp.json()
    assert queue_data["total"] == 1
    assert len(queue_data["items"]) == 1
    assert queue_data["items"][0]["scan_id"] == scan.id
    assert queue_data["items"][0]["review_status"] == "OPEN"
    reasons = queue_data["items"][0]["reasons"]
    assert any(r.get("subtype") == "confirmed_violation" for r in reasons)


# ---------------------------------------------------------------------------
# Test 2: Unconfirmed violation awaiting officer review
# ---------------------------------------------------------------------------
def test_unconfirmed_violation_awaiting_review():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(
        insp.id,
        commodity_generic="Basmati Rice",
        overall_result="violation",
    )
    db.add(scan)
    db.commit()

    # Unconfirmed violation: engine_verdict is fail, human_verdict is None
    f = _make_finding(
        scan.id,
        check_id="CHK05",
        title="Net Quantity Deficit",
        engine_verdict="fail",
        human_verdict=None,
        confidence=0.88,
        severity="critical",
    )
    db.add(f)
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    assert summary["total"] == 1
    assert summary["violations_pending"] == 1
    assert summary["breakdown"]["unconfirmed_violations"] == 1
    assert summary["breakdown"]["confirmed_violations"] == 0

    queue_data = client.get("/review/queue", headers=headers).json()
    assert queue_data["total"] == 1
    reasons = queue_data["items"][0]["reasons"]
    assert any(r.get("subtype") == "unconfirmed_violation" for r in reasons)


# ---------------------------------------------------------------------------
# Test 3: Technical OCR / provider failure recovery
# ---------------------------------------------------------------------------
def test_technical_ocr_provider_recovery():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(
        insp.id,
        commodity_generic="Biscuits",
        overall_result="not_assessed",
        checks_assessed=0,
    )
    db.add(scan)
    db.commit()

    f = _make_finding(
        scan.id,
        check_id="CHK01",
        title="Label Verification",
        engine_verdict="not_assessed",
        reason="OCR provider timeout: external service unavailable",
        severity="major",
    )
    db.add(f)
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    assert summary["total"] == 1
    assert summary["not_assessed"] == 1
    assert summary["breakdown"]["technical_recovery"] == 1

    queue_data = client.get("/review/queue?reason=not_assessed", headers=headers).json()
    assert queue_data["total"] == 1
    reasons = queue_data["items"][0]["reasons"]
    assert any(r.get("subtype") == "technical_recovery" for r in reasons)


# ---------------------------------------------------------------------------
# Test 4: Scan requiring recapture (zero images or missing panel guidance)
# ---------------------------------------------------------------------------
def test_scan_requiring_recapture():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    # Scan with not_assessed and 0 images attached
    scan = _make_scan(
        insp.id,
        commodity_generic="Detergent Powder",
        overall_result="not_assessed",
        checks_assessed=0,
    )
    db.add(scan)
    db.commit()

    f = _make_finding(
        scan.id,
        check_id="CHK04",
        title="Manufacturer Details",
        engine_verdict="not_assessed",
        reason="Incomplete coverage: recapture required for back panel",
        severity="major",
    )
    db.add(f)
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    assert summary["total"] == 1
    assert summary["not_assessed"] == 1
    assert summary["breakdown"]["recapture_required"] == 1


# ---------------------------------------------------------------------------
# Test 5: Draft inspection awaiting continuation (EXCLUDED from officer review queue)
# ---------------------------------------------------------------------------
def test_draft_inspection_excluded_from_officer_review():
    env = _setup_queue_test_env()
    db, client, admin, insp_user, store = (
        env["db"],
        env["client"],
        env["admin"],
        env["insp1"],
        env["store1"],
    )

    # Draft inspection
    insp_draft = Inspection(
        user_id=insp_user.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="draft",
        in_scope=True,
    )
    db.add(insp_draft)
    db.commit()

    # Even if scan has a violation, draft status must NOT enter officer review queue
    scan_draft = _make_scan(
        insp_draft.id,
        commodity_generic="Sugar",
        overall_result="violation",
    )
    db.add(scan_draft)
    db.commit()

    f = _make_finding(
        scan_draft.id,
        check_id="CHK06",
        title="MRP Missing",
        engine_verdict="fail",
        confidence=0.9,
    )
    db.add(f)
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    # Total in review queue MUST be 0
    assert summary["total"] == 0
    assert summary["violations_pending"] == 0
    # Draft is tracked separately under breakdown
    assert summary["breakdown"]["draft_inspections"] == 1

    queue_data = client.get("/review/queue", headers=headers).json()
    assert queue_data["total"] == 0
    assert len(queue_data["items"]) == 0


# ---------------------------------------------------------------------------
# Test 6: Finalized (submitted) inspection with unresolved findings included
# ---------------------------------------------------------------------------
def test_submitted_inspection_included_in_queue():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(
        insp.id,
        commodity_generic="Snacks",
        overall_result="violation",
    )
    db.add(scan)
    db.commit()

    f = _make_finding(
        scan.id,
        check_id="CHK12",
        title="Country of Origin missing",
        engine_verdict="fail",
        confidence=0.85,
    )
    db.add(f)
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    assert summary["total"] == 1
    assert summary["violations_pending"] == 1

    queue_data = client.get("/review/queue", headers=headers).json()
    assert queue_data["total"] == 1
    assert queue_data["items"][0]["scan_id"] == scan.id


# ---------------------------------------------------------------------------
# Test 7: Closed / resolved records excluded from OPEN status
# ---------------------------------------------------------------------------
def test_resolved_inspection_transitions_out_of_open_queue():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(
        insp.id,
        commodity_generic="Tea Powder",
        overall_result="violation",
    )
    db.add(scan)
    db.commit()

    f = _make_finding(
        scan.id,
        check_id="CHK06",
        title="MRP Declaration missing",
        engine_verdict="fail",
        confidence=0.9,
    )
    db.add(f)
    db.commit()

    headers = _auth(admin)

    # Initial state: 1 open
    summary_before = client.get("/review/queue/summary", headers=headers).json()
    assert summary_before["total"] == 1

    # Officer adjudicates and resolves the inspection
    adjudicate_resp = client.post(
        f"/review/inspections/{insp.id}/adjudicate",
        json={
            "action": "resolve",
            "reason": "Officer verified manufacturer rectifying batch label under section 39",
            "resolution_notes": "Notice issued; compounding fee collected",
        },
        headers=headers,
    )
    assert adjudicate_resp.status_code == 200

    # Summary with default status_filter (or status_filter=OPEN) must now be 0
    summary_after = client.get("/review/queue/summary?status_filter=OPEN", headers=headers).json()
    assert summary_after["total"] == 0
    assert summary_after["breakdown"]["adjudicated_resolved"] == 1

    # Queue with status_filter=OPEN must be empty
    queue_open = client.get("/review/queue?status_filter=OPEN", headers=headers).json()
    assert queue_open["total"] == 0
    assert len(queue_open["items"]) == 0

    # Queue with status_filter=RESOLVED must show the resolved item
    queue_resolved = client.get("/review/queue?status_filter=RESOLVED", headers=headers).json()
    assert queue_resolved["total"] == 1
    assert queue_resolved["items"][0]["review_status"] == "RESOLVED"


# ---------------------------------------------------------------------------
# Test 8: Single scan with multiple findings creates exactly ONE queue item
# ---------------------------------------------------------------------------
def test_single_scan_with_multiple_findings_creates_one_queue_item():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(
        insp.id,
        commodity_generic="Assorted Spices",
        overall_result="violation",
        checks_total=8,
        checks_assessed=8,
    )
    db.add(scan)
    db.commit()

    # Add 5 failing findings on the SAME scan
    for i in range(1, 6):
        f = _make_finding(
            scan.id,
            check_id=f"CHK0{i}",
            title=f"Rule Check {i} Violation",
            engine_verdict="fail",
            confidence=0.85,
        )
        db.add(f)
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    # Distinct scan count must be 1, NOT 5
    assert summary["total"] == 1
    assert summary["violations_pending"] == 1

    queue_data = client.get("/review/queue", headers=headers).json()
    assert queue_data["total"] == 1
    assert len(queue_data["items"]) == 1
    assert queue_data["items"][0]["scan_id"] == scan.id


# ---------------------------------------------------------------------------
# Test 9: Duplicate finding records and joins do NOT inflate queue totals
# ---------------------------------------------------------------------------
def test_duplicate_finding_records_do_not_inflate_totals():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan1 = _make_scan(
        insp.id,
        commodity_generic="Milk Carton",
        overall_result="violation",
    )
    scan2 = _make_scan(
        insp.id,
        commodity_generic="Bread Loaf",
        overall_result="not_assessed",
        checks_assessed=2,
    )
    db.add_all([scan1, scan2])
    db.commit()

    # Populate 15 findings on scan1 and 10 on scan2
    for i in range(15):
        db.add(
            _make_finding(
                scan1.id,
                check_id=f"CHK{i:02d}",
                title=f"Milk Check {i}",
                engine_verdict="fail" if i % 2 == 0 else "pass",
                confidence=0.9,
            )
        )
    for j in range(10):
        db.add(
            _make_finding(
                scan2.id,
                check_id=f"CHK{j:02d}",
                title=f"Bread Check {j}",
                engine_verdict="not_assessed",
                reason="Incomplete coverage",
                confidence=0.5,
            )
        )
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    # Exactly 2 scans
    assert summary["total"] == 2
    assert summary["violations_pending"] == 1
    assert summary["not_assessed"] == 1
    assert summary["low_confidence"] == 1  # scan2 has confidence 0.5 < 0.60

    queue_data = client.get("/review/queue", headers=headers).json()
    assert queue_data["total"] == 2
    assert len(queue_data["items"]) == 2


# ---------------------------------------------------------------------------
# Test 10: Passing rule checks with NULL confidence do NOT trigger low_confidence
# ---------------------------------------------------------------------------
def test_passing_null_confidence_does_not_inflate_queue():
    """Historical audit bug reproduction: 789 items were caused by counting
    every Finding where confidence IS NULL, even passing checks on compliant packages!
    """
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    scan = _make_scan(
        insp.id,
        commodity_generic="Compliant Flour",
        overall_result="compliant",
        checks_total=18,
        checks_assessed=18,
    )
    db.add(scan)
    db.commit()

    # 18 passing checks with NULL confidence (geometry/rule checks)
    for i in range(1, 19):
        db.add(
            _make_finding(
                scan.id,
                check_id=f"CHK{i:02d}",
                title=f"Compliant Rule {i}",
                engine_verdict="pass",
                confidence=None,  # Null confidence on pass
            )
        )
    db.commit()

    headers = _auth(admin)
    summary = client.get("/review/queue/summary", headers=headers).json()
    # Compliant scan with NULL confidence MUST NOT enter review queue!
    assert summary["total"] == 0
    assert summary["low_confidence"] == 0
    assert summary["violations_pending"] == 0
    assert summary["not_assessed"] == 0

    queue_data = client.get("/review/queue", headers=headers).json()
    assert queue_data["total"] == 0
    assert len(queue_data["items"]) == 0


# ---------------------------------------------------------------------------
# Test 11: Absolute consistency between queue summary counts and actual query results
# ---------------------------------------------------------------------------
def test_queue_summary_matches_queue_listing_and_admin_badge():
    env = _setup_queue_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]

    insp = Inspection(
        user_id=admin.id,
        store_id=store.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add(insp)
    db.commit()

    # Create 3 distinct reviewable scans
    s1 = _make_scan(insp.id, commodity_generic="Item 1", overall_result="violation")
    s2 = _make_scan(insp.id, commodity_generic="Item 2", overall_result="not_assessed")
    s3 = _make_scan(insp.id, commodity_generic="Item 3", overall_result="compliant")
    db.add_all([s1, s2, s3])
    db.commit()

    # s1 has violation
    db.add(_make_finding(s1.id, check_id="CHK01", title="F1", engine_verdict="fail", confidence=0.9))
    # s2 has not_assessed
    db.add(_make_finding(s2.id, check_id="CHK02", title="F2", engine_verdict="not_assessed", reason="coverage"))
    # s3 has genuine low confidence finding < 0.60
    db.add(_make_finding(s3.id, check_id="CHK03", title="F3", engine_verdict="pass", confidence=0.45))
    db.commit()

    headers = _auth(admin)

    # 1. /review/queue/summary
    summary = client.get("/review/queue/summary", headers=headers).json()
    # 2. /review/queue
    queue_data = client.get("/review/queue", headers=headers).json()
    # 3. /admin/review-queue
    badge_data = client.get("/admin/review-queue", headers=headers).json()
    # 4. Direct Python query review_queue_size
    query_size = review_queue_size(db)

    # Consistency assertion across all 4 surfaces
    assert summary["total"] == 3
    assert queue_data["total"] == 3
    assert len(queue_data["items"]) == 3
    assert badge_data["count"] == 3
    assert query_size == 3


# ---------------------------------------------------------------------------
# Test 12: RBAC permissions (Inspector isolated to own items; Admin sees all)
# ---------------------------------------------------------------------------
def test_rbac_review_queue_isolation():
    env = _setup_queue_test_env()
    db, client, admin, insp1, insp2, store1, store2 = (
        env["db"],
        env["client"],
        env["admin"],
        env["insp1"],
        env["insp2"],
        env["store1"],
        env["store2"],
    )

    # insp1 submits an inspection with a violation
    insp_row1 = Inspection(
        user_id=insp1.id,
        store_id=store1.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    # insp2 submits an inspection with a violation
    insp_row2 = Inspection(
        user_id=insp2.id,
        store_id=store2.id,
        inspection_date=date.today(),
        status="submitted",
        in_scope=True,
    )
    db.add_all([insp_row1, insp_row2])
    db.commit()

    s1 = _make_scan(insp_row1.id, commodity_generic="Insp1 Item", overall_result="violation")
    s2 = _make_scan(insp_row2.id, commodity_generic="Insp2 Item", overall_result="violation")
    db.add_all([s1, s2])
    db.commit()

    db.add(_make_finding(s1.id, check_id="CHK01", title="F1", engine_verdict="fail", confidence=0.9))
    db.add(_make_finding(s2.id, check_id="CHK01", title="F2", engine_verdict="fail", confidence=0.9))
    db.commit()

    # insp1 can only see 1 item in review queue
    headers1 = _auth(insp1)
    summary1 = client.get("/review/queue/summary", headers=headers1).json()
    assert summary1["total"] == 1
    queue1 = client.get("/review/queue", headers=headers1).json()
    assert queue1["total"] == 1
    assert queue1["items"][0]["scan_id"] == s1.id

    # insp2 can only see 1 item in review queue
    headers2 = _auth(insp2)
    summary2 = client.get("/review/queue/summary", headers=headers2).json()
    assert summary2["total"] == 1
    queue2 = client.get("/review/queue", headers=headers2).json()
    assert queue2["total"] == 1
    assert queue2["items"][0]["scan_id"] == s2.id

    # admin sees ALL (2 items)
    headers_adm = _auth(admin)
    summary_adm = client.get("/review/queue/summary", headers=headers_adm).json()
    assert summary_adm["total"] == 2
    queue_adm = client.get("/review/queue", headers=headers_adm).json()
    assert queue_adm["total"] == 2
