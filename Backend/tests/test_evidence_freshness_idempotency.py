"""tests/test_evidence_freshness_idempotency.py — Module 4 Evidence Freshness, Cache Invalidation, and Idempotency regression tests."""
import os
import tempfile
import threading
import time
from datetime import date
from unittest.mock import patch, MagicMock

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from image_processor import prepare_derived_analysis_image
from main import app
from models import Finding, Inspection, Scan, ScanImage, Store, User
from ocr.base import OcrLine, OcrResult, STATUS_PROVIDER_ERROR, STATUS_SUCCESS
from password_handler import hash_password
from rbac import get_current_user, require_inspector
from routers.scans import (
    _ASSESSMENT_FINGERPRINTS,
    _SCAN_IN_FLIGHT,
    _SCAN_IN_FLIGHT_LOCK,
    compute_assessment_fingerprint,
)


@pytest.fixture
def env():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db = Session()

    inspector = User(
        employee_id="INSP-FRESH-01",
        full_name="Freshness Officer",
        password_hash=hash_password("freshpass123"),
        role="inspector",
        is_active=True,
    )
    db.add(inspector)
    db.commit()
    db.refresh(inspector)

    store = Store(
        name="Freshness Test Mart",
        address="42 Residency Road, Bengaluru",
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
    cv2.putText(im, text, (40, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (20, 20, 20), 2)
    _, buf = cv2.imencode(".jpg", im)
    return buf.tobytes()


def _make_temp_image_file(text="TEST EVIDENCE", prefix="fresh_"):
    raw = _make_dummy_image(text)
    with tempfile.NamedTemporaryFile(suffix=".jpg", prefix=prefix, delete=False) as tf:
        tf.write(raw)
        path = tf.name
    return path, raw


def test_recapture_after_successful_assessment_invalidates_cache_and_findings(env):
    """Requirement: Recapture after successful assessment clears cache and old findings."""
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
        catalog_hash="hash_parle",
        engine_version="2.4.0",
        panel_height_mm=120.0,
    )
    db.add(scan)
    db.commit()

    front_path, front_raw = _make_temp_image_file("Parle G Front Panel NET QTY 100 g MRP 10", "front_")
    s_img1 = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=front_path,
        byte_size=len(front_raw),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="sha256_parle_front_01",
    )
    db.add(s_img1)
    db.commit()

    mock_lines = [
        OcrLine(text="PARLE G BISCUITS", confidence=0.95, box=[[10, 10], [100, 10], [100, 30], [10, 30]], height_px=14.0),
        OcrLine(text="NET QTY 100 g", confidence=0.96, box=[[10, 40], [80, 40], [80, 60], [10, 60]], height_px=12.0),
        OcrLine(text="MRP Rs. 10.00", confidence=0.97, box=[[10, 70], [90, 70], [90, 90], [10, 90]], height_px=12.0),
    ]
    mock_ocr = OcrResult(
        lines=mock_lines,
        engine="mock_engine",
        mean_confidence=0.96,
        failure_reason=None,
        status=STATUS_SUCCESS,
    )

    with patch("ocr_engine.run_ocr", return_value=mock_ocr):
        # 1. Run initial assessment
        res1 = client.post(f"/scans/{scan.id}/assess")
        assert res1.status_code == 200
        db.refresh(scan)
        assert scan.ocr_cache_hash is not None
        assert scan.overall_result != "not_assessed"
        fp1 = res1.json()["diagnostics"]["assessment_input_fingerprint"]

        # 2. Upload recapture / additional back panel
        back_raw = _make_dummy_image("Parle G Back Panel Ingredients and Mfg Date")
        res_up = client.post(
            f"/scans/{scan.id}/images",
            data={"panel": "back"},
            files={"file": ("back.jpg", back_raw, "image/jpeg")},
        )
        assert res_up.status_code in (200, 201)

        # 3. Verify cache invalidation & evidence freshness on scan
        db.refresh(scan)
        assert scan.ocr_cache_hash is None
        assert scan.ocr_cache is None
        assert scan.llm_cache_hash is None
        assert scan.overall_result == "not_assessed"
        assert scan.checks_assessed == 0

        # Old findings must NOT survive
        findings_after_upload = db.query(Finding).filter(Finding.scan_id == scan.id).all()
        assert len(findings_after_upload) == 0

        # 4. GET /scans/{scan.id} reflects unassessed state honestly
        get_res = client.get(f"/scans/{scan.id}")
        assert get_res.status_code == 200
        assert get_res.json()["overall_result"] == "not_assessed"
        assert len(get_res.json()["findings"]) == 0

        # 5. Reassessment evaluates updated evidence set
        res2 = client.post(f"/scans/{scan.id}/assess")
        assert res2.status_code == 200
        db.refresh(scan)
        assert scan.ocr_cache_hash is not None
        fp2 = res2.json()["diagnostics"]["assessment_input_fingerprint"]
        assert fp1 != fp2
        assert len(res2.json()["images"]) == 2


def test_reuse_valid_cached_result_when_evidence_unchanged(env):
    """Requirement: Unchanged evidence reuses valid cached assessment idempotently."""
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
        catalog_hash="hash_tea",
        engine_version="2.4.0",
        panel_height_mm=150.0,
    )
    db.add(scan)
    db.commit()

    f_path, f_raw = _make_temp_image_file("Tata Tea Premium 250g MRP 120", "tea_")
    s_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=f_path,
        byte_size=len(f_raw),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="sha256_tea_front",
    )
    db.add(s_img)
    db.commit()

    mock_lines = [
        OcrLine(text="TATA TEA PREMIUM", confidence=0.95, box=[[10, 10], [100, 10], [100, 30], [10, 30]], height_px=14.0),
        OcrLine(text="NET QTY 250 g", confidence=0.96, box=[[10, 40], [80, 40], [80, 60], [10, 60]], height_px=12.0),
        OcrLine(text="MRP Rs. 120.00", confidence=0.97, box=[[10, 70], [90, 70], [90, 90], [10, 90]], height_px=12.0),
    ]
    mock_ocr = OcrResult(
        lines=mock_lines,
        engine="mock_engine",
        mean_confidence=0.96,
        failure_reason=None,
        status=STATUS_SUCCESS,
    )

    with patch("ocr_engine.run_ocr", return_value=mock_ocr):
        # First call
        r1 = client.post(f"/scans/{scan.id}/assess")
        assert r1.status_code == 200
        d1 = r1.json()

        # Second call (unchanged evidence): fast idempotent return
        r2 = client.post(f"/scans/{scan.id}/assess")
        assert r2.status_code == 200
        d2 = r2.json()

    assert d1["overall_result"] == d2["overall_result"]
    assert d1["diagnostics"]["assessment_input_fingerprint"] == d2["diagnostics"]["assessment_input_fingerprint"]
    assert len(d1["findings"]) == len(d2["findings"])


def test_failed_ocr_never_cached_as_successful_empty_text(env):
    """Requirement: Failed OCR provider operation must never be permanently cached as successful."""
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
        commodity_generic="Soap",
        brand_name="Dove",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_dove",
        engine_version="2.4.0",
        panel_height_mm=80.0,
    )
    db.add(scan)
    db.commit()

    img_path, img_raw = _make_temp_image_file("Dove Soap", "dove_")
    s_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=img_path,
        byte_size=len(img_raw),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="sha256_dove_01",
    )
    db.add(s_img)
    db.commit()

    # Mock OCR engine to simulate complete provider outage
    with patch("ocr_engine.run_ocr") as mock_ocr:
        mock_ocr.return_value = OcrResult(
            lines=[],
            engine="google_vision",
            mean_confidence=None,
            failure_reason="HTTP 403: Billing disabled",
            status=STATUS_PROVIDER_ERROR,
        )
        res = client.post(f"/scans/{scan.id}/assess")
        assert res.status_code == 200

        db.refresh(scan)
        # Invariant: failed OCR MUST NOT set ocr_cache_hash or ocr_cache
        assert scan.ocr_cache_hash is None
        assert scan.ocr_cache is None
        assert scan.llm_cache_hash is None

        # Legal protection (Module 2): Failure does NOT fabricate Section 36 penalties
        res_data = res.json()
        chk18 = next((f for f in res_data["findings"] if f["check_id"] == "CHK18"), None)
        assert chk18 is not None
        assert chk18["engine_verdict"] == "not_assessed"


def test_ocr_provider_fallback_recovery_and_retry(env):
    """Requirement: Failed primary provider followed by usable alternate succeeds and caches fresh result."""
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
        commodity_generic="Oil",
        brand_name="Fortune",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_fortune",
        engine_version="2.4.0",
        panel_height_mm=220.0,
    )
    db.add(scan)
    db.commit()

    img_path, img_raw = _make_temp_image_file("Fortune Oil NET QTY 1 L MRP 150", "fortune_")
    s_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=img_path,
        byte_size=len(img_raw),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="sha256_fortune_01",
    )
    db.add(s_img)
    db.commit()

    # Step 1: Provider outage
    with patch("ocr_engine.run_ocr") as mock_ocr:
        mock_ocr.return_value = OcrResult(
            lines=[],
            engine="none",
            mean_confidence=None,
            failure_reason="Network Timeout",
            status=STATUS_PROVIDER_ERROR,
        )
        r_fail = client.post(f"/scans/{scan.id}/assess")
        assert r_fail.status_code == 200
        db.refresh(scan)
        assert scan.ocr_cache_hash is None

    # Step 2: Retry succeeds with fallback
    fallback_lines = [
        OcrLine(text="FORTUNE SUNFLOWER OIL", confidence=0.95, box=[[10, 10], [100, 10], [100, 30], [10, 30]], height_px=14.0),
        OcrLine(text="NET QTY 1 L", confidence=0.96, box=[[10, 40], [80, 40], [80, 60], [10, 60]], height_px=12.0),
        OcrLine(text="MRP Rs. 150.00", confidence=0.97, box=[[10, 70], [90, 70], [90, 90], [10, 90]], height_px=12.0),
    ]
    with patch("ocr_engine.run_ocr") as mock_ocr:
        mock_ocr.return_value = OcrResult(
            lines=fallback_lines,
            engine="ocr_space",
            mean_confidence=0.96,
            failure_reason=None,
            status=STATUS_SUCCESS,
        )
        r_success = client.post(f"/scans/{scan.id}/assess")
        assert r_success.status_code == 200
        db.refresh(scan)
        assert scan.ocr_cache_hash is not None
        assert scan.ocr_cache is not None


def test_ocr_provider_config_change_invalidates_fingerprint(env):
    """Requirement: Change in OCR provider or chain configuration changes fingerprint."""
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
        commodity_generic="Flour",
        brand_name="Aashirvaad",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_flour",
        engine_version="2.4.0",
        panel_height_mm=250.0,
    )
    db.add(scan)
    db.commit()

    f_path, f_raw = _make_temp_image_file("Aashirvaad Atta 5 kg", "atta_")
    s_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=f_path,
        byte_size=len(f_raw),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="sha256_atta_front",
    )
    db.add(s_img)
    db.commit()

    with patch.object(settings, "OCR_PROVIDER", "google_vision"), patch.object(settings, "OCR_PROVIDER_CHAIN", "google_vision,ocr_space"):
        fp1 = compute_assessment_fingerprint(scan, [s_img])

    with patch.object(settings, "OCR_PROVIDER", "ocr_space"), patch.object(settings, "OCR_PROVIDER_CHAIN", "ocr_space,tesseract"):
        fp2 = compute_assessment_fingerprint(scan, [s_img])

    assert fp1 != fp2


def test_rule_pack_version_change_invalidates_fingerprint(env):
    """Requirement: Legal rule-pack version change invalidates assessment fingerprint."""
    eng, db, inspector, store, client = env

    scan = Scan(
        inspection_id=1,
        commodity_generic="Sugar",
        brand_name="Madhur",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_sugar",
        engine_version="2.4.0",
        rule_pack_version="2026.09.v1",
    )
    s_img = ScanImage(
        id=1,
        scan_id=scan.id,
        panel="front",
        sha256="sha256_sugar_front",
    )

    fp_v1 = compute_assessment_fingerprint(scan, [s_img])

    scan.rule_pack_version = "2026.10.v1"
    fp_v2 = compute_assessment_fingerprint(scan, [s_img])

    assert fp_v1 != fp_v2


def test_original_evidence_remains_immutable_on_derivation(env):
    """Requirement: Original evidence file is immutable and never overwritten by recapture or analysis derivation."""
    p_orig, raw_orig = _make_temp_image_file("Authoritative Original Evidence Bytes", "orig_imm_")
    orig_stat_before = os.stat(p_orig)

    # Derive analysis image
    analysis_path, w, h = prepare_derived_analysis_image(p_orig)

    orig_stat_after = os.stat(p_orig)
    with open(p_orig, "rb") as f:
        bytes_after = f.read()

    assert bytes_after == raw_orig
    assert orig_stat_before.st_size == orig_stat_after.st_size
    assert str(analysis_path) != str(p_orig)
    assert os.path.exists(analysis_path)


def test_concurrent_duplicate_requests_reuse_result_without_conflicts(env):
    """Requirement: Concurrent duplicate assessment requests for the same scan do not duplicate findings."""
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
        commodity_generic="Spices",
        brand_name="Everest",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_spices",
        engine_version="2.4.0",
        panel_height_mm=100.0,
    )
    db.add(scan)
    db.commit()

    img_p, img_b = _make_temp_image_file("Everest Garam Masala 100g", "spices_")
    s_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=img_p,
        byte_size=len(img_b),
        width_px=600,
        height_px=800,
        mime_type="image/jpeg",
        sha256="sha256_spices_front",
    )
    db.add(s_img)
    db.commit()

    mock_lines = [
        OcrLine(text="EVEREST GARAM MASALA", confidence=0.95, box=[[10, 10], [100, 10], [100, 30], [10, 30]], height_px=14.0),
        OcrLine(text="NET QTY 100 g", confidence=0.96, box=[[10, 40], [80, 40], [80, 60], [10, 60]], height_px=12.0),
        OcrLine(text="MRP Rs. 55.00", confidence=0.97, box=[[10, 70], [90, 70], [90, 90], [10, 90]], height_px=12.0),
    ]
    mock_ocr = OcrResult(
        lines=mock_lines,
        engine="mock_engine",
        mean_confidence=0.96,
        failure_reason=None,
        status=STATUS_SUCCESS,
    )

    results = []

    with patch("ocr_engine.run_ocr", return_value=mock_ocr):
        def _call_assess():
            r = client.post(f"/scans/{scan.id}/assess")
            results.append(r)

        t1 = threading.Thread(target=_call_assess)
        t2 = threading.Thread(target=_call_assess)

        t1.start()
        t2.start()
        t1.join()
        t2.join()

    assert len(results) == 2
    assert results[0].status_code == 200
    assert results[1].status_code == 200
    assert results[0].json()["overall_result"] == results[1].json()["overall_result"]

    # Verify findings count strictly matches single assessment
    db.refresh(scan)
    findings = db.query(Finding).filter(Finding.scan_id == scan.id).all()
    assert len(findings) == 19
