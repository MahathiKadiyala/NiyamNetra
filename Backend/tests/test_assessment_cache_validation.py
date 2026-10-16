"""Backend/tests/test_assessment_cache_validation.py — Module 7.2: Validate Assessment Cache Before Fast-Path Reuse.

Covers:
1. Valid cache reuse for unchanged evidence and matching fingerprints.
2. Invalidation upon image content or panel assignment changes.
3. OCR text/input change invalidates LLM extraction cache.
4. Model, provider, prompt, or schema version changes invalidate affected extraction results.
5. Rule-pack change invalidates legal assessment without unnecessarily re-running OCR or LLM.
6. Failed OCR or LLM operations never become reusable successful cache entries.
7. Evidence arriving during in-flight assessment prevents stale results from being persisted.
8. Warm reassessment retains the fast path without duplicating external calls.
"""
import io
import os
import time
from datetime import date
from unittest.mock import MagicMock, patch

os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import cv2
import numpy as np
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from main import app
from models import Finding, Inspection, Scan, ScanImage, Store, User
from ocr.base import OcrLine, OcrResult, STATUS_PROVIDER_ERROR, STATUS_SUCCESS
from password_handler import hash_password
from rbac import get_current_user, require_inspector
from routers.scans import (
    _ASSESSMENT_FINGERPRINTS,
    compute_assessment_fingerprint,
    _assess_inner,
)
from llm.factory import (
    _LLM_MEMORY_CACHE,
    compute_llm_cache_hash,
    structure_inspection_ocr,
)
from llm.prompts import PROMPT_VERSION, SCHEMA_VERSION, package_ocr_for_llm
from llm.schema import (
    CommonFieldDeclaration,
    LLM_STATUS_FAILED,
    LLM_STATUS_SUCCESS,
    MrpDeclaration,
    NetQuantityDeclaration,
    StructuredDeclarationResult,
)


def _make_test_image(text="TEST BRAND RICE NET QTY 1 kg MRP Rs 100.00") -> bytes:
    img = Image.new("RGB", (800, 600), color=(245, 245, 240))
    draw = ImageDraw.Draw(img)
    draw.rectangle([20, 20, 780, 580], outline=(30, 30, 30), width=4)
    draw.text((40, 50), text, fill=(10, 10, 10))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _mock_success_llm_provider():
    mock_p = MagicMock()
    mock_p.name = "groq"
    mock_p.model_name = "llama-3.3-70b-versatile"

    def _extract(pkg, meta=None):
        return StructuredDeclarationResult(
            metadata={
                "status": LLM_STATUS_SUCCESS,
                "llm_prompt_version": PROMPT_VERSION,
                "llm_schema_version": SCHEMA_VERSION,
                "llm_provider": "groq",
                "llm_model": "llama-3.3-70b-versatile",
            },
            raw_packaged_ocr=pkg,
            net_quantity=NetQuantityDeclaration(status="confirmed", value=1.0, unit="kg"),
            mrp=MrpDeclaration(status="confirmed", value=120.0, currency="INR"),
        )

    mock_p.extract_declarations.side_effect = _extract
    return mock_p


@pytest.fixture
def cache_env():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng)
    db = Session()

    inspector = User(
        employee_id="INSP-CACHE-01",
        full_name="Cache Validator",
        password_hash=hash_password("testpass123"),
        role="inspector",
        is_active=True,
    )
    db.add(inspector)
    db.commit()
    db.refresh(inspector)

    store = Store(
        name="Validation Store",
        address="100 Mission Road, Bengaluru",
        store_type="retail",
    )
    db.add(store)
    db.commit()
    db.refresh(store)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: inspector
    app.dependency_overrides[require_inspector] = lambda: inspector

    client = TestClient(app)
    _ASSESSMENT_FINGERPRINTS.clear()
    _LLM_MEMORY_CACHE.clear()

    mock_provider = _mock_success_llm_provider()
    with patch("llm.factory.get_llm_provider", return_value=mock_provider):
        yield client, db, inspector, store

    app.dependency_overrides.clear()
    _ASSESSMENT_FINGERPRINTS.clear()
    _LLM_MEMORY_CACHE.clear()
    db.close()
    eng.dispose()


def _mock_success_ocr(text="ROYAL RICE NET QTY 1 kg MRP Rs 120.00"):
    return OcrResult(
        lines=[
            OcrLine(text="ROYAL FEAST BASMATI RICE", confidence=0.98, box=[[10.0, 10.0], [200.0, 10.0], [200.0, 30.0], [10.0, 30.0]]),
            OcrLine(text="NET QTY: 1 kg", confidence=0.99, box=[[10.0, 40.0], [150.0, 40.0], [150.0, 60.0], [10.0, 60.0]]),
            OcrLine(text="MRP Rs. 120.00 (INCL. OF ALL TAXES)", confidence=0.97, box=[[10.0, 70.0], [300.0, 70.0], [300.0, 90.0], [10.0, 90.0]]),
            OcrLine(text="MFG: 01/2026 BATCH: B-101", confidence=0.95, box=[[10.0, 100.0], [250.0, 100.0], [250.0, 120.0], [10.0, 120.0]]),
            OcrLine(text="MFR: ROYAL FOODS PVT LTD", confidence=0.96, box=[[10.0, 130.0], [280.0, 130.0], [280.0, 150.0], [10.0, 150.0]]),
            OcrLine(text="CONSUMER CARE: care@royal.test 1800-111-2222", confidence=0.95, box=[[10.0, 160.0], [350.0, 160.0], [350.0, 180.0], [10.0, 180.0]]),
            OcrLine(text="COUNTRY OF ORIGIN: INDIA", confidence=0.98, box=[[10.0, 190.0], [220.0, 190.0], [220.0, 210.0], [10.0, 210.0]]),
        ],
        engine="google_vision",
        provider="google_vision",
        status=STATUS_SUCCESS,
        mean_confidence=0.97,
    )


def test_unchanged_evidence_and_matching_fingerprints_valid_cache_hit(cache_env):
    """Scenario 1: Unchanged evidence and matching fingerprints produce a fast valid cache hit."""
    client, db, inspector, store = cache_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]

    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Basmati Rice",
            "brand_name": "Royal Feast",
            "geometry": {"panel_shape": "rectangular", "panel_height_mm": 120.0, "panel_width_mm": 80.0, "scale_source": "declared"},
        },
    )
    scan_id = r_scan.json()["id"]
    img_bytes = _make_test_image()
    client.post(f"/scans/{scan_id}/images", data={"panel": "front"}, files={"file": ("front.jpg", img_bytes, "image/jpeg")})

    mock_ocr = _mock_success_ocr()
    with patch("ocr_engine.run_ocr", return_value=mock_ocr) as mock_ocr_call:
        t0 = time.perf_counter()
        r1 = client.post(f"/scans/{scan_id}/assess")
        dur_cold = (time.perf_counter() - t0) * 1000
        assert r1.status_code == 200
        assert mock_ocr_call.call_count >= 1

        # Second call: must hit fast-path cache without calling OCR
        mock_ocr_call.reset_mock()
        t1 = time.perf_counter()
        r2 = client.post(f"/scans/{scan_id}/assess")
        dur_warm = (time.perf_counter() - t1) * 1000
        assert r2.status_code == 200
        assert dur_warm < dur_cold
        mock_ocr_call.assert_not_called()
        assert r1.json()["overall_result"] == r2.json()["overall_result"]
        assert len(r1.json()["findings"]) == len(r2.json()["findings"])


def test_changed_image_or_panel_assignment_invalidates_cache(cache_env):
    """Scenario 2: A changed image or panel assignment invalidates the extraction and assessment cache."""
    client, db, inspector, store = cache_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]

    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Flour",
            "brand_name": "Aashirvaad",
            "geometry": {"panel_shape": "rectangular", "panel_height_mm": 120.0, "panel_width_mm": 80.0, "scale_source": "declared"},
        },
    )
    scan_id = r_scan.json()["id"]
    img_bytes1 = _make_test_image("Aashirvaad Atta Front Panel 5kg")
    client.post(f"/scans/{scan_id}/images", data={"panel": "front"}, files={"file": ("front.jpg", img_bytes1, "image/jpeg")})

    with patch("ocr_engine.run_ocr", return_value=_mock_success_ocr()):
        r1 = client.post(f"/scans/{scan_id}/assess")
        assert r1.status_code == 200
        fp1 = r1.json()["diagnostics"]["assessment_input_fingerprint"]

    # Now upload a new back panel
    img_bytes2 = _make_test_image("Aashirvaad Atta Back Panel Details")
    client.post(f"/scans/{scan_id}/images", data={"panel": "back"}, files={"file": ("back.jpg", img_bytes2, "image/jpeg")})

    scan = db.get(Scan, scan_id)
    # Verification: cache must be invalidated
    assert scan.ocr_cache_hash is None
    assert scan.llm_cache_hash is None
    assert scan.id not in _ASSESSMENT_FINGERPRINTS
    assert scan.overall_result == "not_assessed"

    # Reassessment runs on updated evidence
    with patch("ocr_engine.run_ocr", return_value=_mock_success_ocr()):
        r2 = client.post(f"/scans/{scan_id}/assess")
        assert r2.status_code == 200
        fp2 = r2.json()["diagnostics"]["assessment_input_fingerprint"]
        assert fp1 != fp2


def test_ocr_input_change_invalidates_llm_cache():
    """Scenario 3: An OCR input change invalidates the relevant LLM extraction cache."""
    ocr1 = OcrResult(
        lines=[OcrLine(text="NET QTY 500 g", confidence=0.99)],
        panel="front",
        image_id=1,
        engine="google_vision",
    )
    ocr2 = OcrResult(
        lines=[OcrLine(text="NET QTY 1 kg", confidence=0.99)],
        panel="front",
        image_id=1,
        engine="google_vision",
    )

    pkg1 = package_ocr_for_llm(ocr1)
    pkg2 = package_ocr_for_llm(ocr2)
    hash1 = compute_llm_cache_hash(pkg1, "groq", "llama-3.3-70b-versatile")
    hash2 = compute_llm_cache_hash(pkg2, "groq", "llama-3.3-70b-versatile")

    assert pkg1 != pkg2
    assert hash1 != hash2


def test_model_or_prompt_schema_version_change_invalidates_extraction():
    """Scenario 4: Model/provider or prompt/schema version change invalidates extraction results."""
    ocr = OcrResult(
        lines=[OcrLine(text="MRP Rs 50.00", confidence=0.98)],
        panel="front",
        image_id=1,
        engine="google_vision",
    )
    pkg = package_ocr_for_llm(ocr)

    h_groq = compute_llm_cache_hash(pkg, "groq", "llama-3.3-70b-versatile")
    h_gemini = compute_llm_cache_hash(pkg, "gemini", "gemini-2.0-flash")
    assert h_groq != h_gemini

    # Prompt version mismatch must reject persisted rehydration
    fake_persisted = {
        "metadata": {
            "status": LLM_STATUS_SUCCESS,
            "llm_prompt_version": "0.9.0-obsolete",
            "llm_schema_version": SCHEMA_VERSION,
            "llm_provider": "groq",
            "llm_model": "llama-3.3-70b-versatile",
        },
        "raw_packaged_ocr": pkg,
    }
    meta = {
        "persisted_llm_structured_data": fake_persisted,
        "persisted_llm_cache_hash": h_groq,
    }

    with patch("llm.factory.get_llm_provider") as mock_gp:
        mock_p = MagicMock()
        mock_p.name = "groq"
        mock_p.model_name = "llama-3.3-70b-versatile"
        mock_p.extract_declarations.return_value = StructuredDeclarationResult(
            metadata={"status": LLM_STATUS_SUCCESS, "llm_provider": "groq", "llm_model": "llama-3.3-70b-versatile"},
        )
        mock_gp.return_value = mock_p

        res = structure_inspection_ocr(ocr, evidence_meta=meta)
        # Obsolete prompt version in persisted data must not be rehydrated; must call provider
        mock_p.extract_declarations.assert_called_once()


def test_rule_pack_change_invalidates_assessment_preserves_ocr_llm_cache(cache_env):
    """Scenario 5: Rule-pack change invalidates assessment without re-running OCR or LLM."""
    client, db, inspector, store = cache_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]

    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Oil",
            "brand_name": "Fortune",
            "geometry": {"panel_shape": "rectangular", "panel_height_mm": 120.0, "panel_width_mm": 80.0, "scale_source": "declared"},
        },
    )
    scan_id = r_scan.json()["id"]
    client.post(f"/scans/{scan_id}/images", data={"panel": "front"}, files={"file": ("front.jpg", _make_test_image(), "image/jpeg")})

    mock_ocr = _mock_success_ocr()
    with patch("ocr_engine.run_ocr", return_value=mock_ocr) as mock_ocr_call:
        r1 = client.post(f"/scans/{scan_id}/assess")
        assert r1.status_code == 200
        scan = db.get(Scan, scan_id)
        assert scan.ocr_cache_hash is not None
        assert scan.llm_cache_hash is not None

        # Simulate rule pack update to a new gazette version
        with patch.object(settings, "RULE_PACK_VERSION", "2026.11.v1"):
            mock_ocr_call.reset_mock()
            r2 = client.post(f"/scans/{scan_id}/assess")
            assert r2.status_code == 200
            # Rule pack change invalidated assessment cache, but OCR cache was preserved!
            mock_ocr_call.assert_not_called()
            assert r2.json()["rule_pack_version"] == "2026.11.v1"


def test_failed_ocr_and_llm_never_cached_as_reusable_success(cache_env):
    """Scenario 6: Failed OCR/LLM operations do not become reusable successful cache entries."""
    client, db, inspector, store = cache_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]

    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Sugar",
            "brand_name": "Madhur",
            "geometry": {"panel_shape": "rectangular", "panel_height_mm": 120.0, "panel_width_mm": 80.0, "scale_source": "declared"},
        },
    )
    scan_id = r_scan.json()["id"]
    client.post(f"/scans/{scan_id}/images", data={"panel": "front"}, files={"file": ("front.jpg", _make_test_image(), "image/jpeg")})

    # Provider outage
    failed_ocr = OcrResult(
        lines=[],
        engine="none",
        status=STATUS_PROVIDER_ERROR,
        failure_reason="Network Timeout",
    )
    with patch("ocr_engine.run_ocr", return_value=failed_ocr):
        r_fail = client.post(f"/scans/{scan_id}/assess")
        assert r_fail.status_code == 200
        scan = db.get(Scan, scan_id)
        assert scan.ocr_cache_hash is None
        assert scan.llm_cache_hash is None
        assert scan.overall_result == "not_assessed"
        assert scan.id not in _ASSESSMENT_FINGERPRINTS

    # Subsequent request successfully retries and is NOT blocked by stale failure
    with patch("ocr_engine.run_ocr", return_value=_mock_success_ocr()):
        r_ok = client.post(f"/scans/{scan_id}/assess")
        assert r_ok.status_code == 200
        db.refresh(scan)
        assert scan.overall_result != "not_assessed"
        assert scan.ocr_cache_hash is not None


def test_new_evidence_during_in_flight_assessment_prevents_stale_persist(cache_env):
    """Scenario 7: New evidence arriving during an older assessment prevents stale results from persisting."""
    client, db, inspector, store = cache_env
    insp = Inspection(user_id=inspector.id, store_id=store.id, status="draft", transaction_type="retail")
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Biscuits",
        brand_name="Parle",
        overall_result="not_assessed",
        rules_as_at=date(2026, 9, 25),
        catalog_hash="h1",
        engine_version="2.0.0",
        rule_pack_version="2026.09.v1",
    )
    db.add(scan)
    db.commit()

    raw1 = _make_test_image("Parle Front")
    img1 = ScanImage(
        scan_id=scan.id,
        panel="front",
        sequence=0,
        file_path="mock/front.jpg",
        byte_size=len(raw1),
        width_px=800,
        height_px=600,
        mime_type="image/jpeg",
        sha256="sha256_front_v1",
    )
    db.add(img1)
    db.commit()

    initial_fp = compute_assessment_fingerprint(scan, [img1])

    # Simulate mid-assessment modification: another thread uploads new evidence while assess is in flight
    with patch("routers.scans.build_context") as mock_ctx, patch("rules_engine.assess") as mock_rules:
        from rules_engine import CheckContext, ScanVerdict
        ctx = CheckContext(
            commodity_generic=scan.commodity_generic,
            panel_height_mm=100.0,
            panel_shape="rectangular",
        )
        mock_ctx.return_value = ctx

        def _mock_assess_side_effect(*args, **kwargs):
            # Concurrent upload happens while assessment rules are executing
            raw2 = _make_test_image("Parle Back")
            img2 = ScanImage(
                scan_id=scan.id,
                panel="back",
                sequence=1,
                file_path="mock/back.jpg",
                byte_size=len(raw2),
                width_px=800,
                height_px=600,
                mime_type="image/jpeg",
                sha256="sha256_back_v2",
            )
            db.add(img2)
            db.commit()
            return (
                [],
                ScanVerdict(overall_result="compliant", violation_limb=None, recommended_action=None, checks_total=19, checks_assessed=19, passed=19, failed=0, not_assessed=0),
                {"catalog_hash": "h1", "engine_version": "2.0.0", "rule_pack_version": "2026.09.v1"},
            )

        mock_rules.side_effect = _mock_assess_side_effect

        # _assess_inner must detect the evidence mismatch and raise 409 Conflict
        with pytest.raises(HTTPException) as exc_info:
            _assess_inner(scan, inspector, db, fingerprint=initial_fp)

        assert exc_info.value.status_code == 409
        assert "Evidence was modified while evaluation was in progress" in exc_info.value.detail
