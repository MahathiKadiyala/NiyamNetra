"""queries.py — the aggregations behind the report and dashboard endpoints.

Also home to resolve_duplicate (06 §3.5): the unique index on
(store_id, commodity_generic, batch_number, inspection_date) is expressed over
a join, so on SQLite it is enforced here in the service layer rather than
declaratively.
"""
from datetime import date
from sqlalchemy import and_, case, func, or_, select
from sqlalchemy.orm import Session

from models import Finding, Inspection, Scan, ScanImage, Store, User

LIVE = Scan.duplicate_of.is_(None)


def resolve_duplicate(db: Session, scan: Scan, inspection: Inspection) -> int | None:
    """Return the id of an existing scan this one duplicates, or None.

    A conflict is never rejected. The row is kept with duplicate_of set, so no
    field work is lost, and every aggregate excludes duplicate_of IS NOT NULL.
    """
    if not (scan.commodity_generic and scan.batch_number):
        return None
    prior = (
        db.query(Scan.id)
        .join(Inspection, Scan.inspection_id == Inspection.id)
        .filter(
            Inspection.store_id == inspection.store_id,
            Inspection.inspection_date == inspection.inspection_date,
            Scan.commodity_generic == scan.commodity_generic,
            Scan.batch_number == scan.batch_number,
            Scan.duplicate_of.is_(None),
            Scan.id != scan.id,
        )
        .order_by(Scan.id)
        .first()
    )
    return prior[0] if prior else None


def _result_counts(col=Scan.overall_result):
    """Four counts. Not two. A scan that could not be assessed is not a pass.

    total counts SCANS (Scan.id), not join rows: with the outer join an
    inspection with zero scans yields one NULL-scan row, and func.count()
    would count it as 1 while every bucket counts 0 — total != sum. Counting
    Scan.id keeps total == compliant+violation+not_assessed+out_of_scope.
    """
    return (
        func.coalesce(func.count(Scan.id), 0).label("total"),
        func.coalesce(func.sum(case((col == "compliant", 1), else_=0)), 0).label("compliant"),
        func.coalesce(func.sum(case((col == "violation", 1), else_=0)), 0).label("violation"),
        func.coalesce(func.sum(case((col == "not_assessed", 1), else_=0)), 0).label("not_assessed"),
        func.coalesce(func.sum(case((col == "out_of_scope", 1), else_=0)), 0).label("out_of_scope"),
    )
# --- Query 1: today's report for one inspector ---
def todays_stats(db: Session, user_id: int, day: date):
    total, comp, viol, na, oos = _result_counts()
    # Refusals: count inspections (not scans) where the merchant refused to
    # cooperate. signature_status == 'refused' is set by SubmitInspectionRequest
    # and is independent of whether any scans were taken.
    refusals = func.coalesce(
        func.count(func.distinct(case((Inspection.signature_status == "refused", Inspection.id)))), 0
    ).label("refusals")
    row = db.execute(
        select(
            func.count(func.distinct(Inspection.id)).label("inspections"),
            total, comp, viol, na, oos, refusals,
        )
        .select_from(Inspection)
        .outerjoin(Scan, (Scan.inspection_id == Inspection.id) & LIVE)
        .where(Inspection.user_id == user_id, Inspection.inspection_date == day)
    ).one()
    return dict(row._mapping)


# --- Query 2: store-wise breakdown ---
def store_breakdown(db: Session, user_id: int, day: date):
    total, comp, viol, na, oos = _result_counts()
    return [
        dict(r._mapping)
        for r in db.execute(
            select(Store.id, Store.name, total, comp, viol, na, oos)
            .select_from(Inspection)
            .join(Store, Store.id == Inspection.store_id)
            .outerjoin(Scan, (Scan.inspection_id == Inspection.id) & LIVE)
            .where(Inspection.user_id == user_id, Inspection.inspection_date == day)
            .group_by(Store.id, Store.name)
            .order_by(Store.name)
        )
    ]


# --- Query 3: which dates have data (calendar dots) ---
def active_dates(db: Session, user_id: int, year: int, month: int):
    return [
        r[0]
        for r in db.execute(
            select(Inspection.inspection_date)
            .where(
                Inspection.user_id == user_id,
                func.extract("year", Inspection.inspection_date) == year,
                func.extract("month", Inspection.inspection_date) == month,
            )
            .group_by(Inspection.inspection_date)
        )
    ]
# --- Query 4: admin dashboard ---
def admin_stats(db: Session, start: date, end: date, area: str | None = None):
    total, comp, viol, na, oos = _result_counts()
    q = (
        select(
            func.coalesce(func.count(func.distinct(Inspection.id)), 0).label("inspections"),
            func.coalesce(func.count(func.distinct(Inspection.user_id)), 0).label("active_inspectors"),
            func.coalesce(func.count(func.distinct(Inspection.store_id)), 0).label("stores_visited"),
            total, comp, viol, na, oos,
        )
        .select_from(Inspection)
        .join(Store, Store.id == Inspection.store_id)
        .outerjoin(Scan, (Scan.inspection_id == Inspection.id) & LIVE)
        .where(Inspection.inspection_date.between(start, end))
    )
    if area and area.strip().lower() != "all":
        q = q.where(or_(Store.city.ilike(f"%{area.strip()}%"), Store.district.ilike(f"%{area.strip()}%")))
    row = db.execute(q).one()
    d = {k: (v if v is not None else 0) for k, v in dict(row._mapping).items()}
    # Real count, not the hard-coded 0 that v1.x shipped at line 924.
    d["review_queue"] = review_queue_size(db, area=area) or 0
    return d


def get_low_confidence_scan_subquery():
    """Scan IDs with unresolved findings having confidence < 0.60 (excluding NULL confidence)."""
    return (
        select(Finding.scan_id)
        .filter(
            Finding.confidence.is_not(None),
            Finding.confidence < 0.60,
            Finding.human_verdict.is_(None),
        )
    )


def get_technical_recovery_scan_subquery():
    """Scan IDs with not_assessed findings caused by OCR or provider errors."""
    return (
        select(Finding.scan_id)
        .filter(
            Finding.engine_verdict == "not_assessed",
            or_(
                Finding.reason.ilike("%ocr%"),
                Finding.reason.ilike("%provider%"),
                Finding.reason.ilike("%technical%"),
                Finding.reason.ilike("%timeout%"),
                Finding.reason.ilike("%service unavailable%"),
                Finding.reason.ilike("%failed to initialize%"),
            ),
        )
    )


def get_recapture_guidance_scan_subquery():
    """Scan IDs with not_assessed findings citing missing evidence, blur, or recapture guidance."""
    return (
        select(Finding.scan_id)
        .filter(
            Finding.engine_verdict == "not_assessed",
            or_(
                Finding.reason.ilike("%recapture%"),
                Finding.reason.ilike("%missing%"),
                Finding.reason.ilike("%coverage%"),
                Finding.reason.ilike("%unclear%"),
                Finding.reason.ilike("%blur%"),
            ),
        )
    )


def get_unconfirmed_violation_scan_subquery():
    """Scan IDs with automated fail findings that have not been human-confirmed."""
    return (
        select(Finding.scan_id)
        .filter(
            Finding.engine_verdict == "fail",
            Finding.human_verdict.is_(None),
        )
    )


def get_canonical_subtype_filter(subtype: str):
    """Return SQLAlchemy expression for filtering scans by canonical review subtype."""
    if not subtype:
        return None
    st = subtype.strip().lower()
    if st == "technical_recovery":
        return and_(
            Scan.overall_result == "not_assessed",
            Scan.id.in_(get_technical_recovery_scan_subquery()),
        )
    elif st in ("recapture_required", "incomplete_coverage"):
        return and_(
            Scan.overall_result == "not_assessed",
            ~Scan.id.in_(get_technical_recovery_scan_subquery()),
            or_(
                ~Scan.id.in_(select(ScanImage.scan_id)),
                Scan.id.in_(get_recapture_guidance_scan_subquery()),
            ),
        )
    elif st in ("unconfirmed_violation", "unconfirmed_violations"):
        return and_(
            Scan.overall_result == "violation",
            Scan.id.in_(get_unconfirmed_violation_scan_subquery()),
        )
    elif st in ("confirmed_violation", "confirmed_violations"):
        return and_(
            Scan.overall_result == "violation",
            ~Scan.id.in_(get_unconfirmed_violation_scan_subquery()),
        )
    elif st == "low_confidence":
        return Scan.id.in_(get_low_confidence_scan_subquery())
    elif st in ("offline_edit", "offline_edits"):
        return Inspection.edited_offline.is_(True)
    return None


def get_canonical_queue_metrics(
    db: Session,
    user: User | None = None,
    area: str | None = None,
    status_filter: str | None = None,
) -> dict:
    """Calculate database-authoritative review queue counts across actionable categories.

    Entity: Scans (distinct packaging units under submitted inspections).
    Policy:
      - Submitted inspections only (draft inspections belong to inspector field workflow).
      - Live scans only (duplicate_of IS NULL).
      - Total counts distinct actionable scans (never sums overlapping categories).
      - Passing rule checks with NULL confidence are NOT treated as low-confidence defects.
    """
    from models import AuditLog, Finding, Inspection, Scan, ScanImage, Store
    from sqlalchemy import and_, or_, select, func

    # Base query for submitted live scans
    base_scans = (
        db.query(Scan)
        .join(Inspection, Inspection.id == Scan.inspection_id)
        .filter(
            Inspection.status == "submitted",
            Scan.duplicate_of.is_(None),
        )
    )

    if user and getattr(user, "role", None) != "admin":
        base_scans = base_scans.filter(Inspection.user_id == user.id)

    if area and area.strip().lower() != "all":
        a_str = area.strip()
        base_scans = base_scans.join(Store, Store.id == Inspection.store_id).filter(
            or_(Store.city.ilike(f"%{a_str}%"), Store.district.ilike(f"%{a_str}%"))
        )

    # Resolution status via audit log:
    resolved_insp_subq = (
        select(AuditLog.inspection_id)
        .filter(AuditLog.action.in_(["review_resolved", "review_dismissed"]))
    )
    resolved_scan_subq = (
        select(AuditLog.scan_id)
        .filter(AuditLog.action.in_(["review_resolved", "review_dismissed"]))
    )
    is_resolved_cond = or_(
        Inspection.id.in_(resolved_insp_subq),
        Scan.id.in_(resolved_scan_subq),
    )

    # In-review status via audit log:
    in_review_insp_subq = (
        select(AuditLog.inspection_id)
        .filter(AuditLog.action.in_(["review_started", "review_escalated"]))
    )
    in_review_scan_subq = (
        select(AuditLog.scan_id)
        .filter(AuditLog.action.in_(["review_started", "review_escalated", "finding_overridden"]))
    )
    is_in_review_cond = and_(
        ~is_resolved_cond,
        or_(
            Inspection.id.in_(in_review_insp_subq),
            Scan.id.in_(in_review_scan_subq),
        ),
    )

    # Low-confidence subquery: finding has confidence < 0.60 (and NOT null), unresolved
    low_conf_scan_ids = get_low_confidence_scan_subquery()

    # Review triggers:
    review_trigger_cond = or_(
        Scan.overall_result.in_(["violation", "not_assessed"]),
        Inspection.edited_offline.is_(True),
        Scan.id.in_(low_conf_scan_ids),
    )

    review_scans = base_scans.filter(review_trigger_cond)

    # Status filtering on review scans
    if status_filter == "OPEN":
        filtered_scans = review_scans.filter(~is_resolved_cond, ~is_in_review_cond)
    elif status_filter == "IN_REVIEW":
        filtered_scans = review_scans.filter(is_in_review_cond)
    elif status_filter == "RESOLVED":
        filtered_scans = review_scans.filter(is_resolved_cond)
    else:
        filtered_scans = review_scans

    # 1. Total distinct actionable scans in queue
    total_count = filtered_scans.with_entities(func.count(func.distinct(Scan.id))).scalar() or 0

    # 2. Violations pending: distinct scans in queue with violation result
    violations_count = (
        filtered_scans.filter(Scan.overall_result == "violation")
        .with_entities(func.count(func.distinct(Scan.id)))
        .scalar() or 0
    )

    # 3. Incomplete / Not assessed: distinct scans in queue with not_assessed result
    not_assessed_scans = filtered_scans.filter(Scan.overall_result == "not_assessed")
    not_assessed_count = not_assessed_scans.with_entities(func.count(func.distinct(Scan.id))).scalar() or 0

    # 4. Low confidence: distinct scans in queue having genuine low-confidence unresolved findings
    low_conf_count = (
        filtered_scans.filter(Scan.id.in_(low_conf_scan_ids))
        .with_entities(func.count(func.distinct(Scan.id)))
        .scalar() or 0
    )

    # 5. Offline edits: distinct scans in queue from offline-edited inspections
    offline_edits_count = (
        filtered_scans.filter(Inspection.edited_offline.is_(True))
        .with_entities(func.count(func.distinct(Scan.id)))
        .scalar() or 0
    )

    # Subcategory breakdowns:
    # Technical OCR/provider recovery
    technical_recovery_count = (
        not_assessed_scans.filter(get_canonical_subtype_filter("technical_recovery"))
        .with_entities(func.count(func.distinct(Scan.id)))
        .scalar() or 0
    )

    # Recapture required: not_assessed scans with 0 images or missing evidence/blur/coverage guidance
    recapture_required_count = (
        not_assessed_scans.filter(get_canonical_subtype_filter("recapture_required"))
        .with_entities(func.count(func.distinct(Scan.id)))
        .scalar() or 0
    )

    # Violations breakdown: unconfirmed awaiting adjudication vs confirmed awaiting enforcement
    unconfirmed_violations_count = (
        filtered_scans.filter(get_canonical_subtype_filter("unconfirmed_violation"))
        .with_entities(func.count(func.distinct(Scan.id)))
        .scalar() or 0
    )
    confirmed_violations_count = (
        filtered_scans.filter(get_canonical_subtype_filter("confirmed_violation"))
        .with_entities(func.count(func.distinct(Scan.id)))
        .scalar() or 0
    )

    # Resolved count
    resolved_scans_q = (
        base_scans.filter(
            review_trigger_cond,
            is_resolved_cond,
        )
    )
    adjudicated_resolved_count = resolved_scans_q.with_entities(func.count(func.distinct(Scan.id))).scalar() or 0

    # Draft inspections in progress (distinct inspections, not in officer review queue)
    draft_q = db.query(func.count(Inspection.id)).filter(Inspection.status == "draft")
    if user and getattr(user, "role", None) != "admin":
        draft_q = draft_q.filter(Inspection.user_id == user.id)
    draft_inspections_count = draft_q.scalar() or 0

    return {
        "total": total_count,
        "violations_pending": violations_count,
        "not_assessed": not_assessed_count,
        "low_confidence": low_conf_count,
        "offline_edits": offline_edits_count,
        "breakdown": {
            "technical_recovery": technical_recovery_count,
            "recapture_required": recapture_required_count,
            "unconfirmed_violations": unconfirmed_violations_count,
            "confirmed_violations": confirmed_violations_count,
            "adjudicated_resolved": adjudicated_resolved_count,
            "draft_inspections": draft_inspections_count,
        },
    }


def review_queue_size(db: Session, area: str | None = None) -> int:
    """Everything a human still has to look at, counted the same way the
    review endpoint lists it, so the badge and the page can never disagree.
    Counts unique actionable scans under submitted inspections.
    """
    metrics = get_canonical_queue_metrics(db, area=area, status_filter="OPEN")
    return metrics["total"]


# --- Query 5: violations by check, for the bar chart ---
def violations_by_check(db: Session, start: date, end: date, limit: int = 10, area: str | None = None):
    verdict = func.coalesce(Finding.human_verdict, Finding.engine_verdict)
    q = (
        select(
            Finding.check_id,
            Finding.title,
            func.count().label("count"),
        )
        .join(Scan, Scan.id == Finding.scan_id)
        .join(Inspection, Inspection.id == Scan.inspection_id)
        .join(Store, Store.id == Inspection.store_id)
        .where(
            verdict == "fail",
            LIVE,
            Inspection.inspection_date.between(start, end),
        )
    )
    if area and area.strip().lower() != "all":
        q = q.where(or_(Store.city.ilike(f"%{area.strip()}%"), Store.district.ilike(f"%{area.strip()}%")))
    q = q.group_by(Finding.check_id, Finding.title).order_by(func.count().desc()).limit(limit)
    return [dict(r._mapping) for r in db.execute(q)]


# --- Query 7: repeat violators, for enforcement prioritisation ---
def repeat_violators(db: Session, start: date, end: date, limit: int = 20):
    """Stores ranked by live violation-scan count in the window, with the most
    recent violation date. Powers GET /admin/repeat-violators and answers the
    enforcement question 'which premises re-offend' without a map."""
    return [
        dict(r._mapping)
        for r in db.execute(
            select(
                Store.id.label("store_id"),
                Store.name.label("store_name"),
                func.count(Scan.id).label("violations"),
                func.max(Inspection.inspection_date).label("last_violation_date"),
            )
            .select_from(Scan)
            .join(Inspection, Scan.inspection_id == Inspection.id)
            .join(Store, Store.id == Inspection.store_id)
            .where(
                Scan.overall_result == "violation",
                LIVE,
                Inspection.inspection_date.between(start, end),
            )
            .group_by(Store.id, Store.name)
            .order_by(func.count(Scan.id).desc())
            .limit(limit)
        )
    ]


# --- Query 8: proximity flags — different stores implausibly close ---
def proximity_flags(db: Session, start: date, end: date, meters: float = 50.0,
                    limit: int = 50):
    """Pairs of inspections at DIFFERENT stores whose device fixes fall within
    `meters` of each other. Same-store revisits are expected (see
    repeat_violators); different names at one doorstep mean a duplicated shop
    record, a mis-tagged visit, or GPS trouble — a human looks, the engine
    never concludes. O(n²) in Python over at most 2000 located inspections."""
    import math
    rows = (db.query(Inspection)
            .filter(Inspection.inspection_date.between(start, end),
                    Inspection.latitude.is_not(None),
                    Inspection.longitude.is_not(None))
            .order_by(Inspection.id).limit(2000).all())
    _R = 6_371_000.0

    def _hav(a1: float, o1: float, a2: float, o2: float) -> float:
        p1, p2 = math.radians(a1), math.radians(a2)
        dp, dl = math.radians(a2 - a1), math.radians(o2 - o1)
        h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
        return 2 * _R * math.asin(math.sqrt(h))

    out: list[dict] = []
    for i, a in enumerate(rows):
        for b in rows[i + 1:]:
            if a.store_id == b.store_id:
                continue
            try:
                d = _hav(a.latitude, a.longitude, b.latitude, b.longitude)
            except (TypeError, ValueError):
                continue
            if d <= meters:
                out.append({
                    "inspection_a": a.id, "inspection_b": b.id,
                    "store_a": a.store_id, "store_b": b.store_id,
                    "distance_m": round(d, 1),
                    "date_a": a.inspection_date.isoformat(),
                    "date_b": b.inspection_date.isoformat(),
                })
                if len(out) >= limit:
                    return out
    return out


# --- Query 6: trend, for the line chart ---
def inspection_trend(db: Session, start: date, end: date, area: str | None = None):
    total, comp, viol, na, oos = _result_counts()
    q = (
        select(Inspection.inspection_date, total, comp, viol, na, oos)
        .select_from(Inspection)
        .join(Store, Store.id == Inspection.store_id)
        .outerjoin(Scan, (Scan.inspection_id == Inspection.id) & LIVE)
        .where(Inspection.inspection_date.between(start, end))
    )
    if area and area.strip().lower() != "all":
        q = q.where(or_(Store.city.ilike(f"%{area.strip()}%"), Store.district.ilike(f"%{area.strip()}%")))
    q = q.group_by(Inspection.inspection_date).order_by(Inspection.inspection_date)
    return [dict(r._mapping) for r in db.execute(q)]


def violations_by_category_query(db: Session, start: date, end: date, area: str | None = None) -> list[dict]:
    verdict = func.coalesce(Finding.human_verdict, Finding.engine_verdict)
    q = (
        select(Finding.check_id, Finding.title, func.count().label("count"))
        .join(Scan, Scan.id == Finding.scan_id)
        .join(Inspection, Inspection.id == Scan.inspection_id)
        .join(Store, Store.id == Inspection.store_id)
        .where(verdict == "fail", LIVE, Inspection.inspection_date.between(start, end))
    )
    if area and area.strip().lower() != "all":
        q = q.where(or_(Store.city.ilike(f"%{area.strip()}%"), Store.district.ilike(f"%{area.strip()}%")))
    q = q.group_by(Finding.check_id, Finding.title)
    rows = db.execute(q).all()
    cat_counts = {}
    for r in rows:
        cat = check_category(r.check_id, r.title)
        cat_counts[cat] = cat_counts.get(cat, 0) + r.count
    return [{"category": k, "count": v} for k, v in sorted(cat_counts.items(), key=lambda x: x[1], reverse=True)[:8]]


def violations_by_area_query(db: Session, start: date, end: date, area: str | None = None) -> list[dict]:
    verdict = func.coalesce(Finding.human_verdict, Finding.engine_verdict)
    area_col = func.coalesce(Store.city, Store.district, "Other").label("area")
    q = (
        select(area_col, func.count().label("count"))
        .join(Inspection, Store.id == Inspection.store_id)
        .join(Scan, (Scan.inspection_id == Inspection.id) & LIVE)
        .join(Finding, Finding.scan_id == Scan.id)
        .where(verdict == "fail", Inspection.inspection_date.between(start, end))
    )
    if area and area.strip().lower() != "all":
        q = q.where(or_(Store.city.ilike(f"%{area.strip()}%"), Store.district.ilike(f"%{area.strip()}%")))
    q = q.group_by(area_col).order_by(func.count().desc()).limit(8)
    return [dict(r._mapping) for r in db.execute(q)]


def check_category(check_id: str | None, title: str | None = "") -> str:
    cid = (check_id or "").upper()
    t = (title or "").upper()
    if cid in ("CHK06", "CHK11") or "MRP" in t:
        return "MRP Declaration"
    if cid in ("CHK05", "CHK10") or "QUANTITY" in t or "NET" in t:
        return "Net Quantity"
    if cid in ("CHK07", "CHK13") or "DATE" in t or "EXPIRY" in t or "MONTH" in t:
        return "Date / Expiry"
    if cid == "CHK09" or "CONSUMER" in t or "CARE" in t:
        return "Consumer Care"
    if cid == "CHK04" or "MANUFACTURER" in t or "PACKER" in t:
        return "Manufacturer Info"
    if cid == "CHK12" or "ORIGIN" in t:
        return "Country of Origin"
    if cid in ("CHK15", "CHK16") or "ECOMMERCE" in t or "MARKETPLACE" in t:
        return "E-Commerce"
    return "Package Declarations"


def admin_violations_list(
    db: Session,
    start: date | None = None,
    end: date | None = None,
    store_id: int | None = None,
    limit: int = 200,
) -> list[dict]:
    """Query live finding failures joined with Scan, Inspection, Store, User."""
    verdict = func.coalesce(Finding.human_verdict, Finding.engine_verdict)
    q = (
        db.query(Finding, Scan, Inspection, Store, User)
        .join(Scan, Finding.scan_id == Scan.id)
        .join(Inspection, Scan.inspection_id == Inspection.id)
        .join(Store, Inspection.store_id == Store.id)
        .join(User, Inspection.user_id == User.id)
        .filter(verdict == "fail", LIVE)
    )
    if start:
        q = q.filter(Inspection.inspection_date >= start)
    if end:
        q = q.filter(Inspection.inspection_date <= end)
    if store_id is not None:
        q = q.filter(Store.id == store_id)

    rows = q.order_by(Inspection.inspection_date.desc(), Finding.id.desc()).limit(limit).all()

    out = []
    for f, s, i, st, u in rows:
        prod_name = (
            f"{s.brand_name} {s.commodity_generic}".strip()
            if (s.brand_name or s.commodity_generic)
            else "Packaged Item"
        )
        out.append({
            "id": f.id,
            "scan_id": s.id,
            "check_id": f.check_id,
            "date": i.inspection_date.isoformat() if i.inspection_date else "",
            "store_id": st.id,
            "store_name": st.name,
            "area": st.city or st.district or "Andhra Pradesh",
            "commodity_generic": s.commodity_generic or "Packaged Commodity",
            "product_name": prod_name,
            "brand_name": s.brand_name or "—",
            "manufacturer": s.brand_name or st.name or "Manufacturer",
            "category": check_category(f.check_id, f.title),
            "rule": f.citation or f"Rule ({f.check_id})",
            "citation": f.citation or f"Rule ({f.check_id})",
            "title": f.title,
            "reason": f.reason or f.observed or "Non-compliance observed",
            "result": "violation",
            "inspector_id": u.id,
            "inspector": u.full_name or u.employee_id,
        })
    return out


def scans_list(
    db: Session,
    inspection_id: int | None = None,
    store_id: int | None = None,
    status: str | None = None,
    q: str | None = None,
    limit: int = 200,
    offset: int = 0,
    user: User | None = None,
) -> list[dict]:
    """List live scans (duplicate_of is NULL) joined with Inspection, Store, User."""
    query = (
        db.query(Scan, Inspection, Store, User)
        .join(Inspection, Scan.inspection_id == Inspection.id)
        .join(Store, Inspection.store_id == Store.id)
        .join(User, Inspection.user_id == User.id)
        .filter(LIVE)
    )
    if user and getattr(user, "role", None) == "inspector":
        query = query.filter(Inspection.user_id == user.id)
    if inspection_id is not None:
        query = query.filter(Inspection.id == inspection_id)
    if store_id is not None:
        query = query.filter(Store.id == store_id)
    if status and status != "all":
        query = query.filter(Scan.overall_result == status)
    if q and q.strip():
        term = f"%{q.strip()}%"
        query = query.filter(
            or_(
                Scan.brand_name.ilike(term),
                Scan.commodity_generic.ilike(term),
                Scan.commodity_category.ilike(term),
                Scan.batch_number.ilike(term),
                Scan.barcode.ilike(term),
                Store.name.ilike(term),
            )
        )
    rows = (
        query.order_by(Scan.created_at.desc(), Scan.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    out = []
    for s, i, st, u in rows:
        out.append({
            "id": s.id,
            "inspection_id": i.id,
            "store_id": st.id,
            "store_name": st.name or f"Shop #{st.id}",
            "store_area": st.city or st.district or None,
            "inspector_id": u.id,
            "inspector_name": u.full_name or u.employee_id,
            "commodity_generic": s.commodity_generic,
            "brand_name": s.brand_name,
            "commodity_category": s.commodity_category,
            "batch_number": s.batch_number,
            "barcode": s.barcode,
            "net_quantity_value": s.net_quantity_value,
            "net_quantity_unit": s.net_quantity_unit,
            "mrp": None,
            "overall_result": s.overall_result or "not_assessed",
            "checks_total": s.checks_total or 0,
            "checks_assessed": s.checks_assessed or 0,
            "image_count": len(getattr(s, "images", []) or []),
            "created_at": s.created_at,
        })
    return out
