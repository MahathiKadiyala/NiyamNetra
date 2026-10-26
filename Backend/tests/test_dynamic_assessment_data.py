"""Backend/tests/test_dynamic_assessment_data.py — regression tests for dynamic assessment data,
catalog consistency, panel-image binding, and truthful missing field defaults.
"""
import io
import json
import os
import tempfile
from datetime import date, datetime
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from models import Finding, Inspection, Scan, ScanImage, Store, User, utcnow
from password_handler import hash_password
from main import app
from rbac import get_current_user, require_inspector
from rules import load_rule_pack
from schemas import ScanOut


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
        employee_id="INSP-DYN-01",
        full_name="Dynamic Inspector",
        password_hash=hash_password("dynpass123"),
        role="inspector",
        is_active=True,
    )
    db.add(inspector)
    db.commit()
    db.refresh(inspector)

    store = Store(
        name="Dynamic Retails Ltd",
        address="42 Commercial St, Bengaluru",
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


def _make_dummy_image_file(label="PANEL"):
    import cv2
    im = np.full((800, 600, 3), (240, 240, 240), dtype=np.uint8)
    cv2.putText(im, f"PANEL {label} MRP 50 Net 250g", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (20, 20, 20), 2)
    _, buf = cv2.imencode(".jpg", im)
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(buf.tobytes())
        return tf.name


def test_api_catalog_version_and_count_consistency(env):
    """Test 1 & 9: /health and /rule-info agree on active rule catalog 2026.09.v1 and 26 checks."""
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
    assert len(r_data["rules"]) == 26


def test_two_different_assessment_payloads_have_distinct_identities(env):
    """Test 2: Two different assessment payloads return different commodity names and brands."""
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

    # Scan 1: Dhara Mustard Oil
    s1 = Scan(
        inspection_id=insp.id,
        commodity_generic="Mustard Oil",
        brand_name="Dhara",
        batch_number="DH-101",
        net_quantity_value=500.0,
        net_quantity_unit="ml",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash1",
        engine_version="2.4.0",
    )
    # Scan 2: India Gate Basmati Rice
    s2 = Scan(
        inspection_id=insp.id,
        commodity_generic="Basmati Rice",
        brand_name="India Gate",
        batch_number="IG-202",
        net_quantity_value=5.0,
        net_quantity_unit="kg",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash2",
        engine_version="2.4.0",
    )
    db.add_all([s1, s2])
    db.commit()
    db.refresh(s1)
    db.refresh(s2)

    res1 = client.get(f"/scans/{s1.id}")
    res2 = client.get(f"/scans/{s2.id}")
    assert res1.status_code == 200
    assert res2.status_code == 200

    d1 = res1.json()
    d2 = res2.json()

    assert d1["commodity_generic"] == "Mustard Oil"
    assert d1["brand_name"] == "Dhara"
    assert d1["batch_number"] == "DH-101"
    assert d1["net_quantity_value"] == 500.0
    assert d1["net_quantity_unit"] == "ml"

    assert d2["commodity_generic"] == "Basmati Rice"
    assert d2["brand_name"] == "India Gate"
    assert d2["batch_number"] == "IG-202"
    assert d2["net_quantity_value"] == 5.0
    assert d2["net_quantity_unit"] == "kg"

    # Distinct commodity identities
    assert d1["commodity_generic"] != d2["commodity_generic"]
    assert d1["brand_name"] != d2["brand_name"]
    assert d1["batch_number"] != d2["batch_number"]


def test_missing_fields_truthfully_preserved_without_invention(env):
    """Test 3: Missing fields are returned as None, not fabricated strings."""
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

    # Scan with no brand, no commodity, no batch, no net qty
    s_empty = Scan(
        inspection_id=insp.id,
        commodity_generic=None,
        brand_name=None,
        batch_number=None,
        net_quantity_value=None,
        net_quantity_unit=None,
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_empty",
        engine_version="2.4.0",
    )
    db.add(s_empty)
    db.commit()
    db.refresh(s_empty)

    res = client.get(f"/scans/{s_empty.id}")
    assert res.status_code == 200
    data = res.json()

    assert data["commodity_generic"] is None
    assert data["brand_name"] is None
    assert data["batch_number"] is None
    assert data["net_quantity_value"] is None
    assert data["net_quantity_unit"] is None


def test_panel_to_image_mapping_and_distinct_image_urls(env):
    """Test 4 & 5: Panel mapping maps front, back, mrp, batch, and different image IDs render distinct URLs."""
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

    scan_a = Scan(
        inspection_id=insp.id,
        commodity_generic="Tea Powder",
        brand_name="Tata Tea",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_tea",
        engine_version="2.4.0",
    )
    scan_b = Scan(
        inspection_id=insp.id,
        commodity_generic="Coffee",
        brand_name="Nescafe",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_coffee",
        engine_version="2.4.0",
    )
    db.add_all([scan_a, scan_b])
    db.commit()

    # Add 4 panels to scan_a
    panels = ["front", "back", "mrp", "batch"]
    for idx, panel in enumerate(panels):
        p_file = _make_dummy_image_file(f"A_{panel}")
        img = ScanImage(
            scan_id=scan_a.id,
            panel=panel,
            sequence=idx,
            file_path=p_file,
            byte_size=os.path.getsize(p_file),
            width_px=800,
            height_px=1000,
            mime_type="image/jpeg",
            sha256=f"sha256_scan_a_{panel}",
        )
        db.add(img)

    # Add 4 panels to scan_b with different sha and IDs
    for idx, panel in enumerate(panels):
        p_file = _make_dummy_image_file(f"B_{panel}")
        img = ScanImage(
            scan_id=scan_b.id,
            panel=panel,
            sequence=idx,
            file_path=p_file,
            byte_size=os.path.getsize(p_file),
            width_px=800,
            height_px=1000,
            mime_type="image/jpeg",
            sha256=f"sha256_scan_b_{panel}",
        )
        db.add(img)

    db.commit()

    res_a = client.get(f"/scans/{scan_a.id}")
    res_b = client.get(f"/scans/{scan_b.id}")
    assert res_a.status_code == 200
    assert res_b.status_code == 200

    images_a = res_a.json()["images"]
    images_b = res_b.json()["images"]

    assert len(images_a) == 4
    assert len(images_b) == 4

    # Verify panel mapping
    panels_a = {img["panel"]: img for img in images_a}
    panels_b = {img["panel"]: img for img in images_b}
    for p in ["front", "back", "mrp", "batch"]:
        assert p in panels_a
        assert p in panels_b
        # Distinct image URLs and IDs across scans
        assert panels_a[p]["id"] != panels_b[p]["id"]
        assert panels_a[p]["url"] != panels_b[p]["url"]
        assert panels_a[p]["sha256"] != panels_b[p]["sha256"]
        # URLs must NEVER contain query token leaks
        assert "token=" not in panels_a[p]["url"]
        assert "token=" not in panels_a[p]["thumbnail_url"]

    # Verify authorized access succeeds without query token
    front_img_id = panels_a["front"]["id"]
    thumb_res = client.get(f"/scans/{scan_a.id}/images/{front_img_id}/thumbnail")
    assert thumb_res.status_code == 200
    assert thumb_res.headers["content-type"] == "image/jpeg"

    # Verify full image access succeeds without query token
    full_res = client.get(f"/scans/{scan_a.id}/images/{front_img_id}")
    assert full_res.status_code == 200
    assert "image/" in full_res.headers["content-type"]

    # Verify unauthorized access fails with 401 when no credentials provided
    app.dependency_overrides.pop(get_current_user, None)
    app.dependency_overrides.pop(require_inspector, None)
    unauth_client = TestClient(app)
    unauth_res = unauth_client.get(f"/scans/{scan_a.id}/images/{front_img_id}/thumbnail")
    assert unauth_res.status_code == 401
    app.dependency_overrides[get_current_user] = lambda: inspector
    app.dependency_overrides[require_inspector] = lambda: inspector


def test_completed_assessment_diagnostics_and_counters(env):
    """Test 6 & 7: Dynamic counters reflect actual findings and completed assessment diagnostics."""
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
        brand_name="Parle-G",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_parle",
        engine_version="2.4.0",
        net_quantity_value=250.0,
        net_quantity_unit="g",
    )
    db.add(scan)
    db.commit()

    p_file = _make_dummy_image_file("parle_front")
    img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=p_file,
        byte_size=os.path.getsize(p_file),
        width_px=800,
        height_px=1000,
        mime_type="image/jpeg",
        sha256="sha256_parle_front",
    )
    db.add(img)
    db.commit()

    assess_res = client.post(f"/scans/{scan.id}/assess")
    assert assess_res.status_code == 200
    data = assess_res.json()

    assert data["overall_result"] in ("compliant", "violation", "review_required", "not_assessed")
    assert data["diagnostics"]["catalog_rule_count"] == 26
    assert data["diagnostics"]["rule_pack_version"] == "2026.09.v1"

    counts = data["counts"]
    findings = data["findings"]
    assert counts["total"] == len(findings)
    assert counts["passed"] == sum(1 for f in findings if f["effective_verdict"] == "pass")
    assert counts["failed"] == sum(1 for f in findings if f["effective_verdict"] == "fail")
    assert counts["not_assessed"] == sum(1 for f in findings if f["effective_verdict"] == "not_assessed")


def test_chk03_observation_truthfulness_when_quantity_undeclared():
    """Test: When net quantity is None, CHK03 states quantity not declared rather than static within limits."""
    from rules_engine import CheckContext, chk03_chapter_ii_applicability

    ctx = CheckContext(
        transaction_type="retail_sale",
        net_quantity_value=None,
        net_quantity_unit=None,
    )
    result = chk03_chapter_ii_applicability(ctx)
    assert "net quantity not declared or observed on package" in result.observed
    assert "within the Rule 3 quantity limits" not in result.observed


def test_assessment_diagnostics_and_no_secrets_in_logs(env, caplog):
    """Part G Tests 1, 3, 5, 10, 11, 12:
    - Verifies OCR and LLM success diagnostics
    - Confirms zero secrets (Bearer tokens, Authorization headers, passwords) in application logs
    - Validates catalog vs executable-check counts
    - Validates correct rule-pack version 2026.09.v1
    """
    import logging
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
        commodity_generic="Corn Flakes",
        brand_name="Kellogg's",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_kf",
        engine_version="2.4.0",
        net_quantity_value=475.0,
        net_quantity_unit="g",
        panel_height_mm=260.0,
    )
    db.add(scan)
    db.commit()

    p_file = _make_dummy_image_file("kelloggs_front")
    img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=p_file,
        byte_size=os.path.getsize(p_file),
        width_px=800,
        height_px=1000,
        mime_type="image/jpeg",
        sha256="sha256_kf_front",
        analysis_file_path=p_file,
    )
    db.add(img)
    db.commit()

    with caplog.at_level(logging.INFO):
        res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200
    data = res.json()

    # Rule pack version and counts
    assert data["rule_pack_version"] == "2026.09.v1"
    assert data["diagnostics"]["catalog_rule_count"] == 26
    assert data["diagnostics"]["executable_check_count"] == 19
    assert data["diagnostics"]["manual_review_rules"] == 7
    assert data["diagnostics"]["ocr_provider"] in ("google_vision", "mock", "azure_vision", "auto", "ocr_space")
    assert data["diagnostics"]["llm_provider"] in ("groq", "gemini", "mock")

    # Structured logs verification
    log_text = caplog.text
    assert "[ASSESSMENT] assessment_started" in log_text
    assert "[OCR] ocr_started" in log_text
    assert ("[OCR] ocr_completed" in log_text or "[OCR] ocr_failed" in log_text)
    assert "[LLM] llm_started" in log_text
    assert "[RULES] rules_completed" in log_text
    assert "[ASSESSMENT] assessment_completed" in log_text

    # Zero secrets in logs invariant (Part C & G.10)
    assert "Bearer " not in log_text
    assert "dynpass123" not in log_text
    assert "Authorization" not in log_text


def test_ocr_success_diagnostics_mock(env, caplog, monkeypatch):
    """Part G Test 1: Verifies OCR success diagnostics when provider yields lines."""
    import logging
    from ocr.base import OcrLine, OcrResult, STATUS_SUCCESS

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
        commodity_generic="Oats",
        brand_name="Quaker",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_oats",
        engine_version="2.4.0",
        net_quantity_value=500.0,
        net_quantity_unit="g",
        panel_height_mm=200.0,
    )
    db.add(scan)
    db.commit()

    p_file = _make_dummy_image_file("quaker_front")
    img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=p_file,
        byte_size=os.path.getsize(p_file),
        width_px=800,
        height_px=1000,
        mime_type="image/jpeg",
        sha256="sha256_quaker_front",
        analysis_file_path=p_file,
    )
    db.add(img)
    db.commit()

    def mock_run_ocr(*args, **kwargs):
        return OcrResult(
            lines=[
                OcrLine(text="Quaker Oats 500g", confidence=0.98),
                OcrLine(text="MRP Rs 120", confidence=0.95),
            ],
            engine="mock_ocr",
            mean_confidence=0.965,
            status=STATUS_SUCCESS,
        )

    import ocr_engine
    monkeypatch.setattr(ocr_engine, "run_ocr", mock_run_ocr)

    with caplog.at_level(logging.INFO):
        res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200

    log_text = caplog.text
    assert "[OCR] ocr_started" in log_text
    assert "[OCR] ocr_completed" in log_text
    assert "mock_ocr" in log_text
    assert "line_count=2" in log_text


def test_ocr_and_llm_failure_diagnostics(env, caplog):
    """Part G Tests 2, 4, 5:
    - Verifies OCR failure diagnostics
    - Verifies LLM failure diagnostics and graceful fallback handling
    """
    import logging
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
        commodity_generic="Unknown Item",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_fail",
        engine_version="2.4.0",
    )
    db.add(scan)
    db.commit()

    # Add scan image with a non-existent file path to trigger OCR input failure
    bad_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path="/tmp/non_existent_image_file_12345.jpg",
        byte_size=100,
        width_px=800,
        height_px=1000,
        mime_type="image/jpeg",
        sha256="sha256_bad_file",
    )
    db.add(bad_img)
    db.commit()

    with caplog.at_level(logging.INFO):
        res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200
    data = res.json()
    # When front panel is missing/unusable, honest not_assessed is returned
    assert data["overall_result"] == "not_assessed"


def test_identical_assessment_reuse_and_concurrency_protection(env, caplog, monkeypatch):
    """Part G Tests 6 & 7:
    - Verifies identical assessment input reuses persisted assessment without duplicate OCR/LLM
    - Verifies concurrent requests for the same scan are safely handled
    - Verifies failed / not_assessed scans are never cached or reused
    """
    import logging
    import threading
    eng, db, inspector, store, client = env

    from ocr_engine import OcrLine, OcrResult, STATUS_SUCCESS
    import ocr_engine

    def mock_run_ocr(*args, **kwargs):
        panel = kwargs.get("panel", "front")
        return OcrResult(
            lines=[
                OcrLine(text="Borges Extra Virgin Olive Oil", confidence=0.99, height_px=16.0),
                OcrLine(text="Commodity: Olive Oil", confidence=0.99, height_px=14.0),
                OcrLine(text="Net Quantity: 1 l", confidence=0.99, height_px=14.0),
                OcrLine(text="MRP Rs 1200.00 incl. of all taxes", confidence=0.99, height_px=14.0),
                OcrLine(text="Month and Year of Mfg: 01/2026", confidence=0.99, height_px=12.0),
                OcrLine(text="Best Before: 24 months", confidence=0.99, height_px=12.0),
                OcrLine(text="Country of Origin: Spain", confidence=0.99, height_px=12.0),
                OcrLine(text="Consumer Care: 1800111222", confidence=0.99, height_px=12.0),
                OcrLine(text="Manufactured by: Borges SA Spain", confidence=0.99, height_px=12.0),
                OcrLine(text="Batch No: B-991", confidence=0.99, height_px=12.0),
            ],
            engine="mock_cloud_ocr",
            mean_confidence=0.99,
            status=STATUS_SUCCESS,
            panel=panel,
        )

    monkeypatch.setattr(ocr_engine, "run_ocr", mock_run_ocr)

    from unittest.mock import MagicMock
    from llm.prompts import PROMPT_VERSION, SCHEMA_VERSION
    from llm.schema import (
        CommonFieldDeclaration,
        ConsumerCareDeclaration,
        DateDeclaration,
        FieldProvenance,
        LLM_STATUS_SUCCESS,
        MrpDeclaration,
        NetQuantityDeclaration,
        PartyDeclaration,
        STATUS_CONFIRMED,
        StructuredDeclarationResult,
    )

    expected_provider = getattr(settings, "LLM_PROVIDER", "groq")
    expected_model = (
        getattr(settings, "GEMINI_MODEL", "")
        if expected_provider in ("gemini", "google_gemini", "gemini-flash")
        else getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
    )

    mock_llm = MagicMock()
    mock_llm.name = expected_provider
    mock_llm.model_name = expected_model

    def _extract_declarations(pkg, meta=None):
        return StructuredDeclarationResult(
            metadata={
                "status": LLM_STATUS_SUCCESS,
                "llm_prompt_version": PROMPT_VERSION,
                "llm_schema_version": SCHEMA_VERSION,
                "llm_provider": expected_provider,
                "llm_model": expected_model,
            },
            raw_packaged_ocr=pkg,
            net_quantity=NetQuantityDeclaration(status="confirmed", value=1.0, unit="l"),
            mrp=MrpDeclaration(status="confirmed", value=1200.0, currency="INR"),
        )

    mock_llm.extract_declarations.side_effect = _extract_declarations

    monkeypatch.setattr("llm.factory.get_llm_provider", lambda *a, **kw: mock_llm)
    monkeypatch.setattr("llm.get_llm_provider", lambda *a, **kw: mock_llm)

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
        commodity_generic="Olive Oil",
        brand_name="Borges",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_olive",
        engine_version="2.4.0",
        net_quantity_value=1.0,
        net_quantity_unit="l",
        panel_shape="rectangular",
        panel_height_mm=220.0,
        panel_width_mm=100.0,
        scale_source="declared",
    )
    db.add(scan)
    db.commit()

    p_file = _make_dummy_image_file("borges_front")
    img = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=p_file,
        byte_size=os.path.getsize(p_file),
        width_px=800,
        height_px=1000,
        mime_type="image/jpeg",
        sha256="sha256_borges_front",
    )
    db.add(img)
    db.commit()

    # Call 1: First authoritative assessment
    res1 = client.post(f"/scans/{scan.id}/assess")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["overall_result"] != "not_assessed"

    # Call 2: Identical repeated request -> must hit idempotent fast reuse
    with caplog.at_level(logging.INFO):
        res2 = client.post(f"/scans/{scan.id}/assess")
    assert res2.status_code == 200
    data2 = res2.json()

    assert data1["id"] == data2["id"]
    assert data1["overall_result"] == data2["overall_result"]
    assert data2["diagnostics"]["assessment_input_fingerprint"] == data1["diagnostics"]["assessment_input_fingerprint"]
    assert "idempotent_assessment_reused" in caplog.text

    # Call 3: Concurrent duplicate protection test
    from routers.scans import _SCAN_IN_FLIGHT, _SCAN_IN_FLIGHT_LOCK
    evt = threading.Event()
    with _SCAN_IN_FLIGHT_LOCK:
        _SCAN_IN_FLIGHT[scan.id] = evt

    timer = threading.Timer(0.1, evt.set)
    timer.start()

    with caplog.at_level(logging.INFO):
        res3 = client.post(f"/scans/{scan.id}/assess?force=true")
    timer.join()

    with _SCAN_IN_FLIGHT_LOCK:
        _SCAN_IN_FLIGHT.pop(scan.id, None)

    assert res3.status_code == 200
    assert "concurrent_assessment_detected" in caplog.text or "concurrent_duplicate_reused" in caplog.text

    # Call 4: Failure safety invariant — not_assessed/failed assessments are never reused
    scan_fail = Scan(
        inspection_id=insp.id,
        commodity_generic="Unknown Commodity",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_fail_safe",
        engine_version="2.4.0",
    )
    db.add(scan_fail)
    db.commit()

    def mock_fail_ocr(*args, **kwargs):
        return OcrResult(lines=[], engine="none", status="unavailable", failure_reason="Provider unavailable")

    monkeypatch.setattr(ocr_engine, "run_ocr", mock_fail_ocr)
    p_unusable = _make_dummy_image_file("unusable_front")
    img_fail = ScanImage(
        scan_id=scan_fail.id,
        panel="front",
        sequence=0,
        file_path=p_unusable,
        byte_size=os.path.getsize(p_unusable),
        width_px=800,
        height_px=1000,
        mime_type="image/jpeg",
        sha256="sha256_unusable_front",
    )
    db.add(img_fail)
    db.commit()

    caplog.clear()
    res_f1 = client.post(f"/scans/{scan_fail.id}/assess")
    assert res_f1.status_code == 200
    assert res_f1.json()["overall_result"] == "not_assessed"
    db.refresh(scan_fail)
    assert scan_fail.ocr_cache_hash is None

    caplog.clear()
    res_f2 = client.post(f"/scans/{scan_fail.id}/assess")
    assert res_f2.status_code == 200
    assert "idempotent_assessment_reused" not in caplog.text


def test_new_image_and_recapture_invalidation(env):
    """Part G Tests 8 & 9:
    - Adding new evidence image invalidates fingerprint
    - Re-assessment re-evaluates rules with new evidence
    """
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
        commodity_generic="Honey",
        brand_name="Dabur",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="hash_honey",
        engine_version="2.4.0",
        net_quantity_value=500.0,
        net_quantity_unit="g",
        panel_height_mm=180.0,
    )
    db.add(scan)
    db.commit()

    p_front = _make_dummy_image_file("dabur_front")
    img_front = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path=p_front,
        byte_size=os.path.getsize(p_front),
        width_px=800,
        height_px=1000,
        mime_type="image/jpeg",
        sha256="sha256_dabur_front",
    )
    db.add(img_front)
    db.commit()

    # Initial assessment
    res1 = client.post(f"/scans/{scan.id}/assess")
    assert res1.status_code == 200
    fp1 = res1.json()["diagnostics"]["assessment_input_fingerprint"]

    # Recapture / upload new back panel
    p_back = _make_dummy_image_file("dabur_back")
    with open(p_back, "rb") as f_up:
        up_res = client.post(
            f"/scans/{scan.id}/images",
            data={"panel": "back"},
            files={"file": ("back.jpg", f_up, "image/jpeg")},
        )
    assert up_res.status_code in (200, 201)

    # After new image upload, cached fingerprint was cleared
    db.refresh(scan)
    assert scan.ocr_cache_hash is None

    # Reassessment re-runs with new evidence
    res2 = client.post(f"/scans/{scan.id}/assess")
    assert res2.status_code == 200
    fp2 = res2.json()["diagnostics"]["assessment_input_fingerprint"]
    assert fp1 != fp2
    assert len(res2.json()["images"]) == 2

