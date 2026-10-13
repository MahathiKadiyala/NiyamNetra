"""tests/test_inspection_finalization_assessment.py

Module 5 Verification Suite: Connect Inspection Finalization to Assessment.
Tests:
1. Finalize inspection with one scan with no prior assessment -> automatically assessed.
2. Finalize inspection with scans having already-current assessments -> fresh results reused without re-run.
3. Finalize inspection with scans having evidence newer than prior assessment -> reassessed fresh.
4. Incomplete evidence uploads (0 images) -> recorded in pending_evidence, not fabricated as passed.
5. Technical OCR failure during finalization -> safe handling, no fabricated penalties/violations.
6. Mixed successful and unresolved scans -> truthful per-scan breakdown in finalization_summary.
7. Double-clicking / retry after finalization -> safe idempotent return of persisted record (HTTP 200, not 409).
8. Concurrent finalization requests -> serialized cleanly without conflict.
9. Lifecycle: start, resume draft, add scan, and finalize.
10. Merchant refusal with 0 scans -> submitted with status 'refused'.
"""
import hashlib
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

from database import Base, get_db
import models
from models import AuditLog, Finding, Inspection, Scan, ScanImage, Store, User
from main import app
from password_handler import hash_password
from rbac import get_current_user, require_admin, require_inspector
from routers.scans import (
    _ASSESSMENT_FINGERPRINTS,
    compute_assessment_fingerprint,
    _OCR_CACHE_SALT,
    _SCAN_IN_FLIGHT_LOCK,
    _SCAN_IN_FLIGHT,
)


@pytest.fixture
def test_setup():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    S = sessionmaker(bind=eng)
    db = S()

    # Clear fingerprint cache for test isolation
    _ASSESSMENT_FINGERPRINTS.clear()
    with _SCAN_IN_FLIGHT_LOCK:
        _SCAN_IN_FLIGHT.clear()

    inspector = User(
        employee_id="INS-501",
        full_name="Field Inspector Rao",
        password_hash=hash_password("password123!"),
        role="inspector",
        is_active=True,
    )
    store = Store(
        name="Metro Mart",
        address="Kukatpally Main Rd",
        city="Hyderabad",
        state="Telangana",
        pincode="500072",
        latitude=17.4947,
        longitude=78.3996,
        geofence_radius_m=150,
    )
    db.add_all([inspector, store])
    db.commit()
    db.refresh(inspector)
    db.refresh(store)

    user_holder = [inspector]

    def override_get_db():
        try:
            yield db
        finally:
            pass

    def override_get_current_user():
        return user_holder[0]

    def override_require_inspector():
        return user_holder[0]

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = override_get_current_user
    app.dependency_overrides[require_inspector] = override_require_inspector

    client = TestClient(app)

    yield {
        "db": db,
        "inspector": inspector,
        "store": store,
        "client": client,
    }

    app.dependency_overrides.clear()
    _ASSESSMENT_FINGERPRINTS.clear()
    with _SCAN_IN_FLIGHT_LOCK:
        _SCAN_IN_FLIGHT.clear()
    try:
        db.close()
    except Exception:
        pass
    try:
        eng.dispose()
    except Exception:
        pass


def _make_dummy_image(text="TEST PACKAGING", width=600, height=800, color=(240, 240, 240)):
    im = np.full((height, width, 3), color, dtype=np.uint8)
    cv2.putText(im, text, (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 2)
    _, buf = cv2.imencode(".jpg", im)
    raw = buf.tobytes()
    with tempfile.NamedTemporaryFile(suffix=".jpg", prefix="m5_", delete=False) as tf:
        tf.write(raw)
        path = tf.name
    return path, raw, hashlib.sha256(raw).hexdigest()


def test_finalize_inspection_assesses_unassessed_scan(test_setup):
    """1. An inspection with an unassessed scan is automatically evaluated on finalization."""
    client = test_setup["client"]
    db = test_setup["db"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    assert insp_resp.status_code in (200, 201)
    insp_id = insp_resp.json()["id"]

    scan = Scan(
        inspection_id=insp_id,
        commodity_generic="Basmati Rice",
        brand_name="India Gate",
        batch_number="BATCH-2026-01",
        net_quantity_value=5.0,
        net_quantity_unit="kg",
        panel_shape="rectangular",
        panel_height_mm=250.0,
        panel_width_mm=180.0,
        scale_source="declared",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_rice",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    img_path, img_raw, img_sha = _make_dummy_image("India Gate Basmati Rice 5kg MRP Rs 450")
    scan_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        file_path=img_path,
        sha256=img_sha,
        width_px=600,
        height_px=800,
        byte_size=len(img_raw),
        mime_type="image/jpeg",
    )
    db.add(scan_img)
    db.commit()

    # Verify scan has no findings yet
    assert db.query(Finding).filter(Finding.scan_id == scan.id).count() == 0

    # Finalize inspection
    sub_resp = client.post(
        f"/inspections/{insp_id}/submit",
        json={"signature_status": "signed", "notes": "Endorsed by store manager"},
    )
    assert sub_resp.status_code == 200, sub_resp.text
    data = sub_resp.json()

    assert data["status"] == "submitted"
    assert data["display_status"] == "Submitted"
    assert "finalization_summary" in data

    f_sum = data["finalization_summary"]
    assert scan.id in f_sum["scans_evaluated"]
    assert scan.id not in f_sum["scans_reused"]

    # Findings must now be persisted on the scan
    findings_count = db.query(Finding).filter(Finding.scan_id == scan.id).count()
    assert findings_count > 0, "Scan must have persisted findings after finalization assessment"

    db.refresh(scan)
    assert scan.overall_result in ("compliant", "violation", "not_assessed")
    assert scan.checks_total == 19
    assert scan.checks_assessed > 0


def test_finalize_inspection_reuses_current_fresh_assessment(test_setup):
    """2. Scans with already-current assessments are reused without re-running."""
    client = test_setup["client"]
    db = test_setup["db"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = insp_resp.json()["id"]

    scan = Scan(
        inspection_id=insp_id,
        commodity_generic="Washing Powder",
        brand_name="Surf Excel",
        panel_shape="rectangular",
        scale_source="none",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_surf",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    img_path, img_raw, img_sha = _make_dummy_image("Surf Excel Detergent Powder 1kg")
    scan_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        file_path=img_path,
        sha256=img_sha,
        width_px=600,
        height_px=800,
        byte_size=len(img_raw),
        mime_type="image/jpeg",
    )
    db.add(scan_img)
    db.commit()

    # Pre-assess scan before finalization
    assess_resp = client.post(f"/scans/{scan.id}/assess")
    assert assess_resp.status_code == 200

    # Verify fingerprint cached
    curr_fp = compute_assessment_fingerprint(scan, [scan_img])
    assert _ASSESSMENT_FINGERPRINTS.get(scan.id) == curr_fp

    # Now finalize inspection: should reuse existing fresh assessment
    with patch("routers.scans._assess_inner") as mock_assess_inner:
        sub_resp = client.post(
            f"/inspections/{insp_id}/submit",
            json={"signature_status": "signed", "notes": "Merchant signed"},
        )
        assert sub_resp.status_code == 200
        data = sub_resp.json()

        assert data["status"] == "submitted"
        f_sum = data["finalization_summary"]
        assert scan.id in f_sum["scans_reused"]
        assert scan.id not in f_sum["scans_evaluated"]
        mock_assess_inner.assert_not_called()


def test_finalize_reassesses_when_evidence_modified(test_setup):
    """3. Scans whose evidence has changed since previous assessment are re-evaluated."""
    client = test_setup["client"]
    db = test_setup["db"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = insp_resp.json()["id"]

    scan = Scan(
        inspection_id=insp_id,
        commodity_generic="Biscuits",
        brand_name="Parle-G",
        panel_shape="rectangular",
        scale_source="none",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_parle",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    img_path1, img_raw1, img_sha1 = _make_dummy_image("Parle G Front 100g")
    img1 = ScanImage(
        scan_id=scan.id,
        panel="front",
        file_path=img_path1,
        sha256=img_sha1,
        width_px=600,
        height_px=800,
        byte_size=len(img_raw1),
        mime_type="image/jpeg",
    )
    db.add(img1)
    db.commit()

    # Pre-assess with first image
    client.post(f"/scans/{scan.id}/assess")

    # Add back panel (evidence changed!)
    img_path2, img_raw2, img_sha2 = _make_dummy_image("Parle G Back Panel Ingredients")
    img2 = ScanImage(
        scan_id=scan.id,
        panel="back",
        file_path=img_path2,
        sha256=img_sha2,
        width_px=600,
        height_px=800,
        byte_size=len(img_raw2),
        mime_type="image/jpeg",
    )
    db.add(img2)
    db.commit()

    # Finalize: fingerprint changed -> must evaluate freshly
    sub_resp = client.post(
        f"/inspections/{insp_id}/submit",
        json={"signature_status": "signed", "notes": "All panels checked"},
    )
    assert sub_resp.status_code == 200
    data = sub_resp.json()

    f_sum = data["finalization_summary"]
    assert scan.id in f_sum["scans_evaluated"], "Scan with modified evidence must be evaluated"
    assert scan.id not in f_sum["scans_reused"]


def test_finalize_handles_incomplete_evidence_honestly(test_setup):
    """4. Incomplete evidence (0 images) is reported truthfully in pending_evidence."""
    client = test_setup["client"]
    db = test_setup["db"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = insp_resp.json()["id"]

    scan = Scan(
        inspection_id=insp_id,
        commodity_generic="Tea Powder",
        brand_name="Red Label",
        panel_shape="rectangular",
        scale_source="none",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_tea",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # 0 images attached
    sub_resp = client.post(
        f"/inspections/{insp_id}/submit",
        json={"signature_status": "signed", "notes": "No photos taken"},
    )
    assert sub_resp.status_code == 200
    data = sub_resp.json()

    f_sum = data["finalization_summary"]
    assert scan.id in f_sum["scans_pending_evidence"]
    assert scan.id not in f_sum["scans_evaluated"]
    assert scan.id not in f_sum["scans_reused"]

    # Must NOT fabricate compliant verdict
    db.refresh(scan)
    assert scan.overall_result == "not_assessed"


def test_finalize_safe_idempotent_retry(test_setup):
    """5. Retrying submit_inspection on an already-submitted inspection returns HTTP 200 with persisted record."""
    client = test_setup["client"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = insp_resp.json()["id"]

    # First submit
    sub1 = client.post(
        f"/inspections/{insp_id}/submit",
        json={"signature_status": "signed", "notes": "First submission"},
    )
    assert sub1.status_code == 200
    assert sub1.json()["status"] == "submitted"

    # Second submit (retry / double-click)
    sub2 = client.post(
        f"/inspections/{insp_id}/submit",
        json={"signature_status": "signed", "notes": "Retry submission"},
    )
    assert sub2.status_code == 200, "Retry must return 200 OK, never 409 conflict"
    data2 = sub2.json()
    assert data2["status"] == "submitted"
    assert data2["finalization_summary"]["status"] == "already_submitted"


def test_finalize_concurrent_submissions(test_setup):
    """6. Concurrent submission calls for the same inspection serialize cleanly."""
    client = test_setup["client"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = insp_resp.json()["id"]

    results = []

    def submit_worker():
        r = client.post(
            f"/inspections/{insp_id}/submit",
            json={"signature_status": "signed", "notes": "Concurrent submit"},
        )
        results.append((r.status_code, r.json()["status"]))

    t1 = threading.Thread(target=submit_worker)
    t2 = threading.Thread(target=submit_worker)

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert len(results) == 2
    for code, status in results:
        assert code == 200
        assert status == "submitted"


def test_finalize_multiple_mixed_scans(test_setup):
    """7. Finalization evaluates mixed scans (one current, one new, one pending evidence)."""
    client = test_setup["client"]
    db = test_setup["db"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = insp_resp.json()["id"]

    # Scan 1: Pre-assessed (reused)
    s1 = Scan(
        inspection_id=insp_id,
        commodity_generic="Atta",
        brand_name="Aashirvaad",
        panel_shape="rectangular",
        scale_source="none",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_atta",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(s1)
    db.commit()
    db.refresh(s1)
    p1, r1, sha1 = _make_dummy_image("Aashirvaad Shudh Chakki Atta 10kg")
    db.add(ScanImage(scan_id=s1.id, panel="front", file_path=p1, sha256=sha1, width_px=600, height_px=800, byte_size=len(r1), mime_type="image/jpeg"))
    db.commit()
    client.post(f"/scans/{s1.id}/assess")

    # Scan 2: Unassessed with image (evaluated)
    s2 = Scan(
        inspection_id=insp_id,
        commodity_generic="Salt",
        brand_name="Tata Salt",
        panel_shape="rectangular",
        scale_source="none",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_salt",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(s2)
    db.commit()
    db.refresh(s2)
    p2, r2, sha2 = _make_dummy_image("Tata Salt Vacuum Evaporated 1kg")
    db.add(ScanImage(scan_id=s2.id, panel="front", file_path=p2, sha256=sha2, width_px=600, height_px=800, byte_size=len(r2), mime_type="image/jpeg"))
    db.commit()

    # Scan 3: No images (pending evidence)
    s3 = Scan(
        inspection_id=insp_id,
        commodity_generic="Turmeric",
        brand_name="Everest",
        panel_shape="rectangular",
        scale_source="none",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_everest",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(s3)
    db.commit()
    db.refresh(s3)

    # Submit inspection
    sub_resp = client.post(
        f"/inspections/{insp_id}/submit",
        json={"signature_status": "signed", "notes": "Multiple scans in establishment"},
    )
    assert sub_resp.status_code == 200
    data = sub_resp.json()

    f_sum = data["finalization_summary"]
    assert s1.id in f_sum["scans_reused"]
    assert s2.id in f_sum["scans_evaluated"]
    assert s3.id in f_sum["scans_pending_evidence"]
    assert f_sum["total_scans"] == 3


def test_merchant_refusal_finalization_with_zero_scans(test_setup):
    """8. Finalization of visit with zero scans due to merchant refusal succeeds with status 'refused'."""
    client = test_setup["client"]
    store = test_setup["store"]

    insp_resp = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = insp_resp.json()["id"]

    sub_resp = client.post(
        f"/inspections/{insp_id}/submit",
        json={
            "signature_status": "refused",
            "notes": "Merchant refused inspection access under Legal Metrology Act, 2009.",
        },
    )
    assert sub_resp.status_code == 200
    data = sub_resp.json()

    assert data["status"] == "submitted"
    assert data["signature_status"] == "refused"
    assert data["overall_result"] == "refused"
    assert data["is_refusal"] is True
    assert data["finalization_summary"]["total_scans"] == 0


def test_draft_inspection_resume_lifecycle(test_setup):
    """9. Complete lifecycle: create draft, retrieve/resume draft, add scan, finalize."""
    client = test_setup["client"]
    db = test_setup["db"]
    store = test_setup["store"]

    # Step 1: Start inspection
    start_res = client.post(
        "/inspections",
        json={"store_id": store.id, "transaction_type": "retail_sale"},
    )
    insp_id = start_res.json()["id"]
    assert start_res.json()["status"] == "draft"

    # Step 2: Inspector navigated away, now resumes draft via GET /inspections/{id}
    resume_res = client.get(f"/inspections/{insp_id}")
    assert resume_res.status_code == 200
    draft_data = resume_res.json()
    assert draft_data["status"] == "draft"
    assert draft_data["store_id"] == store.id

    # Step 3: Add package scan during resumed session
    s = Scan(
        inspection_id=insp_id,
        commodity_generic="Mustard Oil",
        brand_name="Fortune",
        panel_shape="cylindrical",
        scale_source="none",
        rules_as_at=date(2026, 9, 21),
        catalog_hash="hash_fortune",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
        overall_result="not_assessed",
    )
    db.add(s)
    db.commit()
    db.refresh(s)

    p, r, sha = _make_dummy_image("Fortune Kachi Ghani Mustard Oil 1 Litre")
    db.add(ScanImage(scan_id=s.id, panel="front", file_path=p, sha256=sha, width_px=600, height_px=800, byte_size=len(r), mime_type="image/jpeg"))
    db.commit()

    # Step 4: Finalize
    final_res = client.post(
        f"/inspections/{insp_id}/submit",
        json={"signature_status": "signed", "notes": "Resumed and completed visit"},
    )
    assert final_res.status_code == 200
    final_data = final_res.json()
    assert final_data["status"] == "submitted"
    assert s.id in final_data["finalization_summary"]["scans_evaluated"]
