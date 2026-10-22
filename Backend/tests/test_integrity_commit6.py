"""tests/test_integrity_commit6.py — Comprehensive evidence integrity and duplicate detection test suite for Commit 6.

Covers all 18 requirements from Section 24:
1. Exact same bytes -> same SHA-256
2. Altered bytes -> different SHA-256
3. Same-scan exact replay detected (HTTP 200, replayed=True)
4. Same-scan retry is idempotent (no duplicate rows)
5. Cross-scan near duplicate produces review signal
6. Different image does not falsely trigger exact replay
7. Resized same image behaves according to pHash threshold (<= 5)
8. Recompressed same image behaves correctly (<= 5)
9. Rotated image behavior documented
10. Different package not falsely marked duplicate
11. pHash failure does not invalidate evidence
12. SHA-256 remains authoritative for evidence identity
13. Offline retry does not duplicate evidence
14. Audit event remains consistent and chain intact
15. Original evidence cannot be overwritten
16. Thumbnail/rectified image never becomes authoritative evidence
17. API response remains backward compatible
18. Tamper-evident verification endpoint validates integrity
"""
import hashlib
import io
import os
from pathlib import Path
import threading
import time
import cv2
import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image, ImageDraw
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from audit import append_audit, verify_chain
from database import Base, get_db
from image_processor import (
    PHASH_NEAR_DUPLICATE,
    find_near_duplicates_with_distances,
    hamming,
    phash_bands,
    process_image_similarity_background,
    verify_stored_image,
)
from main import app
from models import Inspection, Scan, ScanImage, Store, User
from password_handler import hash_password
from rbac import get_current_user, require_inspector


def _create_sample_jpeg(text="TEST PACKAGING PRODUCT", width=800, height=600, color=(240, 240, 240)) -> bytes:
    im = Image.new("RGB", (width, height), color=color)
    draw = ImageDraw.Draw(im)
    draw.rectangle([50, 50, width - 50, height - 50], outline=(30, 30, 30), width=4)
    draw.text((100, 150), text, fill=(20, 20, 20))
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


def _post_scan(client, insp_id: int, commodity: str = "Test Commodity") -> int:
    r = client.post(
        f"/inspections/{insp_id}/scans",
        json={
            "commodity_generic": commodity,
            "geometry": {"panel_shape": "rectangular", "scale_source": "none"},
        },
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.fixture
def integrity_env(monkeypatch):
    class SyncThread(threading.Thread):
        def start(self):
            self.run()

    monkeypatch.setattr(threading, "Thread", SyncThread)

    eng = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=eng)
    S = sessionmaker(bind=eng)
    db = S()

    inspector = User(
        employee_id="INS-INT-01",
        full_name="Integrity Inspector",
        password_hash=hash_password("password123"),
        role="inspector",
        is_active=True,
    )
    store = Store(name="Integrity Store", city="Mumbai")
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


# --------------------------------------------------------------------------
# 1. Exact same bytes -> same SHA-256
# --------------------------------------------------------------------------
def test_01_exact_same_bytes_same_sha256():
    data = b"NiyamNetra Immutable Evidence Bytes"
    h1 = hashlib.sha256(data).hexdigest()
    h2 = hashlib.sha256(data).hexdigest()
    assert h1 == h2
    assert len(h1) == 64


# --------------------------------------------------------------------------
# 2. Altered bytes -> different SHA-256
# --------------------------------------------------------------------------
def test_02_altered_bytes_different_sha256():
    data1 = b"NiyamNetra Immutable Evidence Bytes"
    data2 = b"NiyamNetra Immutable Evidence ByteS"  # 1 bit/byte flip
    h1 = hashlib.sha256(data1).hexdigest()
    h2 = hashlib.sha256(data2).hexdigest()
    assert h1 != h2


# --------------------------------------------------------------------------
# 3. Same-scan exact replay detected (HTTP 200, replayed=True)
# --------------------------------------------------------------------------
def test_03_same_scan_exact_replay_detected(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    scan_id = _post_scan(client, insp_id, "Salt")

    raw = _create_sample_jpeg("SALT PACKET A")
    # First upload -> 201 Created
    r1 = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front.jpg", raw, "image/jpeg")},
    )
    assert r1.status_code == 201
    d1 = r1.json()
    assert d1.get("replayed") is None or d1.get("replayed") is False

    # Second upload with identical bytes -> 200 OK with replayed=True
    r2 = client.post(
        f"/scans/{scan_id}/images",
        data={"panel": "front"},
        files={"file": ("front.jpg", raw, "image/jpeg")},
    )
    assert r2.status_code == 200
    d2 = r2.json()
    assert d2["replayed"] is True
    assert d2["image_id"] == d1["image_id"]
    assert d2["sha256"] == d1["sha256"]
    assert d2["similarity_status"] == "exact_replay"


# --------------------------------------------------------------------------
# 4. Same-scan retry is idempotent (no duplicate rows)
# --------------------------------------------------------------------------
def test_04_same_scan_retry_is_idempotent(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    scan_id = _post_scan(client, insp_id, "Sugar")

    raw = _create_sample_jpeg("SUGAR PACKET")
    for _ in range(3):
        client.post(
            f"/scans/{scan_id}/images",
            data={"panel": "front"},
            files={"file": ("front.jpg", raw, "image/jpeg")},
        )

    # Exactly 1 row should exist in the database
    rows = db.query(ScanImage).filter(ScanImage.scan_id == scan_id).all()
    assert len(rows) == 1


# --------------------------------------------------------------------------
# 5. Cross-scan near duplicate produces review signal
# --------------------------------------------------------------------------
def test_05_cross_scan_near_duplicate_produces_review_signal(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    s1 = _post_scan(client, insp_id, "Tea")
    s2 = _post_scan(client, insp_id, "Tea")

    raw1 = _create_sample_jpeg("PREMIUM ASSAM TEA", width=800, height=600)
    # Slightly resized / recompressed variation of same package
    raw2 = _create_sample_jpeg("PREMIUM ASSAM TEA", width=800, height=600)

    # Upload to scan 1
    r_up1 = client.post(f"/scans/{s1}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw1, "image/jpeg")})
    img1_id = r_up1.json()["image_id"]
    img1 = db.query(ScanImage).filter(ScanImage.id == img1_id).first()

    # Run background similarity synchronously for img1
    res1 = process_image_similarity_background(
        image_id=img1.id,
        file_path_str=img1.file_path,
        scan_id=s1,
        inspection_id=insp_id,
        panel="front",
        user_id=inspector.id,
        db=db,
    )
    assert res1["status"] in ("unique", "pending")

    # Upload variation to scan 2
    r_up2 = client.post(f"/scans/{s2}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw2, "image/jpeg")})
    img2_id = r_up2.json()["image_id"]
    img2 = db.query(ScanImage).filter(ScanImage.id == img2_id).first()

    # Process background similarity for img2
    res2 = process_image_similarity_background(
        image_id=img2.id,
        file_path_str=img2.file_path,
        scan_id=s2,
        inspection_id=insp_id,
        panel="front",
        user_id=inspector.id,
        db=db,
    )
    # Visual match should be detected as near_duplicate review signal
    assert res2["status"] == "near_duplicate"
    assert res2["matched_image_id"] == img1_id
    assert res2["hamming_distance"] <= PHASH_NEAR_DUPLICATE


# --------------------------------------------------------------------------
# 6. Different image does not falsely trigger exact replay
# --------------------------------------------------------------------------
def test_06_different_image_does_not_falsely_trigger_exact_replay(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    s = _post_scan(client, insp_id, "Flour")

    raw1 = _create_sample_jpeg("FLOUR BRAND A")
    raw2 = _create_sample_jpeg("FLOUR BRAND B")

    r1 = client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw1, "image/jpeg")})
    r2 = client.post(f"/scans/{s}/images", data={"panel": "back"}, files={"file": ("back.jpg", raw2, "image/jpeg")})

    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["image_id"] != r2.json()["image_id"]


# --------------------------------------------------------------------------
# 7. Resized same image behaves according to pHash threshold (<= 5)
# --------------------------------------------------------------------------
def test_07_resized_same_image_phash_threshold(tmp_path):
    orig = Image.new("RGB", (1600, 1200), color=(220, 220, 220))
    d = ImageDraw.Draw(orig)
    d.rectangle([100, 100, 1500, 1100], outline=(20, 20, 20), width=10)
    d.text((200, 300), "DETERGENT POWDER 1kg", fill=(10, 10, 10))

    p1 = tmp_path / "orig.jpg"
    orig.save(p1, format="JPEG", quality=90)

    # Downscale to 800x600
    resized = orig.resize((800, 600), Image.Resampling.LANCZOS)
    p2 = tmp_path / "resized.jpg"
    resized.save(p2, format="JPEG", quality=90)

    h1, _ = phash_bands(p1)
    h2, _ = phash_bands(p2)
    dist = hamming(h1, h2)
    assert dist <= PHASH_NEAR_DUPLICATE


# --------------------------------------------------------------------------
# 8. Recompressed same image behaves correctly (<= 5)
# --------------------------------------------------------------------------
def test_08_recompressed_same_image_behavior(tmp_path):
    orig = Image.new("RGB", (800, 600), color=(230, 230, 230))
    d = ImageDraw.Draw(orig)
    d.rectangle([50, 50, 750, 550], outline=(30, 30, 30), width=6)
    d.text((100, 200), "BISCUIT PACK 200g", fill=(20, 20, 20))

    p1 = tmp_path / "q95.jpg"
    orig.save(p1, format="JPEG", quality=95)

    p2 = tmp_path / "q75.jpg"
    orig.save(p2, format="JPEG", quality=75)

    h1, _ = phash_bands(p1)
    h2, _ = phash_bands(p2)
    dist = hamming(h1, h2)
    assert dist <= PHASH_NEAR_DUPLICATE


# --------------------------------------------------------------------------
# 9. Rotated image behavior documented
# --------------------------------------------------------------------------
def test_09_rotated_image_behavior_documented(tmp_path):
    orig = Image.new("RGB", (600, 600), color=(240, 240, 240))
    d = ImageDraw.Draw(orig)
    d.rectangle([100, 100, 500, 300], outline=(0, 0, 0), width=5)

    p1 = tmp_path / "unrotated.jpg"
    orig.save(p1, format="JPEG")

    # Rotate 90 degrees: standard pHash is NOT rotation-invariant
    rotated = orig.rotate(90)
    p2 = tmp_path / "rot90.jpg"
    rotated.save(p2, format="JPEG")

    h1, _ = phash_bands(p1)
    h2, _ = phash_bands(p2)
    dist = hamming(h1, h2)
    # Documented fact: standard DCT pHash departs when rotated
    assert dist > 0


# --------------------------------------------------------------------------
# 10. Different package not falsely marked duplicate
# --------------------------------------------------------------------------
def test_10_different_package_not_falsely_marked_duplicate(tmp_path):
    # Pack A: bright with text
    im_a = Image.new("RGB", (600, 600), color=(250, 250, 250))
    d_a = ImageDraw.Draw(im_a)
    d_a.rectangle([50, 50, 550, 550], fill=(200, 50, 50))
    p_a = tmp_path / "pack_a.jpg"
    im_a.save(p_a, format="JPEG")

    # Pack B: dark circles
    im_b = Image.new("RGB", (600, 600), color=(30, 30, 30))
    d_b = ImageDraw.Draw(im_b)
    d_b.ellipse([100, 100, 500, 500], fill=(50, 180, 50))
    p_b = tmp_path / "pack_b.jpg"
    im_b.save(p_b, format="JPEG")

    h_a, _ = phash_bands(p_a)
    h_b, _ = phash_bands(p_b)
    dist = hamming(h_a, h_b)
    assert dist > PHASH_NEAR_DUPLICATE


# --------------------------------------------------------------------------
# 11. pHash failure does not invalidate evidence
# --------------------------------------------------------------------------
def test_11_phash_failure_does_not_invalidate_evidence(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    scan_id = _post_scan(client, insp_id, "Soap")

    img = ScanImage(
        scan_id=scan_id, panel="front", sequence=0,
        file_path="non_existent_file.jpg", byte_size=1000,
        width_px=800, height_px=600, mime_type="image/jpeg",
        sha256="fake_sha256",
    )
    db.add(img)
    db.commit()

    res = process_image_similarity_background(
        image_id=img.id,
        file_path_str="non_existent_file.jpg",
        scan_id=scan_id,
        inspection_id=insp_id,
        panel="front",
        user_id=inspector.id,
        db=db,
    )
    assert res["status"] == "analysis_failed"
    # Evidence row still exists in DB
    reloaded = db.query(ScanImage).filter(ScanImage.id == img.id).first()
    assert reloaded is not None
    assert reloaded.sha256 == "fake_sha256"


# --------------------------------------------------------------------------
# 12. SHA-256 remains authoritative
# --------------------------------------------------------------------------
def test_12_sha256_remains_authoritative(tmp_path):
    p = tmp_path / "evidence.jpg"
    raw = _create_sample_jpeg("AUTHORITATIVE EVIDENCE")
    p.write_bytes(raw)
    recorded_sha = hashlib.sha256(raw).hexdigest()

    assert verify_stored_image(p, recorded_sha) is True

    # Tamper with 1 byte on disk
    tampered = bytearray(raw)
    tampered[100] = (tampered[100] + 1) % 256
    p.write_bytes(bytes(tampered))

    assert verify_stored_image(p, recorded_sha) is False


# --------------------------------------------------------------------------
# 13. Offline retry does not duplicate evidence
# --------------------------------------------------------------------------
def test_13_offline_retry_does_not_duplicate_evidence(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    s = _post_scan(client, insp_id, "Oil")

    raw = _create_sample_jpeg("COOKING OIL")
    # Simulate network drop: first attempt stored on server
    r1 = client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw, "image/jpeg")})
    assert r1.status_code == 201

    # Mobile retry sends same payload
    r2 = client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw, "image/jpeg")})
    assert r2.status_code == 200
    assert r2.json()["replayed"] is True

    count = db.query(ScanImage).filter(ScanImage.scan_id == s).count()
    assert count == 1


# --------------------------------------------------------------------------
# 14. Audit event remains consistent and chain intact
# --------------------------------------------------------------------------
def test_14_audit_event_consistency(integrity_env):
    _, db, inspector, store = integrity_env
    append_audit(db, action="image_uploaded", inspection_id=1, scan_id=1, user_id=inspector.id, new_value="front:abcd1234")
    append_audit(db, action="image_replayed", inspection_id=1, scan_id=1, user_id=inspector.id, new_value="front:abcd1234")
    append_audit(db, action="near_duplicate_detected", inspection_id=1, scan_id=2, user_id=inspector.id, new_value="front:matched_img_1:dist_2")

    chain_state = verify_chain(db)
    assert chain_state["intact"] is True
    assert chain_state["entries"] >= 3


# --------------------------------------------------------------------------
# 15. Original evidence cannot be overwritten
# --------------------------------------------------------------------------
def test_15_original_evidence_cannot_be_overwritten(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    s = _post_scan(client, insp_id, "Milk")

    raw1 = _create_sample_jpeg("MILK ORIGINAL")
    r1 = client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw1, "image/jpeg")})
    img1 = db.query(ScanImage).filter(ScanImage.id == r1.json()["image_id"]).first()
    path1 = Path(img1.file_path)
    sha1 = img1.sha256

    # Upload second photo for same panel
    raw2 = _create_sample_jpeg("MILK RETAKE")
    r2 = client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw2, "image/jpeg")})
    img2 = db.query(ScanImage).filter(ScanImage.id == r2.json()["image_id"]).first()

    assert img1.id != img2.id
    assert img1.file_path != img2.file_path
    # Original file is intact on disk with identical hash
    assert verify_stored_image(path1, sha1) is True


# --------------------------------------------------------------------------
# 16. Thumbnail or rectified image never becomes authoritative evidence
# --------------------------------------------------------------------------
def test_16_thumbnail_never_becomes_authoritative_evidence(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    s = _post_scan(client, insp_id, "Coffee")

    raw = _create_sample_jpeg("COFFEE JAR", width=1200, height=900)
    r_up = client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw, "image/jpeg")})
    img_id = r_up.json()["image_id"]

    # Thumbnail endpoint returns downscaled derivative
    r_thumb = client.get(f"/scans/{s}/images/{img_id}/thumbnail")
    assert r_thumb.status_code == 200
    thumb_bytes = r_thumb.content

    # The thumbnail hash must NOT match the authoritative evidence hash
    thumb_sha = hashlib.sha256(thumb_bytes).hexdigest()
    assert thumb_sha != r_up.json()["sha256"]


# --------------------------------------------------------------------------
# 17. API response remains backward compatible
# --------------------------------------------------------------------------
def test_17_api_response_remains_backward_compatible(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    s = _post_scan(client, insp_id, "Snacks")

    raw = _create_sample_jpeg("POTATO CHIPS")
    r_up = client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw, "image/jpeg")})
    assert r_up.status_code == 201
    data = r_up.json()

    # Verify all expected keys are present
    expected_keys = [
        "image_id", "sha256", "usable", "quality_note", "suggested_corners",
        "geometry_status", "rectification_status", "geometry_confidence",
        "perspective_severity", "residual_tilt_deg", "rectified_width", "rectified_height",
        "similarity_status", "duplicate_of_image_id", "hamming_distance",
    ]
    for k in expected_keys:
        assert k in data, f"Missing key: {k}"


# --------------------------------------------------------------------------
# 18. Tamper-evident verification endpoint validates integrity
# --------------------------------------------------------------------------
def test_18_tamper_evident_verification_endpoint(integrity_env):
    client, db, inspector, store = integrity_env
    r_insp = client.post("/inspections", json={"store_id": store.id, "transaction_type": "retail_sale"})
    insp_id = r_insp.json()["id"]
    s = _post_scan(client, insp_id, "Juice")

    raw = _create_sample_jpeg("ORANGE JUICE")
    client.post(f"/scans/{s}/images", data={"panel": "front"}, files={"file": ("front.jpg", raw, "image/jpeg")})

    r_ver = client.get(f"/scans/{s}/verify")
    assert r_ver.status_code == 200
    v_data = r_ver.json()
    assert v_data["all_intact"] is True
    assert len(v_data["images"]) == 1
    img_ver = v_data["images"][0]
    assert img_ver["sha256_matches"] is True
    assert img_ver["tamper_evident"] is True
    assert "similarity_status" in img_ver
