"""routers/inspections.py — visits, scope, geofence, and the scans under them.

Not written out verbatim in Backend.md §8; synthesised to the endpoint
inventory (§8.2) using only the schemas, models, queries, and rbac
dependencies that section 8 does define.
"""
from datetime import date, datetime, timezone
from math import asin, cos, radians, sin, sqrt

from cachetools import TTLCache
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from audit import append_audit
from config import settings
from database import get_db
from models import Inspection, Scan, Store, User
from rbac import get_current_user, owned_inspection, require_admin, require_inspector
from schemas import CreateInspectionRequest, CreateScanRequest, SubmitInspectionRequest, UpdateInspectionRequest
from pydantic import BaseModel, Field

router = APIRouter(tags=["inspections"])

# /stores changes rarely and is read on every app launch; a short TTL keeps it
# fresh without a query per launch. Plain dicts are cached, never ORM rows, so
# nothing detached from a closed session leaks into a later request.
# Thread-safe: TTLCache is not locked internally, so all access goes through
# _STORE_LOCK (uvicorn threads share the process cache).
import threading as _threading
_STORE_LOCK = _threading.Lock()
_STORE_CACHE: TTLCache = TTLCache(maxsize=1, ttl=300)

# Transaction types that put a package inside Chapter II's retail-sale ambit.
# The authoritative, per-package scope test is CHK03 in the engine; this is the
# coarse inspection-level flag, recorded from what the officer selected.
# packed_in_presence is NOT retail (CHK03 OUT_OF_SCOPE_TRANSACTIONS + docs):
# it was wrongly listed here, marking made-in-presence visits in_scope.
_RETAIL_TYPES = {"retail_sale"}


def _haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6_371_000.0
    p1, p2 = radians(lat1), radians(lat2)
    dphi, dlmb = radians(lat2 - lat1), radians(lon2 - lon1)
    a = sin(dphi / 2) ** 2 + cos(p1) * cos(p2) * sin(dlmb / 2) ** 2
    return 2 * r * asin(sqrt(a))


def _store_dict(s: Store) -> dict:
    return {
        "id": s.id, "name": s.name, "store_type": s.store_type,
        "address": s.address, "city": s.city, "district": s.district,
        "state": s.state, "pincode": s.pincode,
        "latitude": s.latitude, "longitude": s.longitude,
        "geofence_radius_m": s.geofence_radius_m,
    }


def _inspection_dict(insp: Inspection) -> dict:
    # P1 fix: App Pass/Violations/Records filtered on result/overall_result/
    # verdict fields the list never returned → permanently empty tabs.
    # Roll up live scans (duplicate_of IS NULL): any violation → violation,
    # all compliant → compliant, any not_assessed → not_assessed, no scans →
    # no_scans (client treats as empty, not as pass). edited_offline +
    # store_name included so the review queue + search work client-side too.
    try:
        live = [s for s in (insp.scans or []) if getattr(s, "duplicate_of", None) is None]
    except Exception:
        live = []
    results = [getattr(s, "overall_result", None) for s in live]
    if not live:
        # A refused inspection (merchant non-cooperation, zero scans) gets its
        # own rollup value so clients can filter/bucket it separately from
        # genuine 'no_scans' visits (inspector arrived but did not scan yet).
        if getattr(insp, "signature_status", None) == "refused":
            rollup = "refused"
        else:
            rollup = "no_scans"
    elif any(r == "violation" for r in results):
        rollup = "violation"
    elif any(r == "not_assessed" for r in results):
        rollup = "not_assessed"
    elif all(r == "compliant" for r in results):
        rollup = "compliant"
    elif any(r == "out_of_scope" for r in results) and all(
            r in ("out_of_scope", "compliant") for r in results):
        rollup = "out_of_scope"
    else:
        rollup = "not_assessed"
    try:
        store_name = insp.store.name if getattr(insp, "store", None) else None
        store_city = insp.store.city if getattr(insp, "store", None) else None
        store_address = insp.store.address if getattr(insp, "store", None) else None
        store_district = insp.store.district if getattr(insp, "store", None) else None
        store_state = insp.store.state if getattr(insp, "store", None) else None
        store_pincode = insp.store.pincode if getattr(insp, "store", None) else None
    except Exception:
        store_name, store_city, store_address, store_district, store_state, store_pincode = None, None, None, None, None, None
    try:
        inspector_name = insp.inspector.full_name if getattr(insp, "inspector", None) else None
        inspector_employee_id = insp.inspector.employee_id if getattr(insp, "inspector", None) else None
    except Exception:
        inspector_name, inspector_employee_id = None, None
    # Check aggregates across live scans (App reads passed/pass_count,
    # checks/checks_total; Portal reads counts). Evidence thumbs: first image
    # per live scan so carousels render without extra round-trips.
    try:
        _passed = sum(int(getattr(s, "checks_assessed", 0) or 0)
                      for s in live if getattr(s, "overall_result", None) == "compliant")
        _total = sum(int(getattr(s, "checks_total", 0) or 0) for s in live)
        _thumbs = []
        _all_findings = []
        _commodity_name = None
        for s in live:
            if not _commodity_name and (getattr(s, "commodity_generic", None) or getattr(s, "brand_name", None)):
                _commodity_name = f"{getattr(s, 'brand_name', '') or ''} {getattr(s, 'commodity_generic', '') or ''}".strip()
            for f in (getattr(s, "findings", []) or []):
                _all_findings.append({
                    "id": f.id,
                    "scan_id": s.id,
                    "check_id": f.check_id,
                    "code": f.check_id,
                    "title": f.title,
                    "name": f.title,
                    "engine_verdict": f.engine_verdict,
                    "human_verdict": f.human_verdict,
                    "effective_verdict": f.effective_verdict,
                    "verdict": f.effective_verdict,
                    "result": f.effective_verdict,
                    "checkVerdict": f.effective_verdict,
                    "severity": f.severity,
                    "reason": f.reason,
                    "observed": f.observed,
                    "required": f.required,
                    "citation": f.citation,
                    "override_reason": f.override_reason,
                })
            try:
                _imgs = sorted(getattr(s, "images", []) or [], key=lambda i: getattr(i, "id", 0))
                if _imgs:
                    _thumbs.append(f"/scans/{s.id}/images/{_imgs[0].id}/thumbnail")
            except Exception:
                continue
        _failed_findings = [f for f in _all_findings if f["effective_verdict"] == "fail"]
        _violated_packages = sum(1 for r in results if r == "violation")
        _violation_count = len(_failed_findings) if len(_failed_findings) > 0 else _violated_packages
    except Exception:
        _passed, _total, _thumbs, _all_findings, _failed_findings = 0, 0, [], [], []
        _commodity_name = None
        _violation_count = sum(1 for r in results if r == "violation")
    return {
        "id": insp.id, "store_id": insp.store_id, "user_id": insp.user_id,
        "store_name": store_name,
        "store_city": store_city,
        "store_address": store_address,
        "store_district": store_district,
        "store_state": store_state,
        "store_pincode": store_pincode,
        "inspector_name": inspector_name,
        "inspector_employee_id": inspector_employee_id,
        "inspection_date": insp.inspection_date.isoformat(),
        "status": insp.status,
        "display_status": "Submitted" if insp.status == "submitted" else "In Progress",
        "sync_status": "Synced",
        "transaction_type": insp.transaction_type,
        "in_scope": insp.in_scope, "out_of_scope_reason": insp.out_of_scope_reason,
        "geofence_status": insp.geofence_status,
        "geofence_distance_m": insp.geofence_distance_m,
        "geofence_reason": insp.geofence_reason,
        "mock_location": insp.mock_location,
        "clock_skew_seconds": insp.clock_skew_seconds,
        "edited_offline": bool(getattr(insp, "edited_offline", False)),
        # Coordinates exposed so a map view can plot visits (stores carry
        # their registered point via /stores; this is the device fix).
        "latitude": insp.latitude, "longitude": insp.longitude,
        "gps_accuracy_m": insp.gps_accuracy_m,
        "signature_status": insp.signature_status,
        "notes": insp.notes,
        "submitted_at": insp.submitted_at.isoformat() if insp.submitted_at else None,
        "scan_count": len(insp.scans),
        "scanned_count": len(live),
        "total_products": len(live),
        "violation_products": sum(1 for r in results if r == "violation"),
        "compliant_products": sum(1 for r in results if r == "compliant"),
        "scanned_at": (
            max((s.created_at for s in live if getattr(s, "created_at", None)), default=None).isoformat()
            if any(getattr(s, "created_at", None) for s in live)
            else (insp.submitted_at.isoformat() if insp.submitted_at else (insp.inspection_date.isoformat() if insp.inspection_date else None))
        ),
        # Commodity & Violation details for cards
        "commodity": _commodity_name,
        "commodity_generic": _commodity_name,
        "violated": _violation_count,
        "violation_count": _violation_count,
        "failed_checks_count": len(_failed_findings),
        "findings": _all_findings,
        # Rollup aliases — every name the clients filter on resolves.
        "overall_result": rollup, "result": rollup, "verdict": rollup,
        "is_refusal": rollup == "refused",
        "local_created_at": insp.inspection_date.isoformat() if insp.inspection_date else None,
        "date": insp.inspection_date.isoformat() if insp.inspection_date else None,
        "result_counts": {
            "compliant": sum(1 for r in results if r == "compliant"),
            "violation": sum(1 for r in results if r == "violation"),
            "not_assessed": sum(1 for r in results if r == "not_assessed"),
            "out_of_scope": sum(1 for r in results if r == "out_of_scope"),
        },
        # Back-compat aggregates for App Pass/Violations/Records screens.
        "passed": _passed, "pass_count": _passed, "checks_passed": _passed,
        "checks": _total, "checks_total": _total,
        "evidence_thumbnails": _thumbs, "evidence_uris": _thumbs,
        "photos": _thumbs, "images": _thumbs,
        "scans": [
            {
                "id": s.id,
                "commodity_generic": s.commodity_generic,
                "brand_name": s.brand_name,
                "overall_result": s.overall_result,
                "checks_total": s.checks_total,
                "checks_assessed": s.checks_assessed,
                "duplicate_of": s.duplicate_of,
                "created_at": s.created_at.isoformat() if s.created_at else None,
                "images": [{"id": img.id, "panel": img.panel} for img in (getattr(s, "images", []) or [])],
                "image_count": len(getattr(s, "images", []) or []),
                "findings": [
                    {
                        "id": f.id,
                        "check_id": f.check_id,
                        "code": f.check_id,
                        "title": f.title,
                        "name": f.title,
                        "effective_verdict": f.effective_verdict,
                        "engine_verdict": f.engine_verdict,
                        "human_verdict": f.human_verdict,
                        "verdict": f.effective_verdict,
                        "result": f.effective_verdict,
                        "checkVerdict": f.effective_verdict,
                        "severity": f.severity,
                        "observed": f.observed,
                        "required": f.required,
                        "citation": f.citation,
                        "reason": f.reason,
                        "override_reason": f.override_reason,
                    }
                    for f in (getattr(s, "findings", []) or [])
                ],
            }
            for s in live
        ],
    }
class CreateStoreRequest(BaseModel):
    name: str = Field(min_length=2, max_length=160)
    store_type: str | None = Field(default=None, max_length=40)
    address: str | None = None
    city: str | None = None
    district: str | None = None
    state: str | None = None
    pincode: str | None = Field(default=None, max_length=10)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    geofence_radius_m: int = Field(default=150, ge=10, le=2000)


@router.get("/stores")
def list_stores(user: User = Depends(require_inspector), db: Session = Depends(get_db)):
    """Active stores, cached five minutes (settings-independent, deliberate)."""
    with _STORE_LOCK:
        cached = _STORE_CACHE.get("all")
        if cached is not None:
            return cached
    rows = db.query(Store).filter(Store.is_active.is_(True)).order_by(Store.name).all()
    data = [_store_dict(s) for s in rows]
    with _STORE_LOCK:
        _STORE_CACHE["all"] = data
    return data


@router.post("/stores", status_code=status.HTTP_201_CREATED)
def create_store(body: CreateStoreRequest, user: User = Depends(require_inspector), db: Session = Depends(get_db)):
    """Inspector or Admin creates a new store. Invalidates the 5-min cache so inspectors see it."""
    from sqlalchemy.exc import IntegrityError
    existing = db.query(Store).filter(Store.name == body.name).first()
    if existing:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Store name already exists")
    s = Store(
        name=body.name, store_type=body.store_type, address=body.address,
        city=body.city, district=body.district, state=body.state, pincode=body.pincode,
        latitude=body.latitude, longitude=body.longitude, geofence_radius_m=body.geofence_radius_m,
    )
    db.add(s)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Store already exists")
    db.refresh(s)
    with _STORE_LOCK:
        _STORE_CACHE.clear()
    append_audit(db, user_id=user.id, action="store_created", new_value=f"{s.id}:{s.name}")
    return _store_dict(s)


@router.post("/inspections", status_code=status.HTTP_201_CREATED)
def create_inspection(body: CreateInspectionRequest, request: Request,
                      user: User = Depends(require_inspector),
                      db: Session = Depends(get_db)):
    from fastapi.responses import JSONResponse
    from idempotency import close_idempotent, idempotency_key, replay_or_open

    # Durable idempotent replay (09 T14, 11 §2.4). The App reuses the queue
    # item id as the key across sync retries; a retry whose first response was
    # lost on the wire replays the stored 201 instead of creating a duplicate.
    _idem = idempotency_key(request)
    _stored, _is_new = replay_or_open(db, request, user.id, _idem)
    if not _is_new:
        _sc, _body = _stored
        return JSONResponse(status_code=_sc, content=_body)

    # 11 §2.1 — refuse at gate if evidence disk low, not mid-capture.
    # Disk errors map to 503 (not 500): the store is unavailable, not the code.
    try:
        _free = _evidence_free_gb()
    except Exception:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="Evidence store unavailable; retry shortly")
    if _free < settings.EVIDENCE_MIN_FREE_GB:
        raise HTTPException(status.HTTP_507_INSUFFICIENT_STORAGE,
                            detail=f"Evidence store low: {_free:.1f}GB free < {settings.EVIDENCE_MIN_FREE_GB}GB floor. Sync or free space.")
    store = db.get(Store, body.store_id)
    if store is None or not store.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Store not found")

    in_scope = body.transaction_type in _RETAIL_TYPES
    oos_reason = None if in_scope else (
        f"Transaction type '{body.transaction_type}' is not a retail sale; "
        "Chapter II of the Packaged Commodities Rules does not apply at the "
        "inspection level. Per-package applicability is still recorded by CHK03."
    )

    # Geofence: distance from the store's registered point against its radius.
    # Unknown when either side lacks coordinates — never silently 'inside'.
    # Mock locations can never attest presence: force unknown. Fixes with
    # accuracy worse than 100 m are too coarse to place the officer: unknown.
    g_status, g_dist, g_reason = "unknown", None, None
    if body.mock_location:
        g_reason = "Mock location reported by the device; presence cannot be attested."
    elif body.gps_accuracy_m is not None and body.gps_accuracy_m > 100:
        g_reason = (f"GPS accuracy {body.gps_accuracy_m:g} m exceeds the 100 m "
                    "threshold; presence cannot be attested.")
    elif (body.latitude is not None and body.longitude is not None
            and store.latitude is not None and store.longitude is not None):
        g_dist = _haversine_m(body.latitude, body.longitude,
                              store.latitude, store.longitude)
        radius = store.geofence_radius_m or 150
        if g_dist <= radius:
            g_status = "inside"
        else:
            g_status = "outside"
            g_reason = (f"Recorded {g_dist:.0f} m from the store point; outside "
                        f"the {radius} m geofence.")
    else:
        g_reason = "No device location fix, or the store has no coordinates."

    now = datetime.now(timezone.utc)
    skew = None
    edited_offline = False
    skew_note: str | None = None
    if body.local_created_at is not None:
        lc = body.local_created_at
        if lc.tzinfo is None:
            lc = lc.replace(tzinfo=timezone.utc)
        skew = int((now - lc).total_seconds())
        # Future device clock (large negative skew): don't reject — flag as
        # edited-offline with an audit note so the review queue sees it.
        if skew is not None and skew < -300:
            edited_offline = True
            skew_note = f"Device clock {abs(skew)}s ahead of server; flagged for review."

    insp = Inspection(
        user_id=user.id, store_id=store.id, inspection_date=date.today(),
        status="draft", transaction_type=body.transaction_type,
        in_scope=in_scope, out_of_scope_reason=oos_reason,
        latitude=body.latitude, longitude=body.longitude,
        gps_accuracy_m=body.gps_accuracy_m, geofence_status=g_status,
        geofence_distance_m=g_dist, geofence_reason=g_reason,
        mock_location=body.mock_location, local_created_at=body.local_created_at,
        synced_at=now, clock_skew_seconds=skew, edited_offline=edited_offline,
        notes=body.notes,
    )
    db.add(insp)
    try:
        db.commit()
    except Exception:
        from sqlalchemy.exc import IntegrityError as _IE
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Inspection conflicts with existing data; retry")
    db.refresh(insp)
    append_audit(db, inspection_id=insp.id, user_id=user.id,
                 action="inspection_created", new_value=f"store={store.id}",
                 reason=skew_note)
    _out = _inspection_dict(insp)
    close_idempotent(db, request, user.id, _idem, status.HTTP_201_CREATED, _out)
    return _out


def _evidence_free_gb() -> float:
    import shutil
    try:
        return shutil.disk_usage(settings.EVIDENCE_DIR).free / (1024 ** 3)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("evidence disk check failed: %s", e)
        raise


@router.get("/inspections")
def list_inspections(
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
    store_id: int | None = None,
    status: str | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    q: str | None = None,
    category: str | None = Query(default=None, max_length=60),
    area: str | None = Query(default=None),
):
    """Filters per 04_PRD §5.9 — store, status, date range, free-text q,
    category, area.

    q matches shop name AND commodity/brand/batch (was shop-only → Portal
    showed "Shop name only"). category filters scans by commodity_category
    (e.g. food, beverage). Scans joined with LEFT OUTER so inspections
    without scans still list; rows deduped by id in Python because the join
    fans out one row per scan (dialect-safe, no DISTINCT + eager-load clash).
    """
    from sqlalchemy import or_
    from sqlalchemy.orm import joinedload, selectinload
    if status is not None and status not in ("draft", "submitted"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="status must be draft or submitted")
    query = db.query(Inspection).options(
        selectinload(Inspection.scans).selectinload(Scan.images),
        selectinload(Inspection.scans).selectinload(Scan.findings),
        joinedload(Inspection.store),
        joinedload(Inspection.inspector),
    )
    if user.role != "admin":
        query = query.filter(Inspection.user_id == user.id)
    if store_id is not None:
        query = query.filter(Inspection.store_id == store_id)
    if status is not None:
        query = query.filter(Inspection.status == status)
    if date_from is not None:
        query = query.filter(Inspection.inspection_date >= date_from)
    if date_to is not None:
        query = query.filter(Inspection.inspection_date <= date_to)
    if area and area.strip().lower() != "all":
        _area_esc = area.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        query = query.join(Store, Store.id == Inspection.store_id).filter(or_(
            Store.city.ilike(f"%{_area_esc}%", escape="\\"),
            Store.district.ilike(f"%{_area_esc}%", escape="\\"),
        ))
    if q:
        # Escape LIKE wildcards so %/_ in user input match literally; cap 100.
        _qq = (q or "")[:100]
        _esc = _qq.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        # Shop name + commodity/brand/batch via LEFT OUTER join to scans.
        if not (area and area.strip().lower() != "all"):
            query = query.outerjoin(Store, Store.id == Inspection.store_id)
        query = (query.outerjoin(Scan, Scan.inspection_id == Inspection.id)
                 .filter(or_(
                     Store.name.ilike(f"%{_esc}%", escape="\\"),
                     Scan.commodity_generic.ilike(f"%{_esc}%", escape="\\"),
                     Scan.brand_name.ilike(f"%{_esc}%", escape="\\"),
                     Scan.batch_number.ilike(f"%{_esc}%", escape="\\"),
                 )))
    if category:
        # Exact category match (case-insensitive) on the scan's declared
        # commodity_category. Joins scans once even when q already did.
        _cat = (category or "").strip()[:60]
        _cesc = _cat.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        if not q:
            query = query.outerjoin(Scan, Scan.inspection_id == Inspection.id)
        query = query.filter(Scan.commodity_category.ilike(_cesc, escape="\\"))
    rows = query.order_by(Inspection.inspection_date.desc(), Inspection.id.desc()).all()
    # Dedupe: the scans join fans out one row per scan.
    _seen, _uniq = set(), []
    for _r in rows:
        if _r.id not in _seen:
            _seen.add(_r.id)
            _uniq.append(_r)
    return [_inspection_dict(i) for i in _uniq]


class AddRemarkRequest(BaseModel):
    remark: str = Field(..., min_length=2, max_length=1000)


@router.get("/inspections/{inspection_id}")
def get_inspection(insp: Inspection = Depends(owned_inspection),
                   db: Session = Depends(get_db)):
    from models import Finding
    from rules_engine import ALL_CHECK_IDS
    d = _inspection_dict(insp)
    # Detail payload: scans are rebuilt below with the full per-package
    # contract the portal reads (product_name/mrp/net_quantity/images sha256/
    # findings with ledger_ref+confidence) plus the app-compat keys the list
    # shape exposed (created_at, image_count, checks, finding code/name/
    # checkVerdict aliases). Both are kept so neither client loses fields.
    d["scans"] = []
    order = {cid: i for i, cid in enumerate(ALL_CHECK_IDS)}
    for s in (insp.scans or []):
        _imgs = sorted(getattr(s, "images", []) or [], key=lambda i: getattr(i, "id", 0))
        findings_rows = db.query(Finding).filter(Finding.scan_id == s.id).all()
        findings_rows.sort(key=lambda r: order.get(r.check_id, 99))

        # Extract MRP and check violations
        mrp_str = None
        for f in findings_rows:
            if f.check_id in ("CHK03", "CHK11") and f.observed:
                mrp_str = f.observed
                break
            if f.observed and ("₹" in f.observed or "Rs" in f.observed or "MRP" in f.observed.upper()):
                mrp_str = f.observed

        # Net quantity string
        nq_str = None
        if getattr(s, "net_quantity_value", None) is not None and getattr(s, "net_quantity_unit", None):
            val = s.net_quantity_value
            val_fmt = f"{int(val)}" if val == int(val) else f"{val}"
            nq_str = f"{val_fmt} {s.net_quantity_unit}"

        v_count = sum(1 for f in findings_rows if (f.effective_verdict or f.engine_verdict) == "fail")

        d["scans"].append({
            "id": s.id,
            "commodity_generic": s.commodity_generic,
            "product_name": f"{s.brand_name or ''} {s.commodity_generic or 'Commodity Item'}".strip(),
            "brand_name": s.brand_name,
            "commodity_category": s.commodity_category,
            "batch_number": s.batch_number,
            "barcode": s.barcode,
            "net_quantity_value": s.net_quantity_value,
            "net_quantity_unit": s.net_quantity_unit,
            "net_quantity": nq_str,
            "mrp": mrp_str,
            "violations_count": v_count,
            "overall_result": s.overall_result,
            "result": s.overall_result,
            "verdict": s.overall_result,
            "checks_assessed": s.checks_assessed,
            "checks_total": s.checks_total,
            "checks": s.checks_total,
            "duplicate_of": s.duplicate_of,
            "created_at": s.created_at.isoformat() if getattr(s, "created_at", None) else None,
            "image_count": len(_imgs),
            "engine_version": s.engine_version,
            "rules_as_at": s.rules_as_at.isoformat() if getattr(s, "rules_as_at", None) else None,
            "thumbnail_url": (
                f"/scans/{s.id}/images/{_imgs[0].id}/thumbnail"
                if _imgs else None),
            "images": [
                {
                    "id": img.id,
                    "panel": img.panel,
                    "sha256": img.sha256,
                    "url": f"/scans/{s.id}/images/{img.id}/thumbnail",
                    "thumbnail_url": f"/scans/{s.id}/images/{img.id}/thumbnail",
                }
                for img in _imgs
            ],
            "findings": [
                {
                    "id": f.id,
                    "check_id": f.check_id,
                    # Aliases the inspector app's FindingRow/VerdictBadge read.
                    "code": f.check_id,
                    "title": f.title,
                    "name": f.title,
                    "engine_verdict": f.engine_verdict,
                    "human_verdict": f.human_verdict,
                    "effective_verdict": f.effective_verdict,
                    "verdict": f.effective_verdict,
                    "checkVerdict": f.effective_verdict,
                    "result": (
                        "Violation" if (f.effective_verdict or f.engine_verdict) == "fail"
                        else "Not Assessed" if (f.effective_verdict or f.engine_verdict) == "not_assessed"
                        else "Out of Scope" if (f.effective_verdict or f.engine_verdict) == "out_of_scope"
                        else "Compliant"
                    ),
                    "severity": f.severity,
                    "reason": f.reason,
                    "override_reason": f.override_reason,
                    "observed": f.observed,
                    "required": f.required,
                    "citation": f.citation,
                    "ledger_ref": f.ledger_ref,
                    "confidence": f.confidence,
                    "overridden_at": f.overridden_at.isoformat() if f.overridden_at else None,
                    "overridden_by": f.overridden_by,
                }
                for f in findings_rows
            ],
        })
    return d


@router.get("/inspections/{inspection_id}/assessment")
def get_inspection_assessment(
    insp: Inspection = Depends(owned_inspection),
    db: Session = Depends(get_db),
):
    """Aggregate multi-panel inspection assessment with Phase 4D dossier & capture guidance."""
    from models import Finding
    from rules.aggregation import aggregate_inspection_assessment
    from rules_engine import FindingResult

    live = [s for s in (insp.scans or []) if getattr(s, "duplicate_of", None) is None]
    all_findings: list[FindingResult] = []
    all_images = []
    captured_panels: set[str] = set()

    for s in live:
        for img in getattr(s, "images", []) or []:
            all_images.append(img)
            if getattr(img, "panel", None):
                captured_panels.add(img.panel)
        findings_rows = db.query(Finding).filter(Finding.scan_id == s.id).all()
        for f in findings_rows:
            all_findings.append(FindingResult(
                check_id=f.check_id,
                title=f.title,
                verdict=f.effective_verdict or f.engine_verdict,
                severity=f.severity,
                reason=f.reason,
                observed=f.observed,
                required=f.required,
                citation=f.citation,
                ledger_ref=f.ledger_ref,
                confidence=f.confidence,
                limb=f.limb,
            ))

    assessment = aggregate_inspection_assessment(
        inspection_id=insp.id,
        findings=all_findings,
        rules_as_at=insp.inspection_date,
        captured_panels=captured_panels,
        images=all_images,
    )
    return assessment.to_dict()


@router.post("/inspections/{inspection_id}/findings/{finding_id}/remark")
def add_finding_remark(
    finding_id: int,
    body: AddRemarkRequest,
    insp: Inspection = Depends(owned_inspection),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    from models import Finding
    finding = db.get(Finding, finding_id)
    if finding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Finding not found")

    scan = db.get(Scan, finding.scan_id)
    if scan is None or scan.inspection_id != insp.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Finding does not belong to this inspection")

    old_reason = finding.reason
    finding.reason = body.remark.strip()
    finding.override_reason = body.remark.strip()
    finding.overridden_by = user.id
    finding.overridden_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(finding)

    append_audit(
        db,
        inspection_id=insp.id,
        scan_id=scan.id,
        user_id=user.id,
        action="inspector_remark",
        old_value=old_reason,
        new_value=finding.reason,
        reason=f"Remark recorded for {finding.check_id}",
    )

    return {
        "success": True,
        "finding_id": finding.id,
        "check_id": finding.check_id,
        "remark": finding.reason,
        "updated_at": finding.overridden_at.isoformat() if finding.overridden_at else None,
    }


@router.post("/inspections/{inspection_id}/submit")
def submit_inspection(body: SubmitInspectionRequest,
                      insp: Inspection = Depends(owned_inspection),
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    if insp.status == "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already submitted")
    insp.status = "submitted"
    insp.signature_status = body.signature_status
    if body.notes:
        insp.notes = body.notes
    insp.submitted_at = datetime.now(timezone.utc)
    db.commit()
    append_audit(db, inspection_id=insp.id, user_id=user.id,
                 action="inspection_submitted", old_value="draft",
                 new_value="submitted", reason=body.notes)
    return _inspection_dict(insp)


@router.patch("/inspections/{inspection_id}")
def update_inspection(inspection_id: int,
                      body: UpdateInspectionRequest,
                      user: User = Depends(get_current_user),
                      db: Session = Depends(get_db)):
    """Update inspection notes (inspector remarks) or signature status.
    Inspector may update their own inspection; admin can update any."""
    insp = db.get(Inspection, inspection_id)
    if insp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inspection not found")
    if user.role != "admin" and insp.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted to modify this inspection")

    old_notes = insp.notes
    if body.notes is not None:
        insp.notes = body.notes
        append_audit(db, inspection_id=insp.id, user_id=user.id,
                     action="inspection_remark_updated",
                     old_value=old_notes, new_value=body.notes,
                     reason="Inspector notes/remarks updated")
    if body.signature_status is not None:
        insp.signature_status = body.signature_status

    db.commit()
    db.refresh(insp)
    return _inspection_dict(insp)


@router.get("/rule-info")
def get_rule_info(user: User = Depends(get_current_user)):
    """Return active statutory rulebook info for inspector app (dynamic 2026.09.v1)."""
    from rules.loader import load_rule_pack
    pack = load_rule_pack(settings.RULE_PACK_VERSION)
    enabled_rules = [r for r in pack.rules if r.enabled]
    return {
        "id": f"rv-{pack.rule_pack_version}",
        "name": pack.title,
        "gazette_ref": pack.metadata.get("gazette_baseline", "G.S.R. 826(E) (Fourth Amendment Rules 2026)"),
        "rule_pack_version": pack.rule_pack_version,
        "active_rule_pack_version": pack.rule_pack_version,
        "rules_as_at": pack.rules_as_at,
        "engine_version": settings.ENGINE_VERSION,
        "is_active": True,
        "status": "Active",
        "total_checks": len(enabled_rules),
        "checks_registered": len(enabled_rules),
        "catalog_rule_count": len(pack.rules),
        "total_rules": len(pack.rules),
        "executable_check_count": 19,
        "implemented_checks": 19,
        "manual_review_rules": 7,
        "coverage_summary": {
            "catalog_rules": 26,
            "executable_checks": 19,
            "manual_review_rules": 7,
            "notes": "19 checks evaluated deterministically by algorithmic engine; 7 catalog rules require manual inspection or specialized gazette schedules.",
        },
        "summary": pack.description,
        "rules": [
            {
                "rule_id": r.rule_id,
                "code": r.code,
                "title": r.title,
                "source_rule": r.source_rule,
                "citation": f"{r.source_rule} — {r.source_document}",
                "severity": r.severity,
                "statutory_limb": r.statutory_limb,
                "required_fields": r.required_fields,
            }
            for r in enabled_rules
        ],
    }


@router.post("/inspections/{inspection_id}/scans",
              status_code=status.HTTP_201_CREATED)
def create_scan(body: CreateScanRequest,
                request: Request,
                insp: Inspection = Depends(owned_inspection),
                user: User = Depends(get_current_user),
                db: Session = Depends(get_db)):
    """One scan per package. Images are attached to it afterwards (§8.4).

    The rule-provenance columns are NOT NULL, so they are stamped now with the
    catalogue in force; the assess endpoint overwrites them with the exact set
    it actually ran against.

    Idempotency-Key replay (09 T14): same contract as POST /inspections — the
    sync queue retries this POST with the same key, so a lost response must
    not mint a second scan row.
    """
    from fastapi.responses import JSONResponse
    from idempotency import close_idempotent, idempotency_key, replay_or_open
    from sqlalchemy.exc import IntegrityError
    if insp.status == "submitted":
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Inspection already submitted; scans are frozen")

    _idem = idempotency_key(request)
    _stored, _is_new = replay_or_open(db, request, user.id, _idem)
    if not _is_new:
        _sc, _body = _stored
        return JSONResponse(status_code=_sc, content=_body)

    from rules_engine import catalog_hash

    g = body.geometry
    scan = Scan(
        inspection_id=insp.id,
        commodity_generic=body.commodity_generic,
        brand_name=body.brand_name,
        commodity_category=body.commodity_category,
        batch_number=body.batch_number,
        panel_shape=g.panel_shape,
        panel_height_mm=g.panel_height_mm,
        panel_width_mm=g.panel_width_mm,
        panel_diameter_mm=g.panel_diameter_mm,
        total_surface_area_cm2=g.total_surface_area_cm2,
        is_blown_moulded=g.is_blown_moulded,
        scale_source=g.scale_source,
        rules_as_at=date.fromisoformat(settings.RULES_AS_AT),
        catalog_hash=catalog_hash(),
        engine_version=settings.ENGINE_VERSION,
    )
    # Stash reference_pixel_size transiently for build_context/compute_scale
    # (no DB column; held on the instance, next assess reads via getattr).
    if getattr(g, "reference_pixel_size", None) is not None:
        object.__setattr__(scan, "reference_pixel_size", g.reference_pixel_size)
    db.add(scan)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail="Scan conflicts with existing data; retry")
    db.refresh(scan)
    append_audit(db, inspection_id=insp.id, scan_id=scan.id, user_id=user.id,
                 action="scan_created",
                 new_value=body.commodity_generic or "unidentified")
    _out = {"id": scan.id, "scan_id": scan.id, "inspection_id": insp.id,
            "overall_result": scan.overall_result,
            "checks_total": scan.checks_total}
    close_idempotent(db, request, user.id, _idem, status.HTTP_201_CREATED, _out)
    return _out
