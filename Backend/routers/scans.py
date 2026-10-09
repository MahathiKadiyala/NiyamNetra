"""routers/scans.py"""
import math
import os
import threading
from datetime import date
from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, Request, Response, UploadFile, status
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from audit import append_audit
from config import settings
from database import get_db
from models import Finding, Inspection, Scan, ScanImage, User
from rbac import get_current_user, owned_scan
from schemas import ScanListItemOut, ScanOut, VerdictCounts, BatchAssessRequest, BatchAssessItem, BatchAssessResponse
from pydantic import BaseModel, Field, field_validator, model_validator

router = APIRouter(prefix="/scans", tags=["scans"])

MAX_BYTES = settings.MAX_UPLOAD_MB * 1024 * 1024

# Assess concurrency guard (11 §4): ASSESS_CONCURRENCY=0 -> cpu_count.
# A full assess run is CPU-bound (OCR + 19 checks); unbounded parallel
# assesses OOM the worker. Try-acquire with 20s wait, else 503+Retry-After.
def _assess_limit() -> int:
    try:
        n = int(getattr(settings, "ASSESS_CONCURRENCY", 0) or 0)
    except Exception:
        n = 0
    if n <= 0:
        try:
            n = os.cpu_count() or 4
        except Exception:
            n = 4
    return max(1, n)

_ASSESS_SEMAPHORE = threading.Semaphore(_assess_limit())
_ASSESS_WAIT_S = int(getattr(settings, "ASSESS_QUEUE_WAIT_S", 20) or 20)

ALLOWED_PANELS = {"front", "back", "side", "mrp", "batch", "other", "principal"}
ALLOWED_QUANTITY_UNITS = {
    "g", "kg", "mg", "gm", "ml", "l", "ltr", "litre",
    "pcs", "pc", "pack", "m", "cm", "mm", "n", "u",
}

_OCR_CACHE_SALT = "ocr-v2"


def _label_for(line) -> str | None:
    """Map an OCR line to the declared-field label whose character height Rule 7/9 governs.
    Returns None for lines that carry no height-regulated field. Referenced by build_context."""
    t = (getattr(line, "text", "") or "").lower()
    if any(k in t for k in ("net qty", "net quantity", "net wt", "net weight", "net vol", "contents")):
        return "net_quantity"
    if any(k in t for k in ("m.r.p", "mrp", "maximum retail price")):
        return "mrp"
    return None


@router.post("/{scan_id}/images", status_code=status.HTTP_201_CREATED)
async def upload_image(
    request: Request,
    response: Response,
    panel: str = Form(...),
    file: UploadFile = File(...),
    scan: Scan = Depends(owned_scan),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Attach one panel image to an existing scan.

    Note what this does NOT do: it does not create a Scan. v1.x created a new
    Scan per uploaded image, so a package photographed front, back, MRP panel
    and batch panel became four packages with four independent verdicts, and
    the report listed the same item four times.
    """
    # Submitted-inspection guard: frozen once submitted (same as update_scan).
    if scan.inspection_id:
        _insp = db.get(Inspection, scan.inspection_id)
        if _insp is not None and _insp.status == "submitted":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="Inspection submitted; scan frozen")

    # Panel validation: allow-list, normalized; reject empty/>24 chars (422).
    normalized_panel = (panel or "").strip().lower()
    if not normalized_panel or len(normalized_panel) > 24:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Invalid panel: must be 1-24 chars")
    if normalized_panel not in ALLOWED_PANELS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Invalid panel: must be one of {sorted(ALLOWED_PANELS)}")

    # OOM guard: reject early on Content-Length before buffering the body.
    try:
        _cl = request.headers.get("content-length")
        if _cl is not None and int(_cl) > MAX_BYTES + 1024 * 1024:
            raise HTTPException(
                status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail=f"Image exceeds {settings.MAX_UPLOAD_MB} MB",
            )
    except HTTPException:
        raise
    except Exception:
        pass

    import time
    from perf_baseline import get_process_memory_mb, record_upload_timing
    t_arrival = time.perf_counter()
    mem_before = get_process_memory_mb()

    raw = await file.read()
    t_raw = time.perf_counter()
    if len(raw) > MAX_BYTES:
        raise HTTPException(
            status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"Image exceeds {settings.MAX_UPLOAD_MB} MB",
        )

    # Idempotent re-upload (09 T14): a sync retry that lost its response
    # re-sends the SAME bytes. scan_images rows are append-only (migration
    # 0006 even denies DELETE), so the honest reply is the ORIGINAL record
    # — 200 with its id — instead of a duplicate row for duplicate evidence.
    import hashlib as _hashlib
    _sha = _hashlib.sha256(raw).hexdigest()
    _dup = (db.query(ScanImage)
              .filter(ScanImage.scan_id == scan.id, ScanImage.sha256 == _sha)
              .first())
    if _dup is not None:
        from audit import append_audit as _append_audit
        _append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id,
                      user_id=user.id, action="image_replayed",
                      new_value=f"{_dup.panel}:{_sha[:16]}",
                      reason="Same-scan exact SHA-256 replay (idempotent)")
        response.status_code = status.HTTP_200_OK
        return {
            "image_id": _dup.id,
            "sha256": _dup.sha256,
            "usable": True,
            "quality_note": "already stored (replayed upload)",
            "suggested_corners": None,
            "replayed": True,
            "similarity_status": "exact_replay",
            "duplicate_of_image_id": _dup.id,
            "hamming_distance": 0,
        }

    # Cross-scan exact duplicate detection (Section 7 & 17: review signal, not automatic fraud)
    _cross_dup = (db.query(ScanImage)
                    .filter(ScanImage.scan_id != scan.id, ScanImage.sha256 == _sha)
                    .first())
    _initial_similarity = "pending"
    _dup_img_id = None
    _hamming_dist = None
    if _cross_dup is not None:
        _initial_similarity = "exact_replay"
        _dup_img_id = _cross_dup.id
        _hamming_dist = 0

    from image_processor import (
        PHASH_BANDS, assess_quality, phash_bands, read_capture_time,
        sniff_mime, store_upload,
    )
    from PIL import UnidentifiedImageError
    from PIL.Image import DecompressionBombError
    import cv2
    import numpy as np

    # MIME sniffing: never trust file.content_type; sniff via PIL.
    # sniff_mime enforces MAX_IMAGE_PIXELS (decompression-bomb ceiling) via
    # PIL before any cv2 decode, so a bomb fails fast with 413, not OOM.
    t_mime_start = time.perf_counter()
    try:
        sniffed_mime = sniff_mime(raw)
    except ValueError as e:
        msg = str(e)
        if "too large" in msg.lower():
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=msg)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)
    t_mime_end = time.perf_counter()

    # Pre-validate decodability BEFORE store_upload to avoid orphan files.
    # PIL already verified pixels above; cv2 probe is the second gate.
    t_probe_start = time.perf_counter()
    try:
        _probe = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    except Exception:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Image could not be decoded")
    if _probe is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Image could not be decoded")
    t_probe_end = time.perf_counter()

    t_store_start = time.perf_counter()
    try:
        stored = store_upload(raw, sniffed_mime, scan.inspection_id, scan.id)
    except ValueError as e:
        msg = str(e)
        if "too large" in msg.lower():
            raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=msg)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=msg)
    except OSError as e:
        # Disk errors (ENOSPC/EACCES from mkdir/write) are 503, not 422:
        # the evidence store is unavailable, not the upload invalid.
        import errno as _errno
        _no = getattr(e, "errno", None)
        if _no in (_errno.ENOSPC, _errno.EACCES, _errno.EROFS, 28, 13, 30):
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                                detail="Evidence store unavailable; retry shortly")
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Unsupported or corrupt image: {e}")
    except UnidentifiedImageError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Unsupported or corrupt image: {e}")
    except DecompressionBombError as e:
        raise HTTPException(status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=f"Image too large: {e}")
    except Exception as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail=f"Image store failed: {type(e).__name__}")
    t_store_end = time.perf_counter()

    t_decode_start = time.perf_counter()
    bgr = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if bgr is None:
        # Pre-validation passed but decode now fails; remove orphan if created.
        try:
            from pathlib import Path as _P
            _p = _P(str(stored.path))
            if _p.exists():
                _p.unlink()
        except Exception:
            pass
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Image could not be decoded")
    t_decode_end = time.perf_counter()
    mem_peak = get_process_memory_mb()

    t_quality_start = time.perf_counter()
    quality = assess_quality(bgr)
    t_quality_end = time.perf_counter()

    # pHash is deferred to non-blocking background analysis (Section 8 & 25)
    t_phash_start = time.perf_counter()
    t_phash_end = t_phash_start

    # Sequence race: (scan_id, panel, sequence) is unique. Two concurrent
    # uploads can read the same COUNT; retry with max+1 requery (3 attempts),
    # else 409 so the client retries instead of seeing a 500.
    from image_processor import prepare_derived_analysis_image
    analysis_path, a_w, a_h = prepare_derived_analysis_image(stored.path, bgr)

    t_db_start = time.perf_counter()
    img = None
    for _attempt in range(3):
        try:
            _seq = db.query(ScanImage).filter(
                ScanImage.scan_id == scan.id, ScanImage.panel == normalized_panel
            ).count()
            # On retry, ensure forward progress past the conflicting slot.
            if _attempt > 0:
                from sqlalchemy import func as _func
                _mx = db.query(_func.max(ScanImage.sequence)).filter(
                    ScanImage.scan_id == scan.id, ScanImage.panel == normalized_panel
                ).scalar()
                if _mx is not None and _mx >= _seq:
                    _seq = _mx + 1
            img = ScanImage(
                scan_id=scan.id, panel=normalized_panel,
                sequence=_seq,
                file_path=str(stored.path), byte_size=stored.byte_size,
                width_px=stored.width_px, height_px=stored.height_px,
                mime_type=stored.mime_type, sha256=stored.sha256,
                phash=None,
                captured_at=read_capture_time(raw),
                blur_variance=quality.blur_variance, glare_ratio=quality.glare_ratio,
                similarity_status=_initial_similarity,
                duplicate_of_image_id=_dup_img_id,
                hamming_distance=_hamming_dist,
                analysis_file_path=str(analysis_path),
                rectified_file_path=None,
            )
            db.add(img)
            # Invalidate any cached assessment on scan since image set has changed
            scan.ocr_cache_hash = None
            scan.ocr_cache = None
            db.commit()
            break
        except IntegrityError:
            db.rollback()
            if _attempt == 2:
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    detail="Image slot conflict; retry upload")
            continue
    assert img is not None
    db.refresh(img)
    t_db_end = time.perf_counter()

    append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id,
                 user_id=user.id, action="image_uploaded",
                 new_value=f"{normalized_panel}:{stored.sha256[:16]}")
    if _cross_dup is not None:
        append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id,
                     user_id=user.id, action="cross_scan_exact_duplicate_detected",
                     new_value=f"{normalized_panel}:matched_img_{_cross_dup.id}",
                     reason="Exact SHA-256 match found in previous scan (review signal)")

    # Dispatch non-blocking background similarity analysis (pHash & cross-scan near duplicate search)
    import threading
    from image_processor import process_image_similarity_background
    threading.Thread(
        target=process_image_similarity_background,
        kwargs={
            "image_id": img.id,
            "file_path_str": str(stored.path),
            "scan_id": scan.id,
            "inspection_id": scan.inspection_id,
            "panel": normalized_panel,
            "user_id": user.id,
            "db": db,
        },
        daemon=True,
    ).start()

    # A poor-quality image is accepted and flagged. It is NOT rejected: doing
    # so throws away the capture and leaves no record that an unreadable
    # package was found, which silently biases the statistics toward the
    # photogenic subset of the field.
    # Advisory panel-quad suggestion (classical detector, never acts on
    # evidence). The client may offer it for on-screen adjustment before
    # assess; None means "frame it manually".
    t_quad_start = time.perf_counter()
    _corners = None
    _geom_meta = {
        "geometry_status": "unavailable",
        "rectification_status": "skipped",
        "confidence": 0.0,
        "perspective_severity": "unusable",
        "residual_tilt_deg": 0.0,
        "rectified_width": None,
        "rectified_height": None,
    }
    try:
        from image_processor import detect_candidate_quad
        _q, _geom_meta = detect_candidate_quad(bgr)
        if _q is not None:
            _corners = [[round(float(x), 1), round(float(y), 1)] for x, y in _q.tolist()]
    except Exception:
        _corners = None
    t_quad_end = time.perf_counter()

    mem_after = get_process_memory_mb()
    t_finish = time.perf_counter()

    record_upload_timing({
        "scan_id": scan.id,
        "panel": normalized_panel,
        "byte_size": len(raw),
        "width_px": stored.width_px,
        "height_px": stored.height_px,
        "byte_read_ms": round((t_raw - t_arrival) * 1000, 2),
        "mime_sniff_ms": round((t_mime_end - t_mime_start) * 1000, 2),
        "probe_decode_ms": round((t_probe_end - t_probe_start) * 1000, 2),
        "file_write_ms": round((t_store_end - t_store_start) * 1000, 2),
        "decode_ms": round((t_decode_end - t_decode_start) * 1000, 2),
        "quality_ms": round((t_quality_end - t_quality_start) * 1000, 2),
        "phash_ms": round((t_phash_end - t_phash_start) * 1000, 2),
        "db_persist_ms": round((t_db_end - t_db_start) * 1000, 2),
        "quad_detect_ms": round((t_quad_end - t_quad_start) * 1000, 2),
        "total_ms": round((t_finish - t_arrival) * 1000, 2),
        "mem_before_mb": mem_before,
        "mem_peak_mb": mem_peak,
        "mem_after_mb": mem_after,
    })

    return {
        "image_id": img.id,
        "sha256": img.sha256,
        "usable": quality.usable,
        "quality_note": quality.reason,
        "suggested_corners": _corners,
        "geometry_status": _geom_meta.get("geometry_status", "unavailable"),
        "rectification_status": _geom_meta.get("rectification_status", "skipped"),
        "geometry_confidence": _geom_meta.get("confidence", 0.0),
        "perspective_severity": _geom_meta.get("perspective_severity", "unusable"),
        "residual_tilt_deg": _geom_meta.get("residual_tilt_deg", 0.0),
        "rectified_width": _geom_meta.get("rectified_width"),
        "rectified_height": _geom_meta.get("rectified_height"),
        "similarity_status": _initial_similarity,
        "duplicate_of_image_id": _dup_img_id,
        "hamming_distance": _hamming_dist,
    }


@router.post("/{scan_id}/assess", response_model=ScanOut)
def assess_scan(
    force: bool = Query(default=False),
    scan: Scan = Depends(owned_scan),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Run all statutory checks and persist the findings.

    Idempotent by replacement: re-assessing deletes the previous findings for
    this scan and writes a fresh set, and the replacement is audited. It does
    NOT edit engine_verdict in place (C11) — the old row is gone, the new row
    is new, and the audit log records that an assessment was re-run.

    Idempotency optimization (Audit Section 19): If the scan has already been
    assessed and the underlying evidence images have not changed, returns the
    persisted assessment without re-executing duplicate OCR + LLM work (unless
    explicitly forced via force=True).

    Concurrency: guarded by a process-local semaphore (ASSESS_CONCURRENCY or
    cpu_count); if the pool is busy for ASSESS_QUEUE_WAIT_S the endpoint
    returns 503 with Retry-After instead of OOMing the worker.
    """
    import hashlib as _hashlib
    import logging as _log
    _logger = _log.getLogger("niyamnetra.assess")

    # Fast idempotency check before acquiring concurrency semaphore
    if not force:
        _existing_findings = db.query(Finding).filter(Finding.scan_id == scan.id).all()
        if _existing_findings and scan.overall_result not in ("not_assessed", None):
            _imgs = db.query(ScanImage).filter(ScanImage.scan_id == scan.id).all()
            _img_parts = sorted(f"{_im.panel}:{_im.sha256}" for _im in _imgs)
            _curr_hash = _hashlib.sha256((_OCR_CACHE_SALT + "|" + "|".join(_img_parts)).encode()).hexdigest()
            if getattr(scan, "ocr_cache_hash", None) == _curr_hash:
                _logger.info(
                    "[ASSESSMENT] idempotent_assessment_reused scan_id=%s hash=%s checks_count=%d",
                    scan.id, _curr_hash[:12], len(_existing_findings)
                )
                return _scan_out(db, scan)

    if not _ASSESS_SEMAPHORE.acquire(timeout=_ASSESS_WAIT_S):
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Assessment capacity busy; retry shortly",
                            headers={"Retry-After": str(_ASSESS_WAIT_S)})
    try:
        return _assess_inner(scan, user, db, force=force)
    finally:
        try:
            _ASSESS_SEMAPHORE.release()
        except Exception:
            pass


@router.post("/batch-assess", response_model=BatchAssessResponse)
def assess_batch(
    body: BatchAssessRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Re-assess a list of scans the caller owns.

    Returns per-item results so the client always receives HTTP 200: individual
    scan failures (permission denied, capacity busy, not found) are reported in
    the item's error field rather than turning into a batch 503/403.

    Each scan is assessed sequentially through the shared concurrency semaphore
    (_ASSESS_SEMAPHORE) with a per-item timeout of _ASSESS_WAIT_S seconds; a
    timeout yields ok=False, error='capacity busy' for that item only.
    """
    from schemas import BatchAssessItem, BatchAssessRequest, BatchAssessResponse
    results = []
    for scan_id in body.scan_ids:
        item_result = None
        try:
            scan = db.get(Scan, scan_id)
            if scan is None:
                results.append(BatchAssessItem(scan_id=scan_id, ok=False, error="not found"))
                continue
            # Ownership: inspector must own the inspection; admin sees all.
            if user.role != "admin":
                _insp = db.get(Inspection, scan.inspection_id)
                if _insp is None or _insp.user_id != user.id:
                    results.append(BatchAssessItem(scan_id=scan_id, ok=False, error="not permitted"))
                    continue
            if not _ASSESS_SEMAPHORE.acquire(timeout=_ASSESS_WAIT_S):
                results.append(BatchAssessItem(scan_id=scan_id, ok=False, error="capacity busy"))
                continue
            try:
                _out = _assess_inner(scan, user, db)
                results.append(BatchAssessItem(
                    scan_id=scan_id,
                    overall_result=_out.overall_result,
                    ok=True,
                ))
            except Exception as _e:
                results.append(BatchAssessItem(scan_id=scan_id, ok=False, error=str(_e)[:200]))
            finally:
                try:
                    _ASSESS_SEMAPHORE.release()
                except Exception:
                    pass
        except Exception as _outer:
            results.append(BatchAssessItem(scan_id=scan_id, ok=False, error=str(_outer)[:200]))
    return {"total": len(results), "results": [r.model_dump() for r in results]}


def _assess_inner(scan: Scan, user: User, db: Session, force: bool = False):
    import time
    import logging as _log
    from perf_baseline import record_assess_timing
    t_assess_start = time.perf_counter()

    assess_log = _log.getLogger("niyamnetra.assess")
    _images_count = db.query(ScanImage).filter(ScanImage.scan_id == scan.id).count()
    assess_log.info(
        "[ASSESSMENT] assessment_started inspection_id=%s scan_id=%s image_count=%d force=%s",
        scan.inspection_id, scan.id, _images_count, force
    )

    inspection = db.get(Inspection, scan.inspection_id)
    if inspection is not None and inspection.status == "submitted" and not force:
        # In dev mode, allow re-assessing packages for testing and demonstration
        if getattr(settings, "ENV", "dev") not in ("dev", "development"):
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="Inspection submitted; scan frozen")
    ctx = build_context(db, scan, inspection)

    from rules_engine import assess as run_assessment
    t_rules_start = time.perf_counter()
    findings, verdict, provenance = run_assessment(ctx)
    t_rules_end = time.perf_counter()

    _rules_log = _log.getLogger("niyamnetra.rules")
    _rules_log.info(
        "[RULES] rules_completed rule_pack_version=%s total_evaluated=%d passed=%d failed=%d not_assessed=%d overall_result=%s",
        provenance.get("rule_pack_version", "2026.09.v1"), len(findings), verdict.passed, verdict.failed, verdict.not_assessed, verdict.overall_result
    )

    # Persist OCR evidence on the scan (truncated 20k) + mean confidence so
    # reports and the review queue can show what the engine actually read
    # without re-running OCR. build_context already staged these on the
    # instance where possible; ensure they are set even if ctx was early-out.
    try:
        _full = getattr(ctx, "ocr_full_text", None)
        if _full is None and getattr(ctx, "fields", None):
            _full = " ".join(
                (getattr(f, "source_line", None) or getattr(f, "value", None) or "")
                for f in ctx.fields.values())
        if _full:
            scan.ocr_text = _full[:20000]
        scan.ocr_confidence_mean = getattr(ctx, "ocr_mean_confidence", None)
    except Exception:
        pass

    old = db.query(Finding).filter(Finding.scan_id == scan.id).all()
    if old:
        # Flush-only: batched into the single commit below so the audit row
        # cannot commit while the findings replacement later fails.
        append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id,
                     user_id=user.id, action="assessment_rerun",
                     old_value=scan.overall_result, reason="Re-assessment requested",
                     commit=False)
        for f in old:
            db.delete(f)
        db.flush()

    for f in findings:
        db.add(Finding(
            scan_id=scan.id, check_id=f.check_id, title=f.title,
            engine_verdict=f.verdict, severity=f.severity, reason=f.reason,
            observed=f.observed, required=f.required, citation=f.citation,
            ledger_ref=f.ledger_ref, confidence=f.confidence, limb=f.limb,
        ))

    scan.overall_result = verdict.overall_result
    scan.violation_limb = verdict.violation_limb
    scan.recommended_action = verdict.recommended_action
    scan.checks_total = verdict.checks_total
    scan.checks_assessed = verdict.checks_assessed
    scan.rules_as_at = ctx.rules_as_at
    scan.catalog_hash = provenance["catalog_hash"]
    scan.engine_version = provenance["engine_version"]
    if ctx.rules_as_at and ctx.rules_as_at >= date(2026, 9, 21):
        scan.rule_pack_version = settings.RULE_PACK_VERSION
    else:
        scan.rule_pack_version = provenance.get("rule_pack_version", "2026.07.v1")
    scan.mm_per_pixel = ctx.mm_per_pixel
    scan.scale_source = ctx.scale_source

    from queries import resolve_duplicate
    scan.duplicate_of = resolve_duplicate(db, scan, inspection)

    if not getattr(scan, "ocr_cache_hash", None):
        import hashlib as _hashlib
        _imgs = db.query(ScanImage).filter(ScanImage.scan_id == scan.id).all()
        if _imgs:
            _img_parts = sorted(f"{_im.panel}:{_im.sha256}" for _im in _imgs)
            scan.ocr_cache_hash = _hashlib.sha256((_OCR_CACHE_SALT + "|" + "|".join(_img_parts)).encode()).hexdigest()

    try:
        db.commit()
    except IntegrityError as exc:
        db.rollback()
        import logging as _logging
        _logging.getLogger("niyamnetra.assess").error("Assessment commit IntegrityError: %s", exc)
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Assessment conflicts with existing data; retry")

    t_assess_end = time.perf_counter()
    from perf_baseline import pop_context_timing
    ctx_timings = pop_context_timing(scan.id)
    record_assess_timing({
        "scan_id": scan.id,
        "total_ms": round((t_assess_end - t_assess_start) * 1000, 2),
        "load_disk_ms": ctx_timings.get("load_disk_ms", 0.0),
        "quad_ms": ctx_timings.get("quad_ms", 0.0),
        "rectify_ms": ctx_timings.get("rectify_ms", 0.0),
        "ocr_ms": ctx_timings.get("ocr_ms", 0.0),
        "rules_ms": round((t_rules_end - t_rules_start) * 1000, 2),
    })
    append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id,
                 user_id=user.id, action="assessed", new_value=verdict.overall_result)

    assess_log.info(
        "[ASSESSMENT] assessment_completed inspection_id=%s scan_id=%s overall_result=%s duration_ms=%.2f",
        scan.inspection_id, scan.id, verdict.overall_result, round((t_assess_end - t_assess_start) * 1000, 2)
    )

    # Evidence retention (06 §9.5): images are kept for EVERY result.
    return _scan_out(db, scan)


def _scan_out(db: Session, scan: Scan) -> ScanOut:
    from rules_engine import ALL_CHECK_IDS
    rows = db.query(Finding).filter(Finding.scan_id == scan.id).all()
    order = {cid: i for i, cid in enumerate(ALL_CHECK_IDS)}
    rows.sort(key=lambda r: order.get(r.check_id, 99))
    counts = VerdictCounts(
        total=len(rows),
        passed=sum(1 for r in rows if r.effective_verdict == "pass"),
        failed=sum(1 for r in rows if r.effective_verdict == "fail"),
        not_assessed=sum(1 for r in rows if r.effective_verdict == "not_assessed"),
    )
    out = ScanOut.model_validate(scan)
    out.counts = counts
    out.batch_number = scan.batch_number
    out.net_quantity_value = scan.net_quantity_value
    out.net_quantity_unit = scan.net_quantity_unit

    # Enrich MRP if extracted from LLM structured data or findings
    mrp_val = None
    if scan.llm_structured_data:
        try:
            import json as _json
            _s_data = _json.loads(scan.llm_structured_data)
            _mrp_dict = _s_data.get("mrp") or {}
            if _mrp_dict.get("value") is not None:
                mrp_val = float(_mrp_dict["value"])
        except Exception:
            pass
    if mrp_val is None:
        mrp_f = next((r for r in rows if r.check_id == "CHK04"), None)
        if mrp_f and mrp_f.observed:
            import re as _re
            _m = _re.search(r"(?:₹|Rs\.?|INR)\s*(\d+(?:\.\d{1,2})?)", mrp_f.observed, _re.I)
            if _m:
                try:
                    mrp_val = float(_m.group(1))
                except Exception:
                    pass
    out.mrp = mrp_val

    # Enrich image URLs for mobile client:
    for img_out in out.images:
        img_out.url = f"/scans/{scan.id}/images/{img_out.id}"
        img_out.thumbnail_url = f"/scans/{scan.id}/images/{img_out.id}/thumbnail"
    out.diagnostics = {
        "assessment_id": f"assess-{scan.id}-{scan.rule_pack_version or '2026.09.v1'}",
        "assessment_input_fingerprint": scan.ocr_cache_hash,
        "ocr_provider": getattr(scan, "ocr_provider", None) or getattr(settings, "OCR_PROVIDER", "google_vision"),
        "ocr_status": "success" if scan.ocr_text else "no_text",
        "ocr_confidence_mean": scan.ocr_confidence_mean,
        "llm_provider": scan.llm_provider or getattr(settings, "LLM_PROVIDER", "groq"),
        "llm_model": scan.llm_model or getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile"),
        "llm_status": "success" if scan.llm_structured_data else "none",
        "llm_duration_ms": scan.llm_duration_ms,
        "rule_pack_version": scan.rule_pack_version or settings.RULE_PACK_VERSION,
        "catalog_rule_count": 26,
        "applicable_rule_count": scan.checks_total,
        "evaluated_rule_count": scan.checks_assessed,
        "overall_result": scan.overall_result,
    }
    return out


@router.get("", response_model=list[ScanListItemOut])
def list_scans(
    inspection_id: int | None = Query(default=None),
    store_id: int | None = Query(default=None),
    status: str | None = Query(default=None),
    q: str | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """List live packages scanned during inspections. Admins see all scans; inspectors see own."""
    from queries import scans_list
    return scans_list(
        db,
        inspection_id=inspection_id,
        store_id=store_id,
        status=status,
        q=q,
        limit=limit,
        offset=offset,
        user=user,
    )


@router.get("/{scan_id}", response_model=ScanOut)
def get_scan(scan: Scan = Depends(owned_scan), db: Session = Depends(get_db)):
    """Findings and counts for one scan. C4/C5 — the counts carry the denominator."""
    return _scan_out(db, scan)


@router.get("/{scan_id}/verify")
def verify_evidence(scan: Scan = Depends(owned_scan), db: Session = Depends(get_db)):
    """Rehash every stored file and report whether it still matches. C9.

    This endpoint is the reason store_upload() hashes what it wrote. Under
    v1.x every single image would report a mismatch here, because the hash was
    taken of the upload and the file on disk was a re-encoded derivative.
    """
    from image_processor import verify_stored_image
    from pathlib import Path

    results = []
    for img in db.query(ScanImage).filter(ScanImage.scan_id == scan.id).all():
        path = Path(img.file_path)
        exists = path.exists()
        matches = exists and verify_stored_image(path, img.sha256)
        results.append({
            "image_id": img.id,
            "panel": img.panel,
            "file_present": exists,
            "sha256_recorded": img.sha256,
            "sha256_matches": matches,
            "tamper_evident": matches,
            "similarity_status": img.similarity_status or "unique",
            "duplicate_of_image_id": img.duplicate_of_image_id,
            "hamming_distance": img.hamming_distance,
            "thumbnail_url": f"/scans/{scan.id}/images/{img.id}/thumbnail",
        })
    return {
        "scan_id": scan.id,
        "images": results,
        "all_intact": bool(results) and all(r["sha256_matches"] for r in results),
    }


@router.get("/{scan_id}/images/{image_id}/thumbnail")
def image_thumbnail(scan_id: int, image_id: int,
                    scan: Scan = Depends(owned_scan),
                    user: User = Depends(get_current_user),
                    db: Session = Depends(get_db)):
    """P1 fix: portal findings showed hashes only — judges could not eyeball.
    Serves a downscaled JPEG copy (max 512px) of the stored evidence; the
    original file is never resized. Auth via owned_scan (inspector sees own,
    admin sees all). Purged images → 404."""
    from fastapi.responses import Response
    from pathlib import Path
    import io
    img = db.query(ScanImage).filter(
        ScanImage.id == image_id, ScanImage.scan_id == scan.id).first()
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Image not found")
    p = Path(img.file_path)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="Image purged or missing")
    try:
        from audit import append_audit as _aa
        _aa(db, inspection_id=scan.inspection_id, scan_id=scan.id,
            user_id=user.id, action="evidence_viewed",
            new_value=f"{img.panel}:{img.sha256[:16]}")
    except Exception:
        pass
    try:
        from PIL import Image
        with Image.open(p) as im:
            im = im.convert("RGB")
            im.thumbnail((512, 512))
            buf = io.BytesIO()
            im.save(buf, format="JPEG", quality=72)
            return Response(content=buf.getvalue(), media_type="image/jpeg")
    except Exception:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Image could not be rendered")


@router.get("/{scan_id}/images/{image_id}")
def image_file(scan_id: int, image_id: int,
               scan: Scan = Depends(owned_scan),
               user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    """Serves the full stored evidence image for mobile evidence viewing.
    Original byte stream preserved without downscaling or alteration. Auth via owned_scan."""
    from fastapi.responses import FileResponse
    from pathlib import Path
    img = db.query(ScanImage).filter(
        ScanImage.id == image_id, ScanImage.scan_id == scan.id).first()
    if img is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Image not found")
    p = Path(img.file_path)
    if not p.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            detail="Image purged or missing")
    try:
        from audit import append_audit as _aa
        _aa(db, inspection_id=scan.inspection_id, scan_id=scan.id,
            user_id=user.id, action="evidence_viewed_full",
            new_value=f"{img.panel}:{img.sha256[:16]}")
    except Exception:
        pass
    media_type = "image/jpeg" if p.suffix.lower() in (".jpg", ".jpeg") else "image/png"
    return FileResponse(path=str(p), media_type=media_type)


class UpdateScanRequest(BaseModel):
    """Patch the operator-declared scope flags that decide CHK02/11/12/13/14. Nullable = unknown -> not_assessed, never pass.

    reference_pixel_size is the pixel length of the scale reference object
    (ID-1 card long edge / 5-INR coin diameter) when scale_source is not
    "declared". Threaded to compute_scale via getattr(scan, ...) in
    build_context; persisted on the row when the column exists, otherwise
    held transiently for the next assess.
    """
    commodity_generic: str | None = Field(default=None, max_length=120)
    brand_name: str | None = Field(default=None, max_length=120)
    commodity_category: str | None = Field(default=None, max_length=60)
    batch_number: str | None = Field(default=None, max_length=60)
    net_quantity_value: float | None = Field(default=None, gt=0)
    net_quantity_unit: str | None = Field(default=None, max_length=12)
    is_imported: bool | None = None
    is_perishable: bool | None = None
    is_medical_device: bool | None = None
    is_tobacco: bool | None = None
    has_sticker: bool | None = None
    sticker_reduces_price: bool | None = None
    sticker_covers_original: bool | None = None
    reference_pixel_size: float | None = Field(default=None, gt=0, le=100000)

    @field_validator("net_quantity_value", mode="after")
    @classmethod
    def _qty_finite_positive(cls, v):
        if v is None:
            return None
        if not math.isfinite(v) or v <= 0:
            raise ValueError("net_quantity_value must be finite and > 0")
        return v

    @field_validator("net_quantity_unit", mode="before")
    @classmethod
    def _unit_normalize(cls, v):
        if v is None:
            return None
        norm = str(v).strip().lower()
        if not norm:
            return None
        if norm not in ALLOWED_QUANTITY_UNITS:
            raise ValueError(f"net_quantity_unit must be one of {sorted(ALLOWED_QUANTITY_UNITS)}")
        return norm

    @model_validator(mode="after")
    def _sticker_consistency(self):
        if self.has_sticker is False and (
            self.sticker_reduces_price is not None or self.sticker_covers_original is not None
        ):
            raise ValueError("sticker_* must be None when has_sticker is False")
        return self


@router.patch("/{scan_id}")
def update_scan(body: UpdateScanRequest, scan: Scan = Depends(owned_scan), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Before assess: persist the 9 scope answers so re-assess is reproducible 06 §3.4.

    Any scope/geometry change stales the previous assessment: findings are
    deleted, overall_result reset to not_assessed with checks_assessed=0, and
    the staling is audited (scan_staled). The next assess rebuilds findings
    from the new inputs; without this the old verdict would masquerade as
    covering the new inputs.
    """
    if scan.inspection_id:
        insp = db.get(Inspection, scan.inspection_id)
        if insp and insp.status == "submitted":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Inspection submitted; scan frozen")
    _keys = body.model_dump(exclude_unset=True)
    for k, v in _keys.items():
        try:
            setattr(scan, k, v)  # all keys are columns (reference_pixel_size persisted since 0003)
        except AttributeError:
            # Unknown attr: keep on the instance for the next assess in this
            # process without failing the patch.
            object.__setattr__(scan, k, v)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Scan update conflicts with existing data")
    # Stale the prior assessment when inputs changed (skip if no findings).
    try:
        _old_findings = db.query(Finding).filter(Finding.scan_id == scan.id).all()
        if _old_findings:
            for _f in _old_findings:
                db.delete(_f)
            scan.overall_result = "not_assessed"
            scan.checks_assessed = 0
            scan.violation_limb = None
            scan.recommended_action = None
            db.commit()
            append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id, user_id=user.id,
                         action="scan_staled", old_value=f"{len(_old_findings)} finding(s)",
                         reason=f"inputs changed: {sorted(_keys)}")
    except IntegrityError:
        db.rollback()
    db.refresh(scan)
    append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id, user_id=user.id, action="scan_updated", new_value=str(sorted(_keys.keys())))
    return {"scan_id": scan.id, "updated": sorted(_keys.keys())}


class ListingRequest(BaseModel):
    url: str = Field(..., description="https:// E-commerce listing URL")
    platform_has_origin_filter: bool | None = Field(
        default=None,
        description="Officer-observed: marketplace offers country-of-origin filter (CHK16)")


@router.post("/{scan_id}/listing")
def attach_listing(body: ListingRequest, scan: Scan = Depends(owned_scan), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Fetch listing for CHK15/16 via SSRF-guarded fetcher. Persists the URL +
    stripped listing text on the scan so build_context can assess Rule 6(10)
    (previously the HTML was fetched then discarded → CHK15/16 dead)."""
    if scan.inspection_id:
        _li = db.get(Inspection, scan.inspection_id)
        if _li is not None and _li.status == "submitted":
            raise HTTPException(status.HTTP_409_CONFLICT,
                                detail="Inspection submitted; scan frozen")
    # Use listing_fetcher SSRF guard
    from listing_fetcher import fetch_listing
    url = body.url
    try:
        html = fetch_listing(url)
    except ValueError as e:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except Exception as e:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=f"Fetch failed: {type(e).__name__}")
    # Strip tags → visible text (first 20k chars) for declaration parsing.
    try:
        import re as _re
        _txt = _re.sub(r"<script[\s\S]*?</script>|<style[\s\S]*?</style>", " ", html, flags=_re.I)
        _txt = _re.sub(r"<[^>]+>", " ", _txt)
        _txt = _re.sub(r"\s+", " ", _txt).strip()[:20000]
    except Exception:
        _txt = html[:20000]
    try:
        scan.listing_url = url[:500]
    except Exception:
        pass
    try:
        scan.listing_text = _txt
    except Exception:
        pass
    # Optional platform flag from request body (checkbox in portal Capture).
    try:
        _pf = getattr(body, "platform_has_origin_filter", None)
        if _pf is not None:
            scan.platform_has_origin_filter = bool(_pf)
    except Exception:
        pass
    try:
        db.commit()
    except Exception:
        db.rollback()
    # Keep ocr_text for OCR only; do NOT append raw HTML. Record the URL +
    # a truncated marker in audit for traceability.
    marker = f"[LISTING {url[:200]} len={len(html)}]"
    append_audit(db, inspection_id=scan.inspection_id, scan_id=scan.id, user_id=user.id, action="listing_attached", new_value=f"{url[:200]} {marker}")
    return {"scan_id": scan.id, "listing_url": url, "fetched_chars": len(html)}

# ---------------------------------------------------------------------------
# OCR result cache helpers
# ---------------------------------------------------------------------------
# A re-assess of an unchanged image set (e.g. after a scope edit or a finding
# override) is the most common re-assess pattern. Each run previously re-paid
# the full cloud-OCR cost even when the images were identical. The cache key
# is a SHA-256 of the sorted (panel, sha256) pairs of the scan's images so
# adding or replacing any image invalidates it.

# Salts the OCR cache fingerprint. Bump this (ocr-v2, ocr-v3, ...) whenever the
# OCR cascade changes meaningfully: it invalidates every previously written
# scan.ocr_cache exactly once so no scan keeps serving stale text produced by
# a different engine (e.g. text-only LLM OCR with fabricated line boxes).
_OCR_CACHE_SALT = "ocr-v2"


def _lines_to_cache(ocr) -> str | None:
    """Serialize an OcrResult to a compact JSON string for scan.ocr_cache.

    Format: {"engine": str, "mean_confidence": float|null,
             "lines": [[text, confidence, [[x,y],...], height_px], ...]}
    Returns None if ocr has no lines (nothing worth caching).
    """
    import json
    if not ocr or not ocr.lines:
        return None
    try:
        serialised = [
            [
                getattr(ln, "text", "") or "",
                float(getattr(ln, "confidence", 0)) if getattr(ln, "confidence", None) is not None else None,
                [[round(float(x), 1), round(float(y), 1)] for x, y in (getattr(ln, "box", None) or [])],
                float(getattr(ln, "height_px", 0) or 0),
            ]
            for ln in ocr.lines
        ]
        return json.dumps({
            "engine": ocr.engine or "unknown",
            "mean_confidence": float(ocr.mean_confidence) if ocr.mean_confidence is not None else None,
            "lines": serialised,
        }, separators=(",", ":"))
    except Exception:
        return None


def _lines_from_cache(s: str | None):
    """Rehydrate an OcrResult from the JSON blob; returns None on any error."""
    import json
    if not s:
        return None
    try:
        from ocr_engine import OcrLine, OcrResult
        d = json.loads(s)
        lines = [
            OcrLine(text=ln[0], confidence=ln[1], box=ln[2] or None, height_px=ln[3])
            for ln in d.get("lines", [])
        ]
        if not lines:
            return None
        return OcrResult(
            lines=lines,
            engine=d.get("engine", "cache"),
            mean_confidence=d.get("mean_confidence"),
            failure_reason=None,
        )
    except Exception:
        return None


def build_context(db: Session, scan: Scan, inspection: Inspection):
    """Assemble everything the engine needs. One place, so a check never
    reaches into the database and so the engine is trivially testable with a
    hand-built context.

    OCR covers ALL panels, not just front: each stored image is OCR'd and the
    lines concatenated for field extraction, so an MRP on the side panel or a
    batch number on the back panel is still found. Scale/measurement still
    comes from the front panel only (Rule 7 governs the principal display).
    OCR text is persisted on scan.ocr_text (truncated 20k) with
    scan.ocr_confidence_mean for reports and the review queue.
    """
    from pathlib import Path
    import cv2

    from image_processor import assess_quality, compute_scale, detect_panel_quad, rectify
    from ocr_engine import OcrResult, extract_fields, run_ocr
    from rules_engine import CheckContext

    images = db.query(ScanImage).filter(ScanImage.scan_id == scan.id).all()
    ctx = CheckContext(
        transaction_type=inspection.transaction_type,
        commodity_generic=scan.commodity_generic,
        commodity_category=scan.commodity_category,
        net_quantity_value=scan.net_quantity_value,
        net_quantity_unit=scan.net_quantity_unit,
        is_imported=scan.is_imported,
        is_perishable=scan.is_perishable,
        is_medical_device=scan.is_medical_device,
        is_tobacco=scan.is_tobacco,
        has_sticker=scan.has_sticker,
        sticker_reduces_price=scan.sticker_reduces_price,
        sticker_covers_original=scan.sticker_covers_original,
        panel_shape=scan.panel_shape,
        panel_height_mm=scan.panel_height_mm,
        panel_width_mm=scan.panel_width_mm,
        panel_diameter_mm=scan.panel_diameter_mm,
        total_surface_area_cm2=scan.total_surface_area_cm2,
        is_blown_moulded=scan.is_blown_moulded,
        panels_captured={i.panel for i in images},
        rules_as_at=scan.rules_as_at or date.fromisoformat(settings.RULES_AS_AT),
    )

    front = next((i for i in images if i.panel in ("front", "principal")), None)
    if front is None or not Path(front.file_path).exists():
        ctx.image_usable = False
        ctx.image_quality_reason = (
            "No front-panel image is available for this scan, so nothing on the "
            "principal display panel can be read or measured."
        )
        return ctx

    import time
    t_disk_load_start = time.perf_counter()
    bgr = cv2.imread(front.file_path)
    t_disk_load_end = time.perf_counter()
    if bgr is None:
        # Missing/corrupt/purged file: mark unusable and return early instead
        # of crashing on assess_quality / bgr.shape (would be a 500).
        ctx.image_usable = False
        ctx.image_quality_reason = (
            "The front-panel image file is missing, corrupt, or was purged, "
            "so nothing on the principal display panel can be read or measured."
        )
        return ctx
    t_quality_start = time.perf_counter()
    quality = assess_quality(bgr)
    t_quality_end = time.perf_counter()
    ctx.image_usable, ctx.image_quality_reason = quality.usable, quality.reason

    # Rectification (Backend.md §image_processor): the stored file stays
    # byte-exact — this is a derived MEASUREMENT copy only. When the panel
    # quad is detected, the frame is warped onto a fronto-parallel plane so
    # mm-per-pixel and letter heights are not read across a tilted surface
    # (rectify used to be imported here and never called). Quality still
    # describes the RAW capture — a warped copy would score its own blur —
    # and when no quad is found or warping fails the raw frame is used
    # exactly as before, so behaviour only changes when a usable quad exists.
    _measure = bgr
    t_quad_start = time.perf_counter()
    t_quad_end = t_quad_start
    t_rectify_start = t_quad_start
    t_rectify_end = t_quad_start
    try:
        from image_processor import detect_candidate_quad, safe_rectify
        _quad, _geom_meta = detect_candidate_quad(bgr)
        t_quad_end = time.perf_counter()
        if _quad is not None:
            t_rectify_start = time.perf_counter()
            _rect, _tilt, _rect_meta = safe_rectify(bgr, _quad)
            t_rectify_end = time.perf_counter()
            if _rect_meta.get("rectification_status") == "applied" and getattr(_rect, "shape", (0,))[0] > 0:
                _measure = _rect
                front.rectified = True
                front.residual_tilt_deg = _tilt
                try:
                    from image_processor import prepare_derived_rectified_artifact
                    r_path, r_w, r_h = prepare_derived_rectified_artifact(Path(front.file_path), _rect)
                    front.rectified_file_path = str(r_path)
                except Exception:
                    pass
                try:
                    db.commit()
                except Exception:
                    db.rollback()
            else:
                front.rectified = False
                front.residual_tilt_deg = _tilt if _tilt is not None else None
                try:
                    db.commit()
                except Exception:
                    db.rollback()
        else:
            front.rectified = False
            front.residual_tilt_deg = None
            try:
                db.commit()
            except Exception:
                db.rollback()
    except Exception:
        _measure = bgr

    shape = getattr(_measure, "shape", None)
    if shape is None or len(shape) < 1:
        ctx.image_usable = False
        ctx.image_quality_reason = (
            "The front-panel image could not be decoded, so nothing on the "
            "principal display panel can be read or measured."
        )
        return ctx
    scale = compute_scale(
        panel_pixel_height=int(shape[0]),
        declared_panel_height_mm=scan.panel_height_mm,
        reference_pixel_size=getattr(scan, "reference_pixel_size", None),
        reference=scan.scale_source or "declared",
    )
    ctx.mm_per_pixel = scale.mm_per_pixel
    ctx.mm_per_pixel_uncertainty = scale.uncertainty
    ctx.scale_source = scale.source
    if scale.mm_per_pixel is None and ctx.image_quality_reason is None:
        ctx.image_quality_reason = scale.reason

    # Multi-panel OCR: run every stored panel concurrently, concat lines for field
    # extraction. Front-panel quality above still governs image_usable; OCR
    # availability is true if ANY panel yields text.
    t_ocr_start = time.perf_counter()
    _all_lines: list = []
    _engines: list[str] = []
    _confs: list[float] = []
    _fail_reason: str | None = None

    # --- OCR cache gate ---
    # Compute a fingerprint of the current image set. If it matches the last
    # cached run, rehydrate the OcrResult without touching any cloud API.
    import hashlib as _hashlib
    import json as _json
    _img_set_parts = sorted(
        (f"{_im.panel}:{_im.sha256}" for _im in images),
    )
    _set_hash = _hashlib.sha256(
        (_OCR_CACHE_SALT + "|" + "|".join(_img_set_parts)).encode()
    ).hexdigest()

    _cached_ocr = None
    if (
        getattr(scan, "ocr_cache_hash", None) == _set_hash
        and getattr(scan, "ocr_cache", None)
    ):
        _cached_ocr = _lines_from_cache(scan.ocr_cache)

    if _cached_ocr is not None:
        # Cache hit: use persisted OcrLines, skip all cloud OCR calls.
        import logging as _log
        _log.getLogger("niyamnetra.ocr_cache").debug(
            "OCR cache hit for scan %s (hash=%s)", scan.id, _set_hash[:12]
        )
        ocr = _cached_ocr
    else:
        # Cache miss: run OCR per panel using DERIVED ARTIFACTS ONLY.
        # MANDATORY INVARIANT (Section 2 & 3 & 28):
        # THE ORIGINAL EVIDENCE FILE MUST NEVER BE SENT TO CLOUD OCR.
        # If derived artifact is missing, fail safely with STATUS_INPUT_UNAVAILABLE.
        from image_processor import get_ocr_input_artifact
        from ocr.base import STATUS_INPUT_UNAVAILABLE, STATUS_NO_TEXT, STATUS_SUCCESS

        _panel_jobs = []
        for _im in images:
            art_path, art_type, meta = get_ocr_input_artifact(_im)
            if art_path is None or not art_path.exists():
                if _fail_reason is None:
                    _fail_reason = f"OCR input unavailable for panel {_im.panel}: missing derived artifact"
                continue
            _panel_jobs.append((_im, art_path, art_type, meta))

        _ocr_results = []
        if _panel_jobs:
            def _ocr_single(job):
                _img_rec, _p, _atype, _m = job
                import time
                import logging as _log
                _ocr_log = _log.getLogger("niyamnetra.ocr")
                _ocr_t0 = time.perf_counter()
                _ocr_log.info(
                    "[OCR] ocr_started provider=%s panel=%s image_id=%s input_artifact_type=%s",
                    getattr(settings, "OCR_PROVIDER", "google_vision"),
                    _img_rec.panel,
                    _img_rec.id,
                    _atype,
                )
                try:
                    _res = run_ocr(
                        _p,
                        panel=_img_rec.panel,
                        image_id=_img_rec.id,
                        scan_id=scan.id,
                        inspection_id=scan.inspection_id,
                    )
                except TypeError:
                    _res = run_ocr(_p)
                except Exception as _ocr_exc:
                    _ocr_dur = (time.perf_counter() - _ocr_t0) * 1000
                    _ocr_log.error(
                        "[OCR] ocr_failed provider=%s failure_reason=%s duration_ms=%.2f",
                        getattr(settings, "OCR_PROVIDER", "google_vision"),
                        str(_ocr_exc),
                        _ocr_dur,
                    )
                    raise

                _ocr_dur = (time.perf_counter() - _ocr_t0) * 1000
                if getattr(_res, "failure_reason", None) and not getattr(_res, "lines", None):
                    _ocr_log.warning(
                        "[OCR] ocr_failed provider=%s failure_reason=%s duration_ms=%.2f",
                        getattr(_res, "engine", "unknown"),
                        _res.failure_reason,
                        _ocr_dur,
                    )
                else:
                    _line_cnt = len(getattr(_res, "lines", []) or [])
                    _word_cnt = sum(len((l.text or "").split()) for l in (getattr(_res, "lines", []) or []))
                    _ocr_log.info(
                        "[OCR] ocr_completed provider=%s status=%s duration_ms=%.2f line_count=%d word_count=%d mean_confidence=%.2f",
                        getattr(_res, "engine", "unknown"),
                        getattr(_res, "status", "success"),
                        _ocr_dur,
                        _line_cnt,
                        _word_cnt,
                        float(getattr(_res, "mean_confidence", 0.0) or 0.0),
                    )

                if getattr(_res, "panel", None) is None:
                    _res.panel = _img_rec.panel
                if getattr(_res, "image_id", None) is None:
                    _res.image_id = _img_rec.id
                if getattr(_res, "scan_id", None) is None:
                    _res.scan_id = scan.id
                if getattr(_res, "inspection_id", None) is None:
                    _res.inspection_id = scan.inspection_id
                _res.input_artifact_type = _atype
                return _res

            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=min(4, len(_panel_jobs))) as _pool:
                _ocr_results = list(_pool.map(_ocr_single, _panel_jobs))

        for _ocr in _ocr_results:
            if _ocr.lines:
                _all_lines.extend(_ocr.lines)
                _engines.append(_ocr.engine)
            if _ocr.mean_confidence is not None:
                _confs.append(float(_ocr.mean_confidence))
            if _ocr.failure_reason and _fail_reason is None:
                _fail_reason = _ocr.failure_reason

        if _all_lines:
            _mean = float(sum(_confs) / len(_confs)) if _confs else None
            ocr = OcrResult(
                lines=_all_lines,
                engine=_engines[0] if _engines else "unknown",
                mean_confidence=_mean,
                failure_reason=None,
                status=STATUS_SUCCESS,
                scan_id=scan.id,
                inspection_id=scan.inspection_id,
            )
        else:
            _status = STATUS_NO_TEXT if _panel_jobs else STATUS_INPUT_UNAVAILABLE
            ocr = OcrResult(
                lines=[],
                engine=_engines[0] if _engines else "none",
                mean_confidence=None,
                failure_reason=_fail_reason or "No text detected across panels",
                status=_status,
                scan_id=scan.id,
                inspection_id=scan.inspection_id,
            )

        # Stage the cache on the scan instance; _assess_inner commits it.
        try:
            scan.ocr_cache_hash = _set_hash
            _cached_blob = _lines_to_cache(ocr)
            if _cached_blob:
                scan.ocr_cache = _cached_blob
        except Exception:
            pass
    t_ocr_end = time.perf_counter()

    from perf_baseline import record_context_timing
    record_context_timing(scan.id, {
        "load_disk_ms": round((t_disk_load_end - t_disk_load_start) * 1000, 2),
        "quality_ms": round((t_quality_end - t_quality_start) * 1000, 2),
        "quad_ms": round((t_quad_end - t_quad_start) * 1000, 2),
        "rectify_ms": round((t_rectify_end - t_rectify_start) * 1000, 2),
        "ocr_ms": round((t_ocr_end - t_ocr_start) * 1000, 2),
    })

    ctx.ocr_available = ocr.engine != "none"
    if not ctx.ocr_available:
        ctx.ocr_failure_reason = ocr.failure_reason
    elif _fail_reason and not _all_lines:
        ctx.ocr_failure_reason = _fail_reason
    ctx.ocr_mean_confidence = ocr.mean_confidence

    # Phase 3: Cloud LLM Structured Declaration Extraction
    # OCR READS. LLM STRUCTURES. RULES DECIDE. (Sections 2, 4, 6, 25)
    from llm import LLM_STATUS_SUCCESS, structure_inspection_ocr
    import time
    import logging as _log
    _llm_log = _log.getLogger("niyamnetra.llm")
    _llm_prov = getattr(settings, "LLM_PROVIDER", "groq")
    _llm_mod = getattr(settings, "GROQ_MODEL", "llama-3.3-70b-versatile")
    _llm_t0 = time.perf_counter()
    _llm_log.info(
        "[LLM] llm_started provider=%s model=%s inspection_id=%s scan_id=%s",
        _llm_prov, _llm_mod, scan.inspection_id, scan.id
    )
    _llm_res = None
    try:
        _input_panels = _ocr_results if _ocr_results else [ocr]
        _llm_res = structure_inspection_ocr(
            _input_panels,
            evidence_meta={"scan_id": scan.id, "inspection_id": scan.inspection_id},
        )
        ctx.llm_result = _llm_res
        _llm_dur = (time.perf_counter() - _llm_t0) * 1000
        if _llm_res and _llm_res.metadata.get("status") == LLM_STATUS_SUCCESS:
            ctx.fields = _llm_res.to_extracted_fields()
            scan.llm_structured_data = _json.dumps(_llm_res.to_dict())
            scan.llm_provider = _llm_res.metadata.get("llm_provider", _llm_prov)
            scan.llm_model = _llm_res.metadata.get("llm_model", _llm_mod)
            scan.llm_duration_ms = _llm_res.metadata.get("llm_duration_ms", _llm_dur)
            scan.llm_cache_hash = _llm_res.metadata.get("evidence_fingerprint")
            _extracted_cnt = sum(1 for v in ctx.fields.values() if getattr(v, "found", False))
            _llm_log.info(
                "[LLM] llm_completed provider=%s model=%s status=%s duration_ms=%.2f schema_valid=True cache_hit=%s extracted_field_count=%d",
                scan.llm_provider, scan.llm_model, _llm_res.metadata.get("status"),
                _llm_dur, _llm_res.metadata.get("cache_hit", False), _extracted_cnt
            )
        else:
            _llm_log.warning(
                "[LLM] llm_failed provider=%s model=%s failure_reason=%s fallback_used=True",
                _llm_prov, _llm_mod, _llm_res.metadata.get("error") if _llm_res else "unsuccessful_status"
            )
            ctx.fields = extract_fields(ocr)
    except Exception as _llm_err:
        _llm_log.warning(
            "[LLM] llm_failed provider=%s model=%s failure_reason=%s fallback_used=True",
            _llm_prov, _llm_mod, str(_llm_err)
        )
        ctx.fields = extract_fields(ocr)

    # Populate scan metadata from extracted fields if missing / placeholder
    _comm_val = ctx.fields.get("commodity")
    if _comm_val and _comm_val.found and _comm_val.value:
        if not scan.commodity_generic or scan.commodity_generic.strip().lower() in ("unspecified commodity", "unspecified", "sample"):
            scan.commodity_generic = _comm_val.value.strip()
            ctx.commodity_generic = scan.commodity_generic

    _batch_val = ctx.fields.get("batch_number")
    if _batch_val and _batch_val.found and _batch_val.value:
        if not scan.batch_number or scan.batch_number.strip().lower() in ("n/a", "none", ""):
            scan.batch_number = _batch_val.value.strip()

    if not scan.brand_name or scan.brand_name.strip().lower() in ("unspecified brand", "unspecified", "package"):
        if _llm_res and getattr(_llm_res, "parties", None):
            for p in _llm_res.parties:
                if getattr(p, "name", None) and getattr(p, "status", None) == "confirmed":
                    scan.brand_name = p.name.strip()
                    break
        if not scan.brand_name or scan.brand_name.strip().lower() in ("unspecified brand", "unspecified", "package"):
            _mfr_val = ctx.fields.get("manufacturer")
            if _mfr_val and _mfr_val.found and _mfr_val.value:
                scan.brand_name = _mfr_val.value.split(",")[0].split("\n")[0].strip()

    # Populate net quantity on scan and ctx if missing / placeholder
    if not scan.net_quantity_value:
        if _llm_res and getattr(_llm_res, "net_quantity", None) and getattr(_llm_res.net_quantity, "value", None) is not None:
            try:
                scan.net_quantity_value = float(_llm_res.net_quantity.value)
                scan.net_quantity_unit = _llm_res.net_quantity.normalized_unit or _llm_res.net_quantity.unit or "g"
                ctx.net_quantity_value = scan.net_quantity_value
                ctx.net_quantity_unit = scan.net_quantity_unit
            except Exception:
                pass
        else:
            _nq_val = ctx.fields.get("net_quantity")
            if _nq_val and _nq_val.found and _nq_val.value:
                import re as _re
                _m = _re.search(r"(\d+(?:\.\d+)?)\s*([a-zA-Z]+)", str(_nq_val.value))
                if _m:
                    try:
                        scan.net_quantity_value = float(_m.group(1))
                        scan.net_quantity_unit = _m.group(2).lower()
                        ctx.net_quantity_value = scan.net_quantity_value
                        ctx.net_quantity_unit = scan.net_quantity_unit
                    except Exception:
                        pass
    try:
        ctx.ocr_full_text = ocr.full_text  # consumed by assess for scan.ocr_text persistence
    except Exception:
        pass
    # Persist OCR evidence on the scan row (staged; committed by assess).
    try:
        if ocr.full_text:
            scan.ocr_text = ocr.full_text[:20000]
        scan.ocr_confidence_mean = ocr.mean_confidence
    except Exception:
        pass

    # Text-only LLM engines (gemini/groq) transcribe words but return NO real
    # line geometry — every "box" is fabricated. Feeding those into the Rule
    # 7/9 letter-height / clear-space / width measurements would invent
    # millimetre verdicts. Keep measurements empty for such engines so the
    # geometry-dependent checks honestly resolve to not_assessed, while the
    # content checks (MRP, net qty, manufacturer, dates…) still assess from
    # the transcribed text.
    _TEXT_ONLY_OCR_ENGINES = {"gemini_vision", "groq_vision"}
    _has_real_geometry = ocr.engine not in _TEXT_ONLY_OCR_ENGINES

    if scale.mm_per_pixel and _has_real_geometry:
        ctx.measured_heights_mm = {
            _label_for(line): line.height_px * scale.mm_per_pixel
            for line in ocr.lines
            if _label_for(line)
        }
        # P0 fix: CHK07/CHK09/CHK08 were permanently not_assessed because these
        # inputs were never built. Derive widths from OCR box x-extent (same
        # scale), clear-space from neighbouring line gaps, contrast from panel.
        try:
            widths: dict[str, float] = {}
            for line in ocr.lines:
                label = _label_for(line)
                if not label or label in widths:
                    continue
                xs = [p[0] for p in (line.box or []) if len(p) >= 2]
                if len(xs) >= 2:
                    widths[label] = (max(xs) - min(xs)) * scale.mm_per_pixel
                elif line.text:
                    # No box (plain-text fallback): width ≈ 0.6×height per char.
                    widths[label] = len(line.text.strip()) * line.height_px * 0.6 * scale.mm_per_pixel
            ctx.measured_widths_mm = widths
        except Exception:
            pass
        try:
            # Clear space: vertical gap between net-qty line and nearest lines
            # above/below in pixel space → mm. Left/right approximated from
            # horizontal margins of the net-qty box within the frame.
            from image_processor import estimate_contrast_ratio
            nq = [l for l in ocr.lines if _label_for(l) == "net_quantity"]
            if nq:
                import numpy as np
                ref = max(nq, key=lambda l: len(l.text or ""))
                ry = [p[1] for p in (ref.box or []) if len(p) >= 2]
                rx = [p[0] for p in (ref.box or []) if len(p) >= 2]
                if ry and rx:
                    ryc, rxc = (min(ry) + max(ry)) / 2, (min(rx) + max(rx)) / 2
                    above = [max([p[1] for p in l.box if len(p) >= 2])
                             for l in ocr.lines if l is not ref and l.box and len(l.box[0]) >= 2
                             and max([p[1] for p in l.box if len(p) >= 2]) < min(ry)]
                    below = [min([p[1] for p in l.box if len(p) >= 2])
                             for l in ocr.lines if l is not ref and l.box and len(l.box[0]) >= 2
                             and min([p[1] for p in l.box if len(p) >= 2]) > max(ry)]
                    h, w = bgr.shape[:2]
                    cs: dict[str, float] = {}
                    if above:
                        cs["above"] = (min(ry) - max(above)) * scale.mm_per_pixel
                    if below:
                        cs["below"] = (min(below) - max(ry)) * scale.mm_per_pixel
                    cs["left"] = min(rx) * scale.mm_per_pixel
                    cs["right"] = (w - max(rx)) * scale.mm_per_pixel
                    ctx.clear_space_mm = {k: max(0.0, float(v)) for k, v in cs.items()}
            try:
                ctx.contrast_ratio = estimate_contrast_ratio(bgr)
            except Exception:
                pass
        except Exception:
            pass

    # P0 fix: CHK15/CHK16 were permanently not_assessed because listing fields
    # were fetched then discarded. Re-parse persisted listing text (stored by
    # POST /scans/{id}/listing) into the same extract_fields shape the engine
    # reads, so e-commerce scans actually assess Rule 6(10).
    try:
        listing_text = getattr(scan, "listing_text", None)
        listing_url = getattr(scan, "listing_url", None)
        if listing_text:
            from ocr_engine import OcrLine, OcrResult as _OR, extract_fields as _ef
            _lr = _OR(lines=[OcrLine(text=listing_text[:20000], confidence=None,
                                     box=[[0.0, 0.0]], height_px=0.0)],
                      engine="listing", mean_confidence=None)
            _lf = _ef(_lr)
            ctx.listing_available = True
            ctx.listing_fields = {k: (v.value or "") for k, v in _lf.items() if v.found}
            # platform filter flag persisted on scan when officer answers it
            pf = getattr(scan, "platform_has_origin_filter", None)
            if pf is not None:
                ctx.platform_has_origin_filter = bool(pf)
        elif listing_url:
            ctx.listing_available = True
            ctx.listing_fields = {}
    except Exception:
        pass
    return ctx

