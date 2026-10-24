"""tests/test_review_queue_subtypes.py — Module 6.4 Server-Side Review Queue Subtype Filtering.

Verifies:
1. Subtype correctness:
   - technical_recovery returns only actionable technical error scans
   - recapture_required returns scans needing recapture, alias incomplete_coverage supported
   - unconfirmed_violation vs confirmed_violation distinction
   - low_confidence excludes NULL-confidence deterministic rule checks
   - offline_edit filtering
   - resolved/dismissed excluded from OPEN status
   - draft inspections excluded from officer review
2. Pagination and deduplication:
   - Filtering occurs before pagination at the SQL database level
   - Filtered total reflects all matching scans, not just current page limit
   - Scans with multiple findings appear exactly once (no fan-out)
   - Overlapping categories handled independently without inflating total
   - Ordering is deterministic across pages
3. Filter compatibility and validation:
   - Invalid subtypes return HTTP 422
   - Invalid reasons return HTTP 422
   - Incompatible reason + subtype combinations return HTTP 422
   - Compatible reason + subtype combinations succeed
   - Subtypes work cleanly with area and status filters
   - Existing broad reason filters preserved
4. Summary consistency:
   - Subtype filtered queue total matches summary breakdown count
5. Security and RBAC:
   - Inspector sees only own items under subtype filter
   - Admin sees all items under subtype filter
"""
import os
from datetime import date

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
from queries import get_canonical_queue_metrics
from rbac import get_current_user, require_admin, require_inspector


def _setup_test_env():
    """Create a fresh in-memory database with test client and isolated roles."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    db = Session()

    inspector1 = User(
        employee_id="LM-INSP-201",
        full_name="Officer Sharma",
        password_hash=hash_password("inspPass123!"),
        role="inspector",
        install_id="device-insp-201",
        is_active=True,
    )
    inspector2 = User(
        employee_id="LM-INSP-202",
        full_name="Officer Verma",
        password_hash=hash_password("inspPass123!"),
        role="inspector",
        install_id="device-insp-202",
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


def _make_inspection(db, user_id: int, store_id: int, status: str = "submitted", **kwargs) -> Inspection:
    defaults = {
        "inspection_date": date.today(),
        "in_scope": True,
        "status": status,
    }
    defaults.update(kwargs)
    insp = Inspection(user_id=user_id, store_id=store_id, **defaults)
    db.add(insp)
    db.commit()
    return insp


def _make_scan(db, inspection_id: int, **kwargs) -> Scan:
    defaults = {
        "rules_as_at": date.today(),
        "catalog_hash": "c" * 64,
        "engine_version": "2.4.0",
        "rule_pack_version": "2026.09.v1",
        "overall_result": "violation",
        "checks_total": 5,
        "checks_assessed": 5,
        "commodity_generic": "Packaged Commodity",
    }
    defaults.update(kwargs)
    scan = Scan(inspection_id=inspection_id, **defaults)
    db.add(scan)
    db.commit()
    return scan


def _make_finding(db, scan_id: int, **kwargs) -> Finding:
    defaults = {
        "check_id": "CHK01",
        "title": "Rule Check",
        "engine_verdict": "fail",
        "severity": "major",
    }
    if kwargs.get("human_verdict") is not None and "override_reason" not in kwargs:
        defaults["override_reason"] = "Officer verified finding override"
    defaults.update(kwargs)
    f = Finding(scan_id=scan_id, **defaults)
    db.add(f)
    db.commit()
    return f


# ===========================================================================
# 1. Subtype Correctness Tests
# ===========================================================================

def test_subtype_technical_recovery_filter():
    """technical_recovery returns only actionable technical error scans."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # 1. Technical error scan
    scan_tech = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(
        db, scan_tech.id,
        check_id="CHK_TECH",
        title="OCR Pipeline Failure",
        engine_verdict="not_assessed",
        reason="OCR provider timeout: service unavailable",
        severity="major",
    )

    # 2. Recapture required scan
    scan_recap = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(
        db, scan_recap.id,
        check_id="CHK_RECAP",
        title="Missing MRP Panel",
        engine_verdict="not_assessed",
        reason="Recapture required: back panel image missing",
        severity="major",
    )

    # 3. Violation scan
    scan_viol = _make_scan(db, insp.id, overall_result="violation")
    _make_finding(db, scan_viol.id, engine_verdict="fail", human_verdict=None)

    resp = client.get("/review/queue?subtype=technical_recovery", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["scan_id"] == scan_tech.id
    reasons = data["items"][0]["reasons"]
    assert any(r.get("subtype") == "technical_recovery" for r in reasons)


def test_subtype_recapture_required_filter_and_alias():
    """recapture_required returns scans needing recapture; alias incomplete_coverage works."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # Recapture scan (0 images)
    scan_recap = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(
        db, scan_recap.id,
        check_id="CHK02",
        title="Missing Net Quantity",
        engine_verdict="not_assessed",
        reason="Incomplete coverage: missing image",
    )

    # Technical scan
    scan_tech = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(
        db, scan_tech.id,
        check_id="CHK03",
        engine_verdict="not_assessed",
        reason="Provider technical timeout",
    )

    # Filter with canonical name
    resp1 = client.get("/review/queue?subtype=recapture_required", headers=headers)
    assert resp1.status_code == 200
    data1 = resp1.json()
    assert data1["total"] == 1
    assert data1["items"][0]["scan_id"] == scan_recap.id

    # Filter with alias
    resp2 = client.get("/review/queue?subtype=incomplete_coverage", headers=headers)
    assert resp2.status_code == 200
    assert resp2.json()["total"] == 1
    assert resp2.json()["items"][0]["scan_id"] == scan_recap.id

    # Filter with subtype_filter parameter alias
    resp3 = client.get("/review/queue?subtype_filter=recapture_required", headers=headers)
    assert resp3.status_code == 200
    assert resp3.json()["total"] == 1


def test_subtype_unconfirmed_vs_confirmed_violations():
    """unconfirmed_violation and confirmed_violation are accurately distinguished."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # Scan 1: Unconfirmed violation (human_verdict is None)
    scan_unconfirmed = _make_scan(db, insp.id, overall_result="violation")
    _make_finding(
        db, scan_unconfirmed.id,
        check_id="CHK_UNCONF",
        engine_verdict="fail",
        human_verdict=None,
    )

    # Scan 2: Confirmed violation (human_verdict is 'fail')
    scan_confirmed = _make_scan(db, insp.id, overall_result="violation")
    _make_finding(
        db, scan_confirmed.id,
        check_id="CHK_CONF",
        engine_verdict="fail",
        human_verdict="fail",
    )

    # Query unconfirmed
    resp_unconf = client.get("/review/queue?subtype=unconfirmed_violation", headers=headers)
    assert resp_unconf.status_code == 200
    d_unconf = resp_unconf.json()
    assert d_unconf["total"] == 1
    assert d_unconf["items"][0]["scan_id"] == scan_unconfirmed.id

    # Query confirmed
    resp_conf = client.get("/review/queue?subtype=confirmed_violation", headers=headers)
    assert resp_conf.status_code == 200
    d_conf = resp_conf.json()
    assert d_conf["total"] == 1
    assert d_conf["items"][0]["scan_id"] == scan_confirmed.id

    # Plural aliases
    resp_plural1 = client.get("/review/queue?subtype=unconfirmed_violations", headers=headers)
    assert resp_plural1.status_code == 200
    assert resp_plural1.json()["total"] == 1

    resp_plural2 = client.get("/review/queue?subtype=confirmed_violations", headers=headers)
    assert resp_plural2.status_code == 200
    assert resp_plural2.json()["total"] == 1


def test_subtype_low_confidence_excludes_null_confidence():
    """low_confidence returns genuine confidence < 0.60; excludes NULL-confidence deterministic rules."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # Scan 1: Genuine low confidence (< 0.60, not resolved)
    scan_low_conf = _make_scan(db, insp.id, overall_result="compliant")
    _make_finding(
        db, scan_low_conf.id,
        check_id="CHK_LOW",
        engine_verdict="pass",
        confidence=0.48,
        human_verdict=None,
    )

    # Scan 2: Deterministic check with NULL confidence (passing)
    scan_null_conf = _make_scan(db, insp.id, overall_result="compliant")
    _make_finding(
        db, scan_null_conf.id,
        check_id="CHK_DET",
        engine_verdict="pass",
        confidence=None,
        human_verdict=None,
    )

    # Scan 3: High confidence (0.95)
    scan_high_conf = _make_scan(db, insp.id, overall_result="compliant")
    _make_finding(
        db, scan_high_conf.id,
        check_id="CHK_HIGH",
        engine_verdict="pass",
        confidence=0.95,
        human_verdict=None,
    )

    resp = client.get("/review/queue?subtype=low_confidence", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["scan_id"] == scan_low_conf.id


def test_subtype_offline_edit_filter():
    """offline_edit returns only scans from offline-edited inspections."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    # Inspection 1: Online
    insp1 = _make_inspection(db, admin.id, store.id, edited_offline=False)
    scan1 = _make_scan(db, insp1.id, overall_result="violation")
    _make_finding(db, scan1.id)

    # Inspection 2: Offline-edited
    insp2 = _make_inspection(db, admin.id, store.id, edited_offline=True, clock_skew_seconds=42)
    scan2 = _make_scan(db, insp2.id, overall_result="compliant")
    _make_finding(db, scan2.id, engine_verdict="pass")

    resp = client.get("/review/queue?subtype=offline_edit", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["scan_id"] == scan2.id


def test_draft_inspections_excluded_from_subtype_filters():
    """Draft inspections are never returned by subtype filters in the officer review queue."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    # Draft inspection with technical failure
    insp_draft = _make_inspection(db, admin.id, store.id, status="draft")
    scan_draft = _make_scan(db, insp_draft.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(
        db, scan_draft.id,
        engine_verdict="not_assessed",
        reason="OCR provider timeout",
    )

    # Submitted inspection with technical failure
    insp_submitted = _make_inspection(db, admin.id, store.id, status="submitted")
    scan_sub = _make_scan(db, insp_submitted.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(
        db, scan_sub.id,
        engine_verdict="not_assessed",
        reason="OCR provider timeout",
    )

    resp = client.get("/review/queue?subtype=technical_recovery", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert data["items"][0]["scan_id"] == scan_sub.id


def test_resolved_scans_excluded_from_open_queue_subtypes():
    """Adjudicated/resolved scans are excluded when status_filter is OPEN or omitted."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)
    scan = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(
        db, scan.id,
        engine_verdict="not_assessed",
        reason="OCR provider error",
    )

    # Before resolution: total == 1
    r1 = client.get("/review/queue?subtype=technical_recovery&status_filter=OPEN", headers=headers)
    assert r1.json()["total"] == 1

    # Record resolution in AuditLog
    append_audit(
        db,
        action="review_resolved",
        user_id=admin.id,
        reason="Technical issue assessed and resolved",
        inspection_id=insp.id,
        scan_id=scan.id,
    )

    # After resolution: OPEN queue has 0
    r2 = client.get("/review/queue?subtype=technical_recovery&status_filter=OPEN", headers=headers)
    assert r2.json()["total"] == 0

    # RESOLVED queue has 1
    r3 = client.get("/review/queue?subtype=technical_recovery&status_filter=RESOLVED", headers=headers)
    assert r3.json()["total"] == 1
    assert r3.json()["items"][0]["scan_id"] == scan.id


# ===========================================================================
# 2. Pagination and Deduplication Tests
# ===========================================================================

def test_pagination_and_total_before_pagination():
    """Filtered total reflects all matching distinct scans; pagination slices properly."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # Create 12 technical recovery scans
    tech_ids = []
    for i in range(12):
        s = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
        _make_finding(
            db, s.id,
            check_id=f"CHK_T{i}",
            engine_verdict="not_assessed",
            reason=f"Technical OCR failure code {i}",
        )
        tech_ids.append(s.id)

    # Create 5 recapture scans (should NOT be included in technical recovery)
    for i in range(5):
        s = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
        _make_finding(
            db, s.id,
            check_id=f"CHK_R{i}",
            engine_verdict="not_assessed",
            reason="Recapture required for back panel",
        )

    # Page 1: limit 5, offset 0
    p1 = client.get("/review/queue?subtype=technical_recovery&limit=5&offset=0", headers=headers).json()
    assert p1["total"] == 12  # All 12 matching across DB, not just 5!
    assert len(p1["items"]) == 5
    page1_ids = [item["scan_id"] for item in p1["items"]]

    # Page 2: limit 5, offset 5
    p2 = client.get("/review/queue?subtype=technical_recovery&limit=5&offset=5", headers=headers).json()
    assert p2["total"] == 12
    assert len(p2["items"]) == 5
    page2_ids = [item["scan_id"] for item in p2["items"]]

    # Page 3: limit 5, offset 10
    p3 = client.get("/review/queue?subtype=technical_recovery&limit=5&offset=10", headers=headers).json()
    assert p3["total"] == 12
    assert len(p3["items"]) == 2
    page3_ids = [item["scan_id"] for item in p3["items"]]

    # Verify no overlaps between pages and all 12 are accounted for
    all_returned_ids = page1_ids + page2_ids + page3_ids
    assert len(set(all_returned_ids)) == 12
    assert set(all_returned_ids) == set(tech_ids)


def test_deduplication_single_scan_multiple_matching_findings():
    """A scan with multiple matching findings appears exactly once."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # One scan with 4 separate technical error findings
    scan = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
    for i in range(4):
        _make_finding(
            db, scan.id,
            check_id=f"CHK_ERR_{i}",
            title=f"Error {i}",
            engine_verdict="not_assessed",
            reason="OCR provider timeout",
        )

    resp = client.get("/review/queue?subtype=technical_recovery", headers=headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    assert data["items"][0]["scan_id"] == scan.id


def test_overlapping_categories_handled_independently():
    """A scan matching multiple categories appears in each filter without double-counting in overall queue."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # Scan A: has unconfirmed violation AND genuine low confidence
    scan_a = _make_scan(db, insp.id, overall_result="violation")
    _make_finding(db, scan_a.id, check_id="CHK_V", engine_verdict="fail", human_verdict=None)
    _make_finding(db, scan_a.id, check_id="CHK_LC", engine_verdict="pass", confidence=0.45, human_verdict=None)

    # Scan B: only unconfirmed violation
    scan_b = _make_scan(db, insp.id, overall_result="violation")
    _make_finding(db, scan_b.id, check_id="CHK_VB", engine_verdict="fail", human_verdict=None, confidence=0.99)

    # Scan C: only low confidence
    scan_c = _make_scan(db, insp.id, overall_result="compliant")
    _make_finding(db, scan_c.id, check_id="CHK_LC2", engine_verdict="pass", confidence=0.52, human_verdict=None)

    # Filter unconfirmed_violation: should find Scan A and Scan B (total: 2)
    r_viol = client.get("/review/queue?subtype=unconfirmed_violation", headers=headers).json()
    assert r_viol["total"] == 2
    viol_ids = {i["scan_id"] for i in r_viol["items"]}
    assert viol_ids == {scan_a.id, scan_b.id}

    # Filter low_confidence: should find Scan A and Scan C (total: 2)
    r_lc = client.get("/review/queue?subtype=low_confidence", headers=headers).json()
    assert r_lc["total"] == 2
    lc_ids = {i["scan_id"] for i in r_lc["items"]}
    assert lc_ids == {scan_a.id, scan_c.id}

    # Unfiltered queue total: exactly 3 distinct scans (NOT 2 + 2 = 4)
    r_all = client.get("/review/queue", headers=headers).json()
    assert r_all["total"] == 3


# ===========================================================================
# 3. Filter Compatibility and Validation Tests
# ===========================================================================

def test_invalid_subtype_returns_422():
    """Invalid subtype returns HTTP 422 with informative validation error."""
    env = _setup_test_env()
    client, admin = env["client"], env["admin"]
    headers = _auth(admin)

    resp = client.get("/review/queue?subtype=unknown_invalid_filter", headers=headers)
    assert resp.status_code == 422
    assert "Invalid subtype" in resp.json()["detail"]


def test_invalid_reason_returns_422():
    """Invalid reason returns HTTP 422 with informative validation error."""
    env = _setup_test_env()
    client, admin = env["client"], env["admin"]
    headers = _auth(admin)

    resp = client.get("/review/queue?reason=not_a_valid_reason", headers=headers)
    assert resp.status_code == 422
    assert "Invalid reason" in resp.json()["detail"]


def test_incompatible_reason_and_subtype_returns_422():
    """Conflicting reason and subtype parameters return HTTP 422."""
    env = _setup_test_env()
    client, admin = env["client"], env["admin"]
    headers = _auth(admin)

    # violation cannot be combined with technical_recovery
    r1 = client.get("/review/queue?reason=violation&subtype=technical_recovery", headers=headers)
    assert r1.status_code == 422
    assert "Incompatible filter combination" in r1.json()["detail"]

    # not_assessed cannot be combined with unconfirmed_violation
    r2 = client.get("/review/queue?reason=not_assessed&subtype=unconfirmed_violation", headers=headers)
    assert r2.status_code == 422
    assert "Incompatible filter combination" in r2.json()["detail"]

    # offline_edit cannot be combined with low_confidence
    r3 = client.get("/review/queue?reason=offline_edit&subtype=low_confidence", headers=headers)
    assert r3.status_code == 422


def test_adjudicate_root_missing_inspection_id_returns_422():
    """Missing inspection_id in /review/adjudicate body returns HTTP 422."""
    env = _setup_test_env()
    client, admin = env["client"], env["admin"]
    headers = _auth(admin)

    resp = client.post(
        "/review/adjudicate",
        json={"action": "resolve", "reason": "Valid reason exceeding ten chars"},
        headers=headers,
    )
    assert resp.status_code == 422
    assert "inspection_id required in request body" in resp.json()["detail"]


def test_resolve_conflict_root_missing_inspection_id_returns_422():
    """Missing inspection_id in /review/resolve-conflict body returns HTTP 422."""
    env = _setup_test_env()
    client, admin = env["client"], env["admin"]
    headers = _auth(admin)

    resp = client.post(
        "/review/resolve-conflict",
        json={
            "conflict_id": "CONFLICT-001",
            "resolution": "dismiss",
            "reason": "Valid reason exceeding ten chars",
        },
        headers=headers,
    )
    assert resp.status_code == 422
    assert "inspection_id required in request body" in resp.json()["detail"]


def test_compatible_reason_and_subtype_succeeds():
    """Compatible reason and subtype combinations succeed."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)
    scan = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(db, scan.id, engine_verdict="not_assessed", reason="OCR technical error")

    # reason=not_assessed + subtype=technical_recovery
    r1 = client.get("/review/queue?reason=not_assessed&subtype=technical_recovery", headers=headers)
    assert r1.status_code == 200
    assert r1.json()["total"] == 1

    # reason=all + subtype=technical_recovery
    r2 = client.get("/review/queue?reason=all&subtype=technical_recovery", headers=headers)
    assert r2.status_code == 200
    assert r2.json()["total"] == 1

    # reason=not_assessed + subtype=all
    r3 = client.get("/review/queue?reason=not_assessed&subtype=all", headers=headers)
    assert r3.status_code == 200
    assert r3.json()["total"] == 1


def test_subtype_with_area_filter():
    """Subtype filter works in combination with area filtering."""
    env = _setup_test_env()
    db, client, admin, store1, store2 = (
        env["db"], env["client"], env["admin"], env["store1"], env["store2"]
    )
    headers = _auth(admin)

    # Hyderabad scan
    insp1 = _make_inspection(db, admin.id, store1.id)
    scan_hyd = _make_scan(db, insp1.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(db, scan_hyd.id, engine_verdict="not_assessed", reason="OCR timeout")

    # Vijayawada scan
    insp2 = _make_inspection(db, admin.id, store2.id)
    scan_vij = _make_scan(db, insp2.id, overall_result="not_assessed", checks_assessed=0)
    _make_finding(db, scan_vij.id, engine_verdict="not_assessed", reason="OCR timeout")

    # Area = Hyderabad
    r_hyd = client.get("/review/queue?subtype=technical_recovery&area=Hyderabad", headers=headers).json()
    assert r_hyd["total"] == 1
    assert r_hyd["items"][0]["scan_id"] == scan_hyd.id

    # Area = Vijayawada
    r_vij = client.get("/review/queue?subtype=technical_recovery&area=Vijayawada", headers=headers).json()
    assert r_vij["total"] == 1
    assert r_vij["items"][0]["scan_id"] == scan_vij.id

    # Area = Mumbai (none)
    r_mum = client.get("/review/queue?subtype=technical_recovery&area=Mumbai", headers=headers).json()
    assert r_mum["total"] == 0


def test_existing_broad_reason_filters_preserved():
    """Broad reason filters (not_assessed, violation, low_confidence, offline_edit) behave correctly."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # 1. not_assessed
    s_na = _make_scan(db, insp.id, overall_result="not_assessed")
    _make_finding(db, s_na.id, engine_verdict="not_assessed", reason="Unassessed")

    # 2. violation
    s_v = _make_scan(db, insp.id, overall_result="violation")
    _make_finding(db, s_v.id, engine_verdict="fail", human_verdict=None)

    r_na = client.get("/review/queue?reason=not_assessed", headers=headers).json()
    assert r_na["total"] == 1
    assert r_na["items"][0]["scan_id"] == s_na.id

    r_v = client.get("/review/queue?reason=violation", headers=headers).json()
    assert r_v["total"] == 1
    assert r_v["items"][0]["scan_id"] == s_v.id


# ===========================================================================
# 4. Summary and Metrics Consistency Tests
# ===========================================================================

def test_summary_breakdown_matches_subtype_filtered_queue_totals():
    """Summary breakdown metrics match the total returned by each subtype filter under identical scope."""
    env = _setup_test_env()
    db, client, admin, store = env["db"], env["client"], env["admin"], env["store1"]
    headers = _auth(admin)

    insp = _make_inspection(db, admin.id, store.id)

    # 2 Technical recovery scans
    for i in range(2):
        s = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
        _make_finding(db, s.id, engine_verdict="not_assessed", reason="OCR provider timeout")

    # 3 Recapture required scans
    for i in range(3):
        s = _make_scan(db, insp.id, overall_result="not_assessed", checks_assessed=0)
        _make_finding(db, s.id, engine_verdict="not_assessed", reason="Recapture required for back panel")

    # 4 Unconfirmed violations
    for i in range(4):
        s = _make_scan(db, insp.id, overall_result="violation")
        _make_finding(db, s.id, engine_verdict="fail", human_verdict=None)

    # 1 Confirmed violation
    s_conf = _make_scan(db, insp.id, overall_result="violation")
    _make_finding(db, s_conf.id, engine_verdict="fail", human_verdict="fail")

    # Get summary
    summary = client.get("/review/queue/summary", headers=headers).json()
    breakdown = summary["breakdown"]

    # Verify each subtype endpoint returns the exact total matching the breakdown
    q_tech = client.get("/review/queue?subtype=technical_recovery", headers=headers).json()
    assert q_tech["total"] == breakdown["technical_recovery"] == 2

    q_recap = client.get("/review/queue?subtype=recapture_required", headers=headers).json()
    assert q_recap["total"] == breakdown["recapture_required"] == 3

    q_unconf = client.get("/review/queue?subtype=unconfirmed_violation", headers=headers).json()
    assert q_unconf["total"] == breakdown["unconfirmed_violations"] == 4

    q_conf = client.get("/review/queue?subtype=confirmed_violation", headers=headers).json()
    assert q_conf["total"] == breakdown["confirmed_violations"] == 1


# ===========================================================================
# 5. Security and RBAC Tests
# ===========================================================================

def test_rbac_review_queue_subtype_isolation():
    """Inspectors can only see scans from their own assigned inspections; Admin sees all."""
    env = _setup_test_env()
    db, client, insp1, insp2, admin, store = (
        env["db"], env["client"], env["insp1"], env["insp2"], env["admin"], env["store1"]
    )

    # Insp1 creates 2 technical recovery scans
    insp_row1 = _make_inspection(db, insp1.id, store.id)
    for _ in range(2):
        s = _make_scan(db, insp_row1.id, overall_result="not_assessed", checks_assessed=0)
        _make_finding(db, s.id, engine_verdict="not_assessed", reason="OCR timeout")

    # Insp2 creates 3 technical recovery scans
    insp_row2 = _make_inspection(db, insp2.id, store.id)
    for _ in range(3):
        s = _make_scan(db, insp_row2.id, overall_result="not_assessed", checks_assessed=0)
        _make_finding(db, s.id, engine_verdict="not_assessed", reason="OCR timeout")

    # Inspector 1 sees only 2
    h1 = _auth(insp1)
    r1 = client.get("/review/queue?subtype=technical_recovery", headers=h1).json()
    assert r1["total"] == 2
    for item in r1["items"]:
        assert item["inspector_id"] == insp1.id

    # Inspector 2 sees only 3
    h2 = _auth(insp2)
    r2 = client.get("/review/queue?subtype=technical_recovery", headers=h2).json()
    assert r2["total"] == 3
    for item in r2["items"]:
        assert item["inspector_id"] == insp2.id

    # Admin sees all 5
    h_admin = _auth(admin)
    r_admin = client.get("/review/queue?subtype=technical_recovery", headers=h_admin).json()
    assert r_admin["total"] == 5
