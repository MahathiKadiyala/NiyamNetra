"""tests/test_review_queue_lifecycle.py — Module 6.10: End-to-End Review Queue Lifecycle Regression Tests.

Comprehensive backend regression tests verifying:
- Scenario A: Unconfirmed violation transitions to confirmed violation or resolved upon adjudication.
- Scenario B: Technical OCR recovery transitions out of technical_recovery without fabricating statutory violations.
- Scenario C: Recapture requirement changes queue membership upon evidence fulfilment / reassessment.
- Scenario D: Draft inspections are excluded from officer review queue until finalization/submission, with idempotent retries.
- Scenario H: Subtype pagination occurs at SQL level before slicing, deduplicates multi-finding scans, and maintains summary consistency.
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


def _setup_lifecycle_env():
    """Create fresh isolated in-memory test database, test users, and client."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    db = Session()

    inspector = User(
        employee_id="LM-INSP-E2E",
        full_name="Officer Lifecycle",
        password_hash=hash_password("lifecyclePass123!"),
        role="inspector",
        install_id="device-e2e-01",
        is_active=True,
    )
    admin = User(
        employee_id="LM-ADM-E2E",
        full_name="Admin Lifecycle",
        password_hash=hash_password("adminLifecyclePass123!"),
        role="admin",
        install_id="device-admin-01",
        is_active=True,
    )
    store = Store(
        name="Lifecycle Superstore",
        address="Banjara Hills Rd 12",
        city="Hyderabad",
        district="Hyderabad",
        state="Telangana",
        pincode="500034",
    )
    db.add_all([inspector, admin, store])
    db.commit()
    db.refresh(inspector)
    db.refresh(admin)
    db.refresh(store)

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
        "inspector": inspector,
        "admin": admin,
        "store": store,
    }


def _auth(user: User) -> dict:
    token = create_access_token(user.id, user.role, user.install_id)
    return {"Authorization": f"Bearer {token}"}


def _create_inspection(db, user_id: int, store_id: int, status: str = "submitted", **kwargs) -> Inspection:
    defaults = {
        "inspection_date": date.today(),
        "in_scope": True,
        "status": status,
    }
    defaults.update(kwargs)
    insp = Inspection(user_id=user_id, store_id=store_id, **defaults)
    db.add(insp)
    db.commit()
    db.refresh(insp)
    return insp


def _create_scan(db, inspection_id: int, **kwargs) -> Scan:
    defaults = {
        "rules_as_at": date.today(),
        "catalog_hash": "e2e" + "0" * 61,
        "engine_version": "2.4.0",
        "rule_pack_version": "2026.09.v1",
        "overall_result": "violation",
        "checks_total": 5,
        "checks_assessed": 5,
        "commodity_generic": "Packaged Wheat Flour",
    }
    defaults.update(kwargs)
    scan = Scan(inspection_id=inspection_id, **defaults)
    db.add(scan)
    db.commit()
    db.refresh(scan)
    return scan


def _create_finding(db, scan_id: int, **kwargs) -> Finding:
    defaults = {
        "check_id": "CHK01",
        "title": "MRP Rule Verification",
        "engine_verdict": "fail",
        "severity": "major",
    }
    defaults.update(kwargs)
    f = Finding(scan_id=scan_id, **defaults)
    db.add(f)
    db.commit()
    db.refresh(f)
    return f


# ===========================================================================
# Scenario A: Unconfirmed violation becomes confirmed or resolved
# ===========================================================================

def test_scenario_a_unconfirmed_to_confirmed_and_resolved():
    """Scenario A:
    1. Scan starts as unconfirmed violation (engine_verdict='fail', human_verdict=None).
    2. Appears in unconfirmed_violation subtype and open queue summary.
    3. Officer confirms violation via finding adjudication (human_verdict='fail').
    4. Backend persists confirmation and moves scan to confirmed_violation subtype.
    5. Officer resolves review item (action='resolve').
    6. Scan leaves OPEN queue and appears in RESOLVED queue.
    """
    env = _setup_lifecycle_env()
    client, db = env["client"], env["db"]
    headers = _auth(env["inspector"])

    insp = _create_inspection(db, env["inspector"].id, env["store"].id, status="submitted")
    scan = _create_scan(db, insp.id, overall_result="violation")
    finding = _create_finding(db, scan.id, check_id="CHK01", engine_verdict="fail", human_verdict=None)

    # 1. Fetch open queue summary
    sum_resp = client.get("/review/queue/summary", headers=headers)
    assert sum_resp.status_code == 200
    sum_data = sum_resp.json()
    assert sum_data["total"] == 1
    assert sum_data["breakdown"]["unconfirmed_violations"] == 1
    assert sum_data["breakdown"]["confirmed_violations"] == 0

    # 2. Fetch unconfirmed_violation queue
    q_resp = client.get("/review/queue?subtype=unconfirmed_violation", headers=headers)
    assert q_resp.status_code == 200
    q_data = q_resp.json()
    assert q_data["total"] == 1
    assert q_data["items"][0]["scan_id"] == scan.id
    assert any(r.get("subtype") == "unconfirmed_violation" for r in q_data["items"][0]["reasons"])

    # 3. Adjudicate finding: Confirm violation
    adj_resp = client.post(
        f"/review/inspections/{insp.id}/adjudicate",
        headers=headers,
        json={
            "action": "override",
            "finding_id": finding.id,
            "human_verdict": "fail",
            "reason": "Officer physically inspected package and confirmed MRP declaration is missing.",
        },
    )
    assert adj_resp.status_code == 200
    adj_data = adj_resp.json()
    assert adj_data["success"] is True
    assert adj_data["new_verdict"] == "fail"

    # Verify persisted DB state
    db.refresh(finding)
    assert finding.human_verdict == "fail"
    assert finding.overridden_by == env["inspector"].id

    # 4. Fetch queue again: scan must NOT be in unconfirmed_violation
    q_unconf = client.get("/review/queue?subtype=unconfirmed_violation", headers=headers)
    assert q_unconf.status_code == 200
    assert q_unconf.json()["total"] == 0

    # Scan must now appear in confirmed_violation
    q_conf = client.get("/review/queue?subtype=confirmed_violation", headers=headers)
    assert q_conf.status_code == 200
    assert q_conf.json()["total"] == 1
    assert q_conf.json()["items"][0]["scan_id"] == scan.id
    assert any(r.get("subtype") == "confirmed_violation" for r in q_conf.json()["items"][0]["reasons"])

    # Summary must reflect shift
    sum_data2 = client.get("/review/queue/summary", headers=headers).json()
    assert sum_data2["breakdown"]["unconfirmed_violations"] == 0
    assert sum_data2["breakdown"]["confirmed_violations"] == 1
    assert sum_data2["total"] == 1

    # 5. Officer resolves review item
    res_resp = client.post(
        f"/review/inspections/{insp.id}/adjudicate",
        headers=headers,
        json={
            "action": "resolve",
            "reason": "Merchant accepted statutory compound fee and rectified packaging label.",
            "resolution_notes": "Compounding receipt #CR-9876 attached.",
        },
    )
    assert res_resp.status_code == 200
    assert res_resp.json()["success"] is True

    # 6. Verify scan leaves OPEN queue
    open_q = client.get("/review/queue?status_filter=OPEN", headers=headers).json()
    assert open_q["total"] == 0

    open_sum = client.get("/review/queue/summary?status_filter=OPEN", headers=headers).json()
    assert open_sum["total"] == 0

    # Verify scan appears in RESOLVED queue
    resolved_q = client.get("/review/queue?status_filter=RESOLVED", headers=headers).json()
    assert resolved_q["total"] == 1
    assert resolved_q["items"][0]["scan_id"] == scan.id


def test_scenario_a_finding_override_to_pass_clears_queue():
    """Scenario A2:
    When an unconfirmed finding is overridden to 'pass', scan overall_result
    recomputes to 'compliant', cleanly leaving the open review queue.
    """
    env = _setup_lifecycle_env()
    client, db = env["client"], env["db"]
    headers = _auth(env["inspector"])

    insp = _create_inspection(db, env["inspector"].id, env["store"].id, status="submitted")
    scan = _create_scan(db, insp.id, overall_result="violation")
    finding = _create_finding(db, scan.id, check_id="CHK02", engine_verdict="fail", human_verdict=None)

    # Initial check: in open queue
    init_sum = client.get("/review/queue/summary", headers=headers).json()
    assert init_sum["total"] == 1

    # Override finding to 'pass'
    adj_resp = client.post(
        f"/review/inspections/{insp.id}/adjudicate",
        headers=headers,
        json={
            "action": "override",
            "finding_id": finding.id,
            "human_verdict": "pass",
            "reason": "Officer verified Net Quantity declaration is clearly visible on side panel.",
        },
    )
    assert adj_resp.status_code == 200
    assert adj_resp.json()["scan_result"] == "compliant"

    # Scan overall_result is now compliant
    db.refresh(scan)
    assert scan.overall_result == "compliant"

    # Scan leaves open queue completely
    after_q = client.get("/review/queue?status_filter=OPEN", headers=headers).json()
    assert after_q["total"] == 0


# ===========================================================================
# Scenario B: Technical OCR recovery and reassessment
# ===========================================================================

def test_scenario_b_technical_ocr_recovery_lifecycle():
    """Scenario B:
    1. Scan encounters technical OCR failure (engine_verdict='not_assessed', reason contains OCR timeout).
    2. Verifies scan appears under 'technical_recovery' subtype.
    3. OCR failure alone NEVER generates fabricated MRP or net-quantity violations or penalties.
    4. Successful reassessment / finding override clears technical_recovery category.
    5. Stale technical-recovery entries do not remain visible under technical_recovery.
    """
    env = _setup_lifecycle_env()
    client, db = env["client"], env["db"]
    headers = _auth(env["inspector"])

    insp = _create_inspection(db, env["inspector"].id, env["store"].id, status="submitted")
    scan = _create_scan(
        db,
        insp.id,
        overall_result="not_assessed",
        checks_total=5,
        checks_assessed=0,
    )
    finding = _create_finding(
        db,
        scan.id,
        check_id="CHK_OCR",
        title="OCR Service Status",
        engine_verdict="not_assessed",
        human_verdict=None,
        reason="OCR provider timeout: Google Cloud Vision API endpoint unreachable during field inspection",
    )

    # 1. Verify subtype is technical_recovery
    q_resp = client.get("/review/queue?subtype=technical_recovery", headers=headers)
    assert q_resp.status_code == 200
    q_data = q_resp.json()
    assert q_data["total"] == 1
    assert q_data["items"][0]["scan_id"] == scan.id
    assert any(r.get("subtype") == "technical_recovery" for r in q_data["items"][0]["reasons"])

    # 2. Invariant: OCR failure alone NEVER produces 'violation' or statutory penalty
    assert scan.overall_result == "not_assessed"
    assert scan.overall_result != "violation"
    assert finding.engine_verdict == "not_assessed"
    assert finding.engine_verdict != "fail"

    # Queue summary shows technical_recovery = 1, violations = 0
    sum_data = client.get("/review/queue/summary", headers=headers).json()
    assert sum_data["breakdown"]["technical_recovery"] == 1
    assert sum_data["violations_pending"] == 0
    assert sum_data["breakdown"]["confirmed_violations"] == 0

    # 3. Simulate recovery/reassessment: finding adjudicated with valid verdict
    adj_resp = client.post(
        f"/review/inspections/{insp.id}/adjudicate",
        headers=headers,
        json={
            "action": "override",
            "finding_id": finding.id,
            "human_verdict": "pass",
            "reason": "Manual offline assessment completed with high-resolution backup photo.",
        },
    )
    assert adj_resp.status_code == 200
    assert adj_resp.json()["scan_result"] == "compliant"

    # 4. Verify technical_recovery category is now cleared
    q_after = client.get("/review/queue?subtype=technical_recovery", headers=headers).json()
    assert q_after["total"] == 0

    sum_after = client.get("/review/queue/summary", headers=headers).json()
    assert sum_after["breakdown"]["technical_recovery"] == 0
    assert sum_after["total"] == 0


# ===========================================================================
# Scenario C: Recapture changes queue membership
# ===========================================================================

def test_scenario_c_recapture_changes_queue_membership():
    """Scenario C:
    1. Scan requires recapture (e.g. missing panel / zero images / recapture guidance).
    2. Scan appears in 'recapture_required' subtype.
    3. Reassessment / evidence update satisfies capture guidance.
    4. Scan transitions out of recapture_required subtype.
    5. Any genuine unresolved findings remain actionable.
    """
    env = _setup_lifecycle_env()
    client, db = env["client"], env["db"]
    headers = _auth(env["inspector"])

    insp = _create_inspection(db, env["inspector"].id, env["store"].id, status="submitted")
    scan = _create_scan(
        db,
        insp.id,
        overall_result="not_assessed",
        checks_total=5,
        checks_assessed=2,
    )
    finding = _create_finding(
        db,
        scan.id,
        check_id="CHK_RECAP",
        title="Coverage Assessment",
        engine_verdict="not_assessed",
        human_verdict=None,
        reason="Missing back panel image with principal display; recapture required for mandatory declarations",
    )

    # 1. Verify in recapture_required subtype
    q_resp = client.get("/review/queue?subtype=recapture_required", headers=headers)
    assert q_resp.status_code == 200
    assert q_resp.json()["total"] == 1
    assert q_resp.json()["items"][0]["scan_id"] == scan.id
    assert any(r.get("subtype") == "recapture_required" for r in q_resp.json()["items"][0]["reasons"])

    # Also supports alias incomplete_coverage
    q_alias = client.get("/review/queue?subtype=incomplete_coverage", headers=headers)
    assert q_alias.status_code == 200
    assert q_alias.json()["total"] == 1

    # 2. Simulate evidence capture and reassessment resulting in an unconfirmed violation
    finding.reason = "Back panel captured and verified"
    finding.engine_verdict = "fail"
    scan.overall_result = "violation"
    db.commit()

    # 3. Verify scan has moved from recapture_required to unconfirmed_violation
    q_recap_after = client.get("/review/queue?subtype=recapture_required", headers=headers).json()
    assert q_recap_after["total"] == 0

    q_unconf_after = client.get("/review/queue?subtype=unconfirmed_violation", headers=headers).json()
    assert q_unconf_after["total"] == 1
    assert q_unconf_after["items"][0]["scan_id"] == scan.id


# ===========================================================================
# Scenario D: Inspection finalization updates queue and draft metrics
# ===========================================================================

def test_scenario_d_draft_inspection_excluded_and_submission_updates_queue():
    """Scenario D:
    1. Draft inspection (status='draft') with scans is EXCLUDED from review queue.
    2. Submitting/finalizing inspection moves scans into actionable queue.
    3. Idempotent re-submission does not duplicate findings or queue items.
    """
    env = _setup_lifecycle_env()
    client, db = env["client"], env["db"]
    headers = _auth(env["inspector"])

    # 1. Create a draft inspection
    draft_insp = _create_inspection(db, env["inspector"].id, env["store"].id, status="draft")
    scan = _create_scan(db, draft_insp.id, overall_result="violation")
    finding = _create_finding(db, scan.id, check_id="CHK01", engine_verdict="fail", human_verdict=None)

    # Verify review queue is empty while inspection is in draft status
    q_draft = client.get("/review/queue", headers=headers).json()
    assert q_draft["total"] == 0
    sum_draft = client.get("/review/queue/summary", headers=headers).json()
    assert sum_draft["total"] == 0
    assert sum_draft["breakdown"]["unconfirmed_violations"] == 0
    assert sum_draft["breakdown"]["draft_inspections"] == 1

    # 2. Finalize / submit inspection via POST /inspections/{id}/submit
    submit_resp = client.post(
        f"/inspections/{draft_insp.id}/submit",
        headers=headers,
        json={"signature_status": "signed"},
    )
    assert submit_resp.status_code == 200
    db.refresh(draft_insp)
    assert draft_insp.status == "submitted"

    # 3. Review queue now reflects the submitted inspection's scans
    q_sub = client.get("/review/queue", headers=headers).json()
    assert q_sub["total"] == 1
    assert q_sub["items"][0]["scan_id"] == scan.id

    sum_sub = client.get("/review/queue/summary", headers=headers).json()
    assert sum_sub["total"] == 1
    assert sum_sub["breakdown"]["unconfirmed_violations"] == 1
    assert sum_sub["breakdown"]["draft_inspections"] == 0

    # 4. Idempotent retry of submission
    retry_resp = client.post(
        f"/inspections/{draft_insp.id}/submit",
        headers=headers,
        json={"signature_status": "signed"},
    )
    assert retry_resp.status_code == 200
    assert retry_resp.json()["finalization_summary"]["status"] == "already_submitted"

    # Assert queue total remains exactly 1 (no duplicate scans or findings)
    q_retry = client.get("/review/queue", headers=headers).json()
    assert q_retry["total"] == 1
    assert len(q_retry["items"]) == 1


# ===========================================================================
# Scenario H: Subtype pagination, deduplication, and summary consistency
# ===========================================================================

def test_scenario_h_subtype_pagination_deduplication_and_summary_consistency():
    """Scenario H:
    1. Multi-finding scan appears exactly ONCE in review queue (no duplicate join fan-out).
    2. Subtype filtering occurs before pagination at the SQL level.
    3. Filtered total covers all matching items across pages.
    4. Offset and limit paginate correctly.
    5. Summary counts represent distinct scans, not inflated sums.
    """
    env = _setup_lifecycle_env()
    client, db = env["client"], env["db"]
    headers = _auth(env["inspector"])

    insp = _create_inspection(db, env["inspector"].id, env["store"].id, status="submitted")

    # Create Scan 1 with 3 failing findings (test deduplication)
    scan1 = _create_scan(db, insp.id, overall_result="violation")
    _create_finding(db, scan1.id, check_id="CHK01", engine_verdict="fail", human_verdict=None)
    _create_finding(db, scan1.id, check_id="CHK02", engine_verdict="fail", human_verdict=None)
    _create_finding(db, scan1.id, check_id="CHK03", engine_verdict="fail", human_verdict=None)

    # Create Scans 2, 3, 4 with single failing findings
    scan2 = _create_scan(db, insp.id, overall_result="violation")
    _create_finding(db, scan2.id, check_id="CHK01", engine_verdict="fail", human_verdict=None)

    scan3 = _create_scan(db, insp.id, overall_result="violation")
    _create_finding(db, scan3.id, check_id="CHK01", engine_verdict="fail", human_verdict=None)

    scan4 = _create_scan(db, insp.id, overall_result="violation")
    _create_finding(db, scan4.id, check_id="CHK01", engine_verdict="fail", human_verdict=None)

    # 1. Assert deduplication: 4 scans total in queue, NOT 6 rows
    q_all = client.get("/review/queue?subtype=unconfirmed_violation", headers=headers).json()
    assert q_all["total"] == 4
    scan_ids = [item["scan_id"] for item in q_all["items"]]
    assert len(scan_ids) == 4
    assert len(set(scan_ids)) == 4
    assert scan1.id in scan_ids

    # 2. Assert pagination: limit=2, offset=0
    page1 = client.get(
        "/review/queue?subtype=unconfirmed_violation&limit=2&offset=0",
        headers=headers,
    ).json()
    assert page1["total"] == 4  # Total reflects ALL matching scans
    assert len(page1["items"]) == 2

    # Page 2: limit=2, offset=2
    page2 = client.get(
        "/review/queue?subtype=unconfirmed_violation&limit=2&offset=2",
        headers=headers,
    ).json()
    assert page2["total"] == 4
    assert len(page2["items"]) == 2

    # The items across pages must be distinct and non-overlapping
    p1_ids = {item["scan_id"] for item in page1["items"]}
    p2_ids = {item["scan_id"] for item in page2["items"]}
    assert p1_ids.isdisjoint(p2_ids)
    assert p1_ids.union(p2_ids) == set(scan_ids)

    # 3. Summary consistency
    sum_data = client.get("/review/queue/summary", headers=headers).json()
    assert sum_data["total"] == 4
    assert sum_data["breakdown"]["unconfirmed_violations"] == 4
    assert sum_data["violations_pending"] == 4
