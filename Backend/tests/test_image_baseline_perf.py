"""tests/test_image_baseline_perf.py — Baseline benchmark tests for image capture, upload, and assessment.

Runs real measured image workloads to collect timing breakdowns and memory metrics for Commit 1 baseline.
"""
import io
import os
import time

os.environ.setdefault("ENV", "test")
os.environ.setdefault("JWT_SECRET", "test-secret-" + "0" * 40)
os.environ.setdefault("DATABASE_URL", "sqlite:///:memory:")

import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from database import Base, get_db
from main import app
from models import Finding, Inspection, Scan, ScanImage, Store, User
from password_handler import hash_password
from perf_baseline import get_latest_benchmarks, get_process_memory_mb
from rbac import get_current_user, require_inspector


def _create_synthetic_label_image(width=1600, height=1200, text="BASMATI RICE NET QTY 1 kg MRP Rs 120.00") -> bytes:
    """Create a synthetic RGB package label image with sharp text."""
    img = Image.new("RGB", (width, height), color=(245, 245, 240))
    draw = ImageDraw.Draw(img)
    # Draw label border / quad
    draw.rectangle([60, 60, width - 60, height - 60], outline=(40, 40, 40), width=6)
    # Draw simulated text lines
    y = 120
    for line in [
        "ROYAL FEAST PREMIUM RICE",
        text,
        "MFG DATE: 01/2026  BATCH: B-2026-X",
        "CONSUMER CARE: care@example.test",
        "ORIGIN: INDIA",
    ]:
        draw.text((100, y), line, fill=(20, 20, 20))
        y += 100
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _create_blurred_image(width=800, height=600) -> bytes:
    """Create a severely defocused image with low sharpness."""
    bgr = np.full((height, width, 3), 180, dtype=np.uint8)
    bgr = cv2.GaussianBlur(bgr, (51, 51), 0)
    _, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), 60])
    return buf.tobytes()


@pytest.fixture
def test_env():
    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    S = sessionmaker(bind=eng)
    db = S()

    inspector = User(
        employee_id="INS-BENCH-01",
        full_name="Benchmarking Inspector",
        password_hash=hash_password("password123"),
        role="inspector",
        is_active=True,
    )
    store = Store(name="Benchmark Store", city="Hyderabad")
    db.add_all([inspector, store])
    db.commit()
    db.refresh(inspector)
    db.refresh(store)

    app.dependency_overrides[get_db] = lambda: db
    app.dependency_overrides[get_current_user] = lambda: inspector
    app.dependency_overrides[require_inspector] = lambda: inspector

    client = TestClient(app)
    yield client, db, inspector, store

    app.dependency_overrides.clear()


def test_baseline_image_upload_and_benchmarks(test_env):
    """Benchmark the full upload path with a realistic 1600x1200 label image."""
    client, db, inspector, store = test_env

    # 1. Create inspection
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    assert r_insp.status_code == 201, r_insp.text
    insp_id = r_insp.json()["id"]

    # 2. Create scan
    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Basmati Rice",
            "brand_name": "Royal Feast",
            "batch_number": "BATCH-01",
            "geometry": {
                "panel_shape": "rectangular",
                "panel_height_mm": 120.0,
                "panel_width_mm": 80.0,
                "scale_source": "declared",
            },
        },
    )
    assert r_scan.status_code == 201, r_scan.text
    scan_id = r_scan.json()["id"]

    # 3. Upload image
    img_bytes = _create_synthetic_label_image(1600, 1200)
    mem_before = get_process_memory_mb()
    t0 = time.perf_counter()
    r_up = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front.jpg", img_bytes, "image/jpeg")},
    )
    upload_total_ms = (time.perf_counter() - t0) * 1000
    mem_after = get_process_memory_mb()

    assert r_up.status_code == 201, r_up.text
    data = r_up.json()
    assert "image_id" in data
    assert "sha256" in data
    assert data["usable"] is True

    # Check perf metrics captured
    benches = get_latest_benchmarks()
    assert len(benches["uploads"]) > 0
    latest = benches["uploads"][-1]
    assert latest["scan_id"] == scan_id
    assert latest["panel"] == "front"
    assert latest["byte_size"] == len(img_bytes)
    assert latest["total_ms"] > 0
    print(f"\n[BENCHMARK RESULT] Upload 1600x1200 ({len(img_bytes)/1024:.1f} KB): "
          f"Total={upload_total_ms:.2f}ms (Backend={latest['total_ms']:.2f}ms, "
          f"MIME={latest['mime_sniff_ms']:.2f}ms, Decode={latest['decode_ms']:.2f}ms, "
          f"Quality={latest['quality_ms']:.2f}ms, pHash={latest['phash_ms']:.2f}ms, "
          f"Write={latest['file_write_ms']:.2f}ms, DB={latest['db_persist_ms']:.2f}ms), "
          f"MemBefore={mem_before:.1f}MB, MemAfter={mem_after:.1f}MB")


def test_baseline_blurred_image_rejection_reason(test_env):
    """Verify quality failure on a blurred image without raising an unhandled exception."""
    client, db, inspector, store = test_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]

    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Tea",
            "geometry": {
                "panel_shape": "rectangular",
                "panel_height_mm": 100.0,
                "panel_width_mm": 60.0,
                "scale_source": "declared",
            },
        },
    )
    assert r_scan.status_code == 201, r_scan.text
    scan_id = r_scan.json()["id"]

    blurred = _create_blurred_image(800, 600)
    r_up = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front_blur.jpg", blurred, "image/jpeg")},
    )
    assert r_up.status_code == 201, r_up.text
    data = r_up.json()
    assert data["usable"] is False
    assert "blurred" in (data["quality_note"] or "").lower()


def test_baseline_duplicate_upload_replay(test_env):
    """Verify that replaying identical bytes returns 200 with replayed=True."""
    client, db, inspector, store = test_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Atta",
            "geometry": {
                "panel_shape": "rectangular",
                "panel_height_mm": 120.0,
                "panel_width_mm": 80.0,
                "scale_source": "declared",
            },
        },
    )
    assert r_scan.status_code == 201, r_scan.text
    scan_id = r_scan.json()["id"]

    img_bytes = _create_synthetic_label_image(1200, 900)
    r1 = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front.jpg", img_bytes, "image/jpeg")},
    )
    assert r1.status_code == 201

    r2 = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front.jpg", img_bytes, "image/jpeg")},
    )
    assert r2.status_code == 200
    assert r2.json()["replayed"] is True
    assert r2.json()["image_id"] == r1.json()["image_id"]


def test_baseline_assessment_benchmarks(test_env):
    """Benchmark the full assessment path and measure breakdown (disk, quad, rectify, ocr, rules)."""
    client, db, inspector, store = test_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]

    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Basmati Rice",
            "brand_name": "Royal Feast",
            "batch_number": "B-01",
            "geometry": {
                "panel_shape": "rectangular",
                "panel_height_mm": 120.0,
                "panel_width_mm": 80.0,
                "scale_source": "declared",
            },
        },
    )
    scan_id = r_scan.json()["id"]

    # Upload sharp front panel image
    img_bytes = _create_synthetic_label_image(1600, 1200)
    r_up = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front.jpg", img_bytes, "image/jpeg")},
    )
    assert r_up.status_code == 201

    # Run assess
    t0 = time.perf_counter()
    r_assess = client.post(f"/scans/{scan_id}/assess")
    assess_total_ms = (time.perf_counter() - t0) * 1000
    assert r_assess.status_code == 200, r_assess.text

    benches = get_latest_benchmarks()
    assert len(benches["assessments"]) > 0
    latest_assess = benches["assessments"][-1]
    assert latest_assess["scan_id"] == scan_id

    print(f"\n[BENCHMARK RESULT] Assessment: Total={assess_total_ms:.2f}ms "
          f"(Backend={latest_assess['total_ms']:.2f}ms, "
          f"DiskLoad={latest_assess['load_disk_ms']:.2f}ms, "
          f"QuadDetect={latest_assess['quad_ms']:.2f}ms, "
          f"Rectify={latest_assess['rectify_ms']:.2f}ms, "
          f"OCR={latest_assess['ocr_ms']:.2f}ms, "
          f"Rules={latest_assess['rules_ms']:.2f}ms)")


def test_baseline_reassessment_cache_reuse_latency(test_env):
    """Benchmark the idempotency cache reuse path for unchanged evidence."""
    client, db, inspector, store = test_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]

    r_scan = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": "Basmati Rice",
            "brand_name": "Royal Feast",
            "batch_number": "B-02",
            "geometry": {
                "panel_shape": "rectangular",
                "panel_height_mm": 120.0,
                "panel_width_mm": 80.0,
                "scale_source": "declared",
            },
        },
    )
    scan_id = r_scan.json()["id"]

    # Upload sharp front panel image
    img_bytes = _create_synthetic_label_image(1600, 1200)
    r_up = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front.jpg", img_bytes, "image/jpeg")},
    )
    assert r_up.status_code == 201

    from unittest.mock import patch
    from ocr.base import OcrLine, OcrResult, STATUS_SUCCESS

    mock_res = OcrResult(
        lines=[
            OcrLine(text="ROYAL FEAST BASMATI RICE", confidence=0.98, box=[[10.0, 10.0], [300.0, 10.0], [300.0, 40.0], [10.0, 40.0]]),
            OcrLine(text="NET QTY: 1 kg", confidence=0.99, box=[[10.0, 50.0], [200.0, 50.0], [200.0, 80.0], [10.0, 80.0]]),
            OcrLine(text="MRP Rs. 120.00 (INCL. OF ALL TAXES)", confidence=0.97, box=[[10.0, 90.0], [400.0, 90.0], [400.0, 120.0], [10.0, 120.0]]),
            OcrLine(text="MFG DATE: 01/2026 BATCH NO: B-2026-X", confidence=0.95, box=[[10.0, 130.0], [450.0, 130.0], [450.0, 160.0], [10.0, 160.0]]),
            OcrLine(text="MFR: ROYAL FOODS PVT LTD HYDERABAD", confidence=0.96, box=[[10.0, 170.0], [500.0, 170.0], [500.0, 200.0], [10.0, 200.0]]),
            OcrLine(text="CONSUMER CARE: 1800-111-2222 care@royal.test", confidence=0.95, box=[[10.0, 210.0], [520.0, 210.0], [520.0, 240.0], [10.0, 240.0]]),
            OcrLine(text="COUNTRY OF ORIGIN: INDIA", confidence=0.98, box=[[10.0, 250.0], [350.0, 250.0], [350.0, 280.0], [10.0, 280.0]]),
        ],
        engine="google_vision",
        provider="google_vision",
        status=STATUS_SUCCESS,
        mean_confidence=0.97,
    )

    with patch("ocr_engine.run_ocr", return_value=mock_res):
        # First assess (initial fresh evaluation)
        t0_fresh = time.perf_counter()
        r_fresh = client.post(f"/scans/{scan_id}/assess")
        fresh_ms = (time.perf_counter() - t0_fresh) * 1000
        assert r_fresh.status_code == 200

        # Second assess (cache hit / idempotency fast path)
        t0_cached = time.perf_counter()
        r_cached = client.post(f"/scans/{scan_id}/assess")
        cached_ms = (time.perf_counter() - t0_cached) * 1000
        assert r_cached.status_code == 200
        assert r_cached.json()["overall_result"] == r_fresh.json()["overall_result"]

        print(f"\n[BENCHMARK RESULT] Fresh assess={fresh_ms:.2f}ms vs Cached assess={cached_ms:.2f}ms (Speedup: {fresh_ms / max(cached_ms, 0.01):.1f}x)")
        print("FRESH DIAG:", r_fresh.json().get("diagnostics"), "RESULT:", r_fresh.json().get("overall_result"))
        print("CACHED DIAG:", r_cached.json().get("diagnostics"), "RESULT:", r_cached.json().get("overall_result"))
        assert cached_ms < fresh_ms

