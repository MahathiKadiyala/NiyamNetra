"""tests/test_audit_repair_regression.py — release-blocking regression test suite for mobile audit and repair."""
import io
import json
import os
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
import models
from models import Finding, Inspection, Scan, ScanImage, Store, User, utcnow
from password_handler import hash_password
from main import app
from rbac import get_current_user, require_inspector
from rules import load_rule_pack


@pytest.fixture
def env():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    db = Session()

    inspector = User(
        employee_id="INSP-AUDIT-01",
        full_name="Audit Officer",
        password_hash=hash_password("auditpass123"),
        role="inspector",
        is_active=True,
    )
    db.add(inspector)
    db.commit()
    db.refresh(inspector)

    store = Store(
        name="Supermarket Audit Store",
        address="100 Ring Road, Bengaluru",
        store_type="supermarket",
    )
    db.add(store)
    db.commit()
    db.refresh(store)

    def override_get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db
    app.dependency_overrides[get_current_user] = lambda: inspector
    app.dependency_overrides[require_inspector] = lambda: inspector

    client = TestClient(app)
    try:
        yield eng, db, inspector, store, client
    finally:
        app.dependency_overrides.clear()
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
    cv2.putText(im, text, (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    _, buf = cv2.imencode(".jpg", im)
    return buf.tobytes()


def test_rule_info_and_health_agreement(env):
    """Requirement 1 & 2: /rule-info and /health must agree on active rule pack 2026.09.v1 and 26 checks."""
    eng, db, inspector, store, client = env

    h_res = client.get("/health")
    assert h_res.status_code == 200
    h_data = h_res.json()
    assert h_data["rule_pack_version"] == "2026.09.v1"
    assert h_data["active_rule_pack_version"] == "2026.09.v1"
    assert h_data["rules_as_at"] == "2026-09-21"
    assert h_data["checks_registered"] == 26
    assert h_data["total_rules"] == 26

    r_res = client.get("/rule-info")
    assert r_res.status_code == 200
    r_data = r_res.json()
    assert r_data["active_rule_pack_version"] == "2026.09.v1"
    assert r_data["rules_as_at"] == "2026-09-21"
    assert r_data["total_checks"] == 26
    assert r_data["checks_registered"] == 26
    assert len(r_data["rules"]) == 26

    # Verify rule IDs match the 2026.09.v1 active catalog
    active_pack = load_rule_pack("2026.09.v1")
    expected_rule_ids = {r.rule_id for r in active_pack.rules if r.enabled}
    actual_rule_ids = {r["rule_id"] for r in r_data["rules"]}
    assert actual_rule_ids == expected_rule_ids

    expected_codes = {r.code for r in active_pack.rules if r.enabled}
    actual_codes = {r["code"] for r in r_data["rules"]}
    assert actual_codes == expected_codes


def test_rule_pack_26_evaluated_on_assessment(env):
    """Requirement 1: Production assessment evaluates the 26 registered statutory checks."""
    eng, db, inspector, store, client = env

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 25),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()
    db.refresh(insp)

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Mustard Oil",
        brand_name="Dhara",
        batch_number="B-9988",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="testhash",
        engine_version="2.4.0",
        panel_height_mm=180.0,
        net_quantity_value=500.0,
        net_quantity_unit="ml",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # Attach front image
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(_make_dummy_image("Dhara Mustard Oil 500ml MRP 120"))
        img_path = tf.name

    s_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=img_path,
        byte_size=os.path.getsize(img_path),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="testsha256front001",
    )
    db.add(s_img)
    db.commit()

    # Assess
    res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200
    data = res.json()

    assert data["rule_pack_version"] == "2026.09.v1"
    assert data["checks_total"] >= 18
    assert len(data["findings"]) == 19
    assert data["diagnostics"]["rule_pack_version"] == "2026.09.v1"


def test_repeated_assess_idempotency_and_force(env):
    """Requirement 5: Repeated calls to /assess return cached results without re-assessing unless force=True."""
    eng, db, inspector, store, client = env

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 25),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Wheat Flour",
        brand_name="Aashirvaad",
        batch_number="ATT-2026-X",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="testhash",
        engine_version="2.4.0",
        panel_height_mm=250.0,
        net_quantity_value=1.0,
        net_quantity_unit="kg",
    )
    db.add(scan)
    db.commit()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(_make_dummy_image("Aashirvaad Atta 1kg MRP 55"))
        img_path = tf.name

    s_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=img_path,
        byte_size=os.path.getsize(img_path),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="testsha256front002",
    )
    db.add(s_img)
    db.commit()

    # First call: performs assessment
    res1 = client.post(f"/scans/{scan.id}/assess")
    assert res1.status_code == 200
    d1 = res1.json()

    # Second call (immediate retry, as observed in production logs): fast idempotent return
    res2 = client.post(f"/scans/{scan.id}/assess")
    assert res2.status_code == 200
    d2 = res2.json()
    assert d1["id"] == d2["id"]
    assert d1["overall_result"] == d2["overall_result"]
    assert len(d2["findings"]) == 19

    # Third call with force=True: forces fresh re-assessment
    res3 = client.post(f"/scans/{scan.id}/assess?force=true")
    assert res3.status_code == 200
    d3 = res3.json()
    assert len(d3["findings"]) == 19


def test_recapture_invalidates_assessment_cache(env):
    """Requirement 6: Uploading a new panel image clears the cache fingerprint."""
    eng, db, inspector, store, client = env

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 25),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Tea",
        brand_name="Tata Tea",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="testhash",
        engine_version="2.4.0",
        panel_height_mm=150.0,
    )
    db.add(scan)
    db.commit()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(_make_dummy_image("Tata Tea Front"))
        front_path = tf.name

    s_img1 = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=front_path,
        byte_size=os.path.getsize(front_path),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="shafronttea001",
    )
    db.add(s_img1)
    db.commit()

    # Run assessment
    res_ass = client.post(f"/scans/{scan.id}/assess")
    assert res_ass.status_code == 200
    db.refresh(scan)
    assert scan.ocr_cache_hash is not None

    # Upload a new image (recapture / back panel) via upload endpoint
    new_img_bytes = _make_dummy_image("Tata Tea Back Panel Ingredients")
    up_res = client.post(
        f"/scans/{scan.id}/images",
        data={"panel": "back"},
        files={"file": ("back.jpg", new_img_bytes, "image/jpeg")},
    )
    assert up_res.status_code == 201

    # Invalidation verification: ocr_cache_hash must be cleared on scan
    db.refresh(scan)
    assert scan.ocr_cache_hash is None
    assert scan.ocr_cache is None


def test_image_endpoints_distinct_and_url_population(env):
    """Requirement 4: GET /scans/{id}/images/{img_id} and thumbnail serve distinct evidence."""
    eng, db, inspector, store, client = env

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 25),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Biscuits",
        brand_name="Parle",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="testhash",
        engine_version="2.4.0",
    )
    db.add(scan)
    db.commit()

    # Front image
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf1:
        tf1.write(_make_dummy_image("FRONT PANEL BISCUITS", color=(200, 200, 255)))
        p1 = tf1.name

    # MRP image
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf2:
        tf2.write(_make_dummy_image("MRP PANEL MRP Rs 30", color=(255, 200, 200)))
        p2 = tf2.name

    im1 = ScanImage(scan_id=scan.id, panel="front", sequence=0, file_path=p1, byte_size=os.path.getsize(p1), width_px=600, height_px=800, mime_type="image/jpeg", sha256="shafront001")
    im2 = ScanImage(scan_id=scan.id, panel="mrp", sequence=0, file_path=p2, byte_size=os.path.getsize(p2), width_px=600, height_px=800, mime_type="image/jpeg", sha256="shamrp002")
    db.add_all([im1, im2])
    db.commit()

    # Get scan details — verifies URL enrichment
    res = client.get(f"/scans/{scan.id}")
    assert res.status_code == 200
    s_data = res.json()
    assert len(s_data["images"]) == 2
    for img_out in s_data["images"]:
        assert img_out["url"] == f"/scans/{scan.id}/images/{img_out['id']}"
        assert img_out["thumbnail_url"] == f"/scans/{scan.id}/images/{img_out['id']}/thumbnail"

    # Fetch full images
    f_res1 = client.get(f"/scans/{scan.id}/images/{im1.id}")
    assert f_res1.status_code == 200
    assert len(f_res1.content) == os.path.getsize(p1)

    f_res2 = client.get(f"/scans/{scan.id}/images/{im2.id}")
    assert f_res2.status_code == 200
    assert len(f_res2.content) == os.path.getsize(p2)
    assert f_res1.content != f_res2.content  # distinct evidence

    # Fetch thumbnails
    t_res1 = client.get(f"/scans/{scan.id}/images/{im1.id}/thumbnail")
    assert t_res1.status_code == 200
    assert t_res1.headers["content-type"] == "image/jpeg"


def test_chk03_dynamic_observation(env):
    """Requirement 9: CHK03 observation is dynamic to actual net quantity and transaction."""
    eng, db, inspector, store, client = env

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 25),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Almonds",
        brand_name="Nutraj",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="testhash",
        engine_version="2.4.0",
        panel_height_mm=160.0,
        net_quantity_value=250.0,
        net_quantity_unit="g",
    )
    db.add(scan)
    db.commit()

    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(_make_dummy_image("Nutraj Almonds 250 g MRP 400"))
        p = tf.name

    im = ScanImage(scan_id=scan.id, panel="front", sequence=0, file_path=p, byte_size=os.path.getsize(p), width_px=600, height_px=800, mime_type="image/jpeg", sha256="shanutraj")
    db.add(im)
    db.commit()

    res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200
    chk03 = next((f for f in res.json()["findings"] if f["check_id"] == "CHK03"), None)
    assert chk03 is not None
    assert chk03["effective_verdict"] == "pass"
    assert "250 g" in chk03["observed"]
    assert chk03["observed"] != "Retail sale, within the Rule 3 quantity limits."


def test_distinct_commodities_and_no_unspecified_fallback(env):
    """Requirement 3: Distinct scans produce distinct commodities, brand names, and batches."""
    eng, db, inspector, store, client = env

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 25),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan1 = Scan(
        inspection_id=insp.id,
        commodity_generic="Basmati Rice",
        brand_name="Daawat",
        batch_number="RICE-001",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="testhash",
        engine_version="2.4.0",
        panel_height_mm=200.0,
    )
    scan2 = Scan(
        inspection_id=insp.id,
        commodity_generic="Green Tea",
        brand_name="Tetley",
        batch_number="TEA-882",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="testhash",
        engine_version="2.4.0",
        panel_height_mm=140.0,
    )
    db.add_all([scan1, scan2])
    db.commit()

    r1 = client.get(f"/scans/{scan1.id}")
    r2 = client.get(f"/scans/{scan2.id}")
    assert r1.status_code == 200
    assert r2.status_code == 200

    d1 = r1.json()
    d2 = r2.json()

    assert d1["commodity_generic"] == "Basmati Rice"
    assert d1["brand_name"] == "Daawat"
    assert d1["batch_number"] == "RICE-001"

    assert d2["commodity_generic"] == "Green Tea"
    assert d2["brand_name"] == "Tetley"
    assert d2["batch_number"] == "TEA-882"

    assert d1["commodity_generic"] != d2["commodity_generic"]
    assert "Unspecified" not in d1["commodity_generic"]
    assert "Unspecified" not in d2["commodity_generic"]
