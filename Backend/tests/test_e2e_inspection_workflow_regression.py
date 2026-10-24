"""Backend/tests/test_e2e_inspection_workflow_regression.py — Module 11: End-to-End Regression Tests.

Cross-module integration tests verifying the full inspector lifecycle:
Evidence capture -> upload -> OCR fallback -> extraction -> legal assessment ->
counting arithmetic -> submission/finalization -> review queue.
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from config import settings
from database import Base, get_db
from models import AuditLog, Finding, Inspection, Scan, ScanImage, Store, User
from main import app
from ocr.base import (
    BaseOCRProvider,
    OcrLine,
    OcrResult,
    OcrWord,
    STATUS_AUTH_ERROR,
    STATUS_BILLING_ERROR,
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from ocr.diagnostics import get_ocr_health_diagnostics, reset_diagnostic_records
from ocr.fallback import FallbackOCRProvider
from ocr.security import sanitize_sensitive_text
from password_handler import hash_password
from rbac import get_current_user, require_inspector
from routers.scans import (
    _ASSESSMENT_FINGERPRINTS,
    _SCAN_IN_FLIGHT,
    _SCAN_IN_FLIGHT_LOCK,
    compute_assessment_fingerprint,
)


class MockProviderDouble(BaseOCRProvider):
    def __init__(
        self,
        name: str,
        configured: bool = True,
        result: OcrResult | None = None,
        exc: Exception | None = None,
    ):
        self.name = name
        self._configured = configured
        self._result = result
        self._exc = exc
        self.call_count = 0

    def is_configured(self) -> bool:
        return self._configured

    def recognize(self, image_bytes, image_width=None, image_height=None, options=None):
        self.call_count += 1
        if self._exc:
            raise self._exc
        if self._result is not None:
            return self._result
        return OcrResult(engine=self.name, provider=self.name, status=STATUS_SUCCESS)


@pytest.fixture
def test_env():
    """Isolated database and test client environment for Module 11 integration tests."""
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    db = Session()

    reset_diagnostic_records()
    _ASSESSMENT_FINGERPRINTS.clear()
    with _SCAN_IN_FLIGHT_LOCK:
        _SCAN_IN_FLIGHT.clear()

    inspector = User(
        employee_id="INSP-E2E-11",
        full_name="E2E Officer Sharma",
        password_hash=hash_password("securePassword123!"),
        role="inspector",
        is_active=True,
    )
    db.add(inspector)
    db.commit()
    db.refresh(inspector)

    store = Store(
        name="Apex Supermarket",
        address="100 Brigade Road, Bengaluru",
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

    client = TestClient(app, raise_server_exceptions=False)
    try:
        yield {
            "engine": eng,
            "db": db,
            "inspector": inspector,
            "store": store,
            "client": client,
        }
    finally:
        app.dependency_overrides.clear()
        reset_diagnostic_records()
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


def _create_synthetic_panel_image(label="PACKAGING EVIDENCE"):
    im = np.full((800, 600, 3), (240, 240, 240), dtype=np.uint8)
    cv2.putText(im, label, (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (20, 20, 20), 2)
    _, buf = cv2.imencode(".jpg", im)
    raw = buf.tobytes()
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(raw)
        path = tf.name
    return path, raw


# ===========================================================================
# Section 4: A — Runtime OCR Fallback Through Actual Pipeline
# ===========================================================================
def test_e2e_ocr_fallback_through_actual_assessment_pipeline(test_env, monkeypatch):
    """Scenario A: Primary Google Vision returns 403; fallback OCR.space succeeds; pipeline evaluates derived findings."""
    client = test_env["client"]
    db = test_env["db"]
    store = test_env["store"]
    inspector = test_env["inspector"]

    # 1. Create Draft Inspection
    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 21),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()
    db.refresh(insp)

    # 2. Add Scan
    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Biscuits",
        brand_name="Parle-G",
        rules_as_at=date(2026, 9, 21),
        rule_pack_version="2026.09.v1",
        engine_version="2.0.0",
        catalog_hash="testhash_e2e",
        overall_result="not_assessed",
        net_quantity_value=250.0,
        net_quantity_unit="g",
        panel_height_mm=120.0,
        panel_width_mm=80.0,
        panel_shape="rectangular",
    )
    db.add(scan)
    db.commit()
    db.refresh(scan)

    # 3. Add Evidence Image
    img_path, img_raw = _create_synthetic_panel_image("PARLE-G GLUCOSE BISCUITS MRP 20 Net 250g")
    scan_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        file_path=img_path,
        sha256="sha256_parle_front",
        width_px=600,
        height_px=800,
        byte_size=len(img_raw),
        mime_type="image/jpeg",
    )
    db.add(scan_img)
    db.commit()

    # Configure controlled test doubles:
    # Primary Google Vision fails with HTTP 403 billing suspended (containing dummy key in message)
    p_google = MockProviderDouble(
        name="google_vision",
        configured=True,
        result=OcrResult(
            engine="google_vision",
            provider="google_vision",
            status=STATUS_BILLING_ERROR,
            failure_reason="Google Cloud Vision HTTP 403: Billing suspended for apikey=AIzaSySecretGoogleKey123456",
            duration_ms=85.0,
        ),
    )
    # Fallback OCR.space succeeds with genuine packaging text
    p_ocrspace = MockProviderDouble(
        name="ocr_space",
        configured=True,
        result=OcrResult(
            lines=[
                OcrLine(text="PARLE-G GLUCOSE BISCUITS", confidence=0.98, box=[[10, 10], [200, 10], [200, 40], [10, 40]]),
                OcrLine(text="Net Weight: 250 g", confidence=0.97, box=[[10, 50], [150, 50], [150, 70], [10, 70]]),
                OcrLine(text="MRP Rs. 20.00 (Incl. of all taxes)", confidence=0.99, box=[[10, 80], [220, 80], [220, 100], [10, 100]]),
                OcrLine(text="Mfg Date: 08/2026", confidence=0.95, box=[[10, 110], [120, 110], [120, 130], [10, 130]]),
                OcrLine(text="Manufactured by: Parle Products Pvt Ltd, Mumbai", confidence=0.96, box=[[10, 140], [280, 140], [280, 160], [10, 160]]),
            ],
            engine="ocr_space",
            provider="ocr_space",
            status=STATUS_SUCCESS,
            duration_ms=280.0,
        ),
    )

    fallback_provider = FallbackOCRProvider(candidates=[p_google, p_ocrspace])
    monkeypatch.setattr("ocr_engine.get_ocr_provider", lambda *args, **kwargs: fallback_provider)
    monkeypatch.setattr("ocr.factory.get_ocr_provider", lambda *args, **kwargs: fallback_provider)
    monkeypatch.setattr("ocr.get_ocr_provider", lambda *args, **kwargs: fallback_provider)

    # 4. Trigger Assessment through the actual endpoint
    res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200, res.text
    data = res.json()

    # 5. Verify findings derived from evidence
    assert data["id"] == scan.id
    assert data["checks_total"] == 19
    assert data["checks_assessed"] > 0
    assert len(data["findings"]) == 19

    # 6. Verify safe diagnostic trace and degraded readiness
    diag = get_ocr_health_diagnostics()
    assert diag["fallback_occurred"] is True
    assert diag["readiness"] == "degraded"
    assert diag["verification_status"] == "fallback_succeeded"

    # 7. Verify zero secret exposure
    resp_text = res.text
    assert "AIzaSySecretGoogleKey123456" not in resp_text
    assert "[REDACTED]" not in data["commodity_generic"]


# ===========================================================================
# Section 5: B — All Providers Fail Path & Legal Safety
# ===========================================================================
def test_e2e_all_providers_fail_and_legal_safety(test_env, monkeypatch):
    """Scenario B: All OCR providers fail; legal rules remain not_assessed without fabricating violations."""
    client = test_env["client"]
    db = test_env["db"]
    store = test_env["store"]
    inspector = test_env["inspector"]

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 21),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Corn Flakes",
        brand_name="Kellogg's",
        rules_as_at=date(2026, 9, 21),
        rule_pack_version="2026.09.v1",
        engine_version="2.0.0",
        catalog_hash="testhash_corn",
        overall_result="not_assessed",
    )
    db.add(scan)
    db.commit()

    img_path, img_raw = _create_synthetic_panel_image("KELLOGGS CORN FLAKES")
    scan_img = ScanImage(
        scan_id=scan.id,
        panel="front",
        file_path=img_path,
        sha256="sha256_corn_front",
        width_px=600,
        height_px=800,
        byte_size=len(img_raw),
        mime_type="image/jpeg",
    )
    db.add(scan_img)
    db.commit()

    # Simulate all providers failing (timeout on Google Vision, 500 on OCR.space)
    p1 = MockProviderDouble(
        name="google_vision",
        configured=True,
        result=OcrResult(engine="google_vision", provider="google_vision", status=STATUS_TIMEOUT, failure_reason="Read timed out"),
    )
    p2 = MockProviderDouble(
        name="ocr_space",
        configured=True,
        result=OcrResult(engine="ocr_space", provider="ocr_space", status=STATUS_PROVIDER_ERROR, failure_reason="Internal Server Error 500"),
    )

    # Ensure at least one provider has a test configuration key set so it evaluates as configured
    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", "K89999999999999")

    all_fail_provider = FallbackOCRProvider(candidates=[p1, p2])
    monkeypatch.setattr("ocr_engine.get_ocr_provider", lambda *args, **kwargs: all_fail_provider)
    monkeypatch.setattr("ocr.factory.get_ocr_provider", lambda *args, **kwargs: all_fail_provider)
    monkeypatch.setattr("ocr.get_ocr_provider", lambda *args, **kwargs: all_fail_provider)

    res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200
    data = res.json()

    # Invariant: OCR technical failure is NOT marked as compliant or a false violation
    assert data["overall_result"] == "not_assessed"

    # Diagnostics show all failed state
    diag = get_ocr_health_diagnostics()
    assert diag["readiness"] == "all_failed"
    assert diag["verification_status"] == "all_providers_failed"

    # Dependent mandatory declaration rules (e.g., MRP, Net Quantity) must NOT be false fails
    for finding in data["findings"]:
        if finding["check_id"] in ("CHK01", "CHK04", "CHK05", "CHK06"):
            assert finding["effective_verdict"] == "not_assessed", f"Check {finding['check_id']} must be not_assessed on OCR outage"
            assert "OCR" in (finding.get("reason") or ""), "Reason must honestly cite OCR or evidence unavailability"
        if finding["check_id"] == "CHK18":
            assert finding["effective_verdict"] == "not_assessed", "Penalties (CHK18) must not be generated from OCR outage"


# ===========================================================================
# Section 6: C — Assessment Counting Contract
# ===========================================================================
def test_e2e_assessment_counting_contract(test_env, monkeypatch):
    """Scenario C: Verify total_findings = pass + fail + na and checks_assessed = pass + fail."""
    client = test_env["client"]
    db = test_env["db"]
    store = test_env["store"]
    inspector = test_env["inspector"]

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 21),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Tea",
        brand_name="Red Label",
        rules_as_at=date(2026, 9, 21),
        rule_pack_version="2026.09.v1",
        engine_version="2.0.0",
        catalog_hash="testhash_tea",
        overall_result="not_assessed",
        panel_height_mm=100.0,
    )
    db.add(scan)
    db.commit()

    img_path, img_raw = _create_synthetic_panel_image("RED LABEL TEA MRP 100")
    db.add(ScanImage(scan_id=scan.id, panel="front", file_path=img_path, sha256="sha256_tea", width_px=600, height_px=800, byte_size=len(img_raw), mime_type="image/jpeg"))
    db.commit()

    mock_lines = [
        OcrLine(text="BROOKE BOND RED LABEL TEA", confidence=0.97),
        OcrLine(text="MRP Rs. 100.00", confidence=0.98),
    ]
    mock_ocr = OcrResult(lines=mock_lines, engine="mock", mean_confidence=0.975, status=STATUS_SUCCESS)
    monkeypatch.setattr("ocr_engine.run_ocr", lambda *args, **kwargs: mock_ocr)

    res = client.post(f"/scans/{scan.id}/assess")
    assert res.status_code == 200
    data = res.json()

    findings = data["findings"]
    pass_cnt = sum(1 for f in findings if f.get("effective_verdict") == "pass")
    fail_cnt = sum(1 for f in findings if f.get("effective_verdict") == "fail")
    na_cnt = sum(1 for f in findings if f.get("effective_verdict") == "not_assessed")

    # Contract arithmetic checks
    assert len(findings) == 19
    assert data["checks_total"] == 19
    assert pass_cnt + fail_cnt + na_cnt == 19
    assert data["checks_assessed"] == pass_cnt + fail_cnt
    if "counts" in data and data["counts"]:
        assert data["counts"]["total"] == 19
        assert data["counts"]["passed"] == pass_cnt
        assert data["counts"]["failed"] == fail_cnt
        assert data["counts"]["not_assessed"] == na_cnt


# ===========================================================================
# Section 7: D — Current Evidence, Cache Invalidation, and Idempotency
# ===========================================================================
def test_e2e_evidence_cache_invalidation_and_idempotency(test_env, monkeypatch):
    """Scenario D: Replacing evidence invalidates cache and re-assesses with new fingerprint."""
    client = test_env["client"]
    db = test_env["db"]
    store = test_env["store"]
    inspector = test_env["inspector"]

    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 21),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Soap",
        brand_name="Dove",
        rules_as_at=date(2026, 9, 21),
        rule_pack_version="2026.09.v1",
        engine_version="2.0.0",
        catalog_hash="testhash_soap",
        overall_result="not_assessed",
    )
    db.add(scan)
    db.commit()

    img1_path, img1_raw = _create_synthetic_panel_image("DOVE SOAP FRONT")
    db.add(ScanImage(scan_id=scan.id, panel="front", file_path=img1_path, sha256="sha256_soap_front_v1", width_px=600, height_px=800, byte_size=len(img1_raw), mime_type="image/jpeg"))
    db.commit()

    mock_ocr_1 = OcrResult(
        lines=[OcrLine(text="DOVE BEAUTY BAR", confidence=0.95)],
        engine="mock1",
        status=STATUS_SUCCESS,
    )
    monkeypatch.setattr("ocr_engine.run_ocr", lambda *args, **kwargs: mock_ocr_1)

    # 1. First authoritative assessment
    res1 = client.post(f"/scans/{scan.id}/assess")
    assert res1.status_code == 200
    fp1 = res1.json()["diagnostics"]["assessment_input_fingerprint"]

    # 2. Add second panel (back panel evidence)
    img2_path, img2_raw = _create_synthetic_panel_image("DOVE SOAP BACK INGREDIENTS")
    with open(img2_path, "rb") as f_up:
        up_resp = client.post(
            f"/scans/{scan.id}/images",
            data={"panel": "back"},
            files={"file": ("back.jpg", f_up, "image/jpeg")},
        )
    assert up_resp.status_code in (200, 201)

    # Cache must be cleared on evidence change
    db.refresh(scan)
    assert scan.ocr_cache_hash is None

    # 3. Second assessment evaluates with updated evidence
    mock_ocr_2 = OcrResult(
        lines=[
            OcrLine(text="DOVE BEAUTY BAR", confidence=0.95),
            OcrLine(text="Ingredients: Sodium Lauroyl Isethionate", confidence=0.96),
        ],
        engine="mock2",
        status=STATUS_SUCCESS,
    )
    monkeypatch.setattr("ocr_engine.run_ocr", lambda *args, **kwargs: mock_ocr_2)

    res2 = client.post(f"/scans/{scan.id}/assess")
    assert res2.status_code == 200
    fp2 = res2.json()["diagnostics"]["assessment_input_fingerprint"]

    assert fp1 != fp2, "New panel evidence must produce distinct assessment fingerprint"


# ===========================================================================
# Section 8 & 9: E & F — Inspection Submission & Actionable Review Queue
# ===========================================================================
def test_e2e_finalization_to_review_queue_lifecycle(test_env, monkeypatch):
    """Scenario E & F: Submitting an inspection moves findings to the review queue; drafts remain excluded."""
    client = test_env["client"]
    db = test_env["db"]
    store = test_env["store"]
    inspector = test_env["inspector"]

    # 1. Draft inspection should NOT appear in officer review queue
    insp = Inspection(
        user_id=inspector.id,
        store_id=store.id,
        inspection_date=date(2026, 9, 21),
        status="draft",
        transaction_type="retail",
    )
    db.add(insp)
    db.commit()

    scan = Scan(
        inspection_id=insp.id,
        commodity_generic="Chips",
        brand_name="Lays",
        rules_as_at=date(2026, 9, 21),
        rule_pack_version="2026.09.v1",
        engine_version="2.0.0",
        catalog_hash="testhash_chips",
        overall_result="not_assessed",
    )
    db.add(scan)
    db.commit()

    p_front, r_front = _create_synthetic_panel_image("LAYS CHIPS")
    db.add(ScanImage(scan_id=scan.id, panel="front", file_path=p_front, sha256="sha_chips_front", width_px=600, height_px=800, byte_size=len(r_front), mime_type="image/jpeg"))
    db.commit()

    # Confirm draft inspection is excluded from open review queue
    q_draft = client.get("/review/queue")
    assert q_draft.status_code == 200
    items_draft = q_draft.json().get("items", [])
    assert not any(it.get("inspection_id") == insp.id for it in items_draft)

    # 2. Pre-assess scan with a confirmed statutory violation (e.g. missing MRP / declaration)
    mock_ocr = OcrResult(
        lines=[
            OcrLine(text="LAYS POTATO CHIPS", confidence=0.98),
            # Missing mandatory MRP and manufacturer
        ],
        engine="mock_e2e",
        status=STATUS_SUCCESS,
    )
    monkeypatch.setattr("ocr_engine.run_ocr", lambda *args, **kwargs: mock_ocr)

    assess_resp = client.post(f"/scans/{scan.id}/assess")
    assert assess_resp.status_code == 200

    # 3. Submit and finalize inspection via authoritative route
    sub_resp = client.post(
        f"/inspections/{insp.id}/submit",
        json={"signature_status": "signed", "notes": "E2E finalization test submission"},
    )
    assert sub_resp.status_code == 200
    sub_data = sub_resp.json()
    assert sub_data["status"] == "submitted"
    assert "finalization_summary" in sub_data

    # 4. Actionable review queue now contains the finalized inspection review items
    q_sub = client.get("/review/queue?category=violations")
    assert q_sub.status_code == 200
    items_sub = q_sub.json().get("items", [])
    matching = [it for it in items_sub if it.get("inspection_id") == insp.id]
    assert len(matching) >= 1, "Submitted inspection findings must be actionable in review queue"

    # 5. Adjudication resolution transitions inspection out of open queue
    adj_resp = client.post(
        f"/review/inspections/{insp.id}/adjudicate",
        json={"action": "resolve", "reason": "Officer verified resolution and confirmed fine paid"},
    )
    assert adj_resp.status_code in (200, 201)

    # Item transitions out of OPEN review queue and into RESOLVED review queue
    q_open = client.get("/review/queue?status_filter=OPEN")
    assert q_open.status_code == 200
    assert not any(it.get("inspection_id") == insp.id for it in q_open.json().get("items", []))

    q_resolved = client.get("/review/queue?status_filter=RESOLVED")
    assert q_resolved.status_code == 200
    assert any(it.get("inspection_id") == insp.id for it in q_resolved.json().get("items", []))


# ===========================================================================
# Section 10: G — Security Across End-to-End Workflow
# ===========================================================================
def test_e2e_security_and_credential_protection(test_env, monkeypatch):
    """Scenario G: End-to-end security protections prevent credential leakage in diagnostics, logs, and /health."""
    client = test_env["client"]

    # Injected synthetic secrets
    monkeypatch.setattr(settings, "GOOGLE_VISION_API_KEY", "AIzaSyFakeGoogleTestKey999")
    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", "K89999999999999")
    monkeypatch.setattr(settings, "AZURE_VISION_KEY", "0123456789abcdef0123456789abcdef")

    # 1. Health endpoint exposes NO secrets
    h_resp = client.get("/health")
    assert h_resp.status_code == 200
    h_text = h_resp.text
    assert "AIzaSyFakeGoogleTestKey999" not in h_text
    assert "K89999999999999" not in h_text
    assert "0123456789abcdef0123456789abcdef" not in h_text

    # 2. Sanitizer successfully scrubs authorization headers and tokens
    dirty_err = "Request failed for Authorization: Bearer secret_jwt_token_456 with apikey=K89999999999999"
    clean_err = sanitize_sensitive_text(dirty_err)
    assert "secret_jwt_token_456" not in clean_err
    assert "K89999999999999" not in clean_err
    assert "[REDACTED]" in clean_err
