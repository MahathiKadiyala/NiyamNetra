"""routers/admin.py — the override path (§8.6, verbatim) and the admin surface
built to the §8.2 inventory around it.
"""
import math
import re
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlalchemy.orm import Session

from audit import append_audit, verify_chain
from config import settings
from database import get_db
from models import AuditLog, Finding, RuleVersionState, Scan, User
from password_handler import hash_password
from queries import (
    admin_stats, admin_violations_list, inspection_trend, proximity_flags, repeat_violators, review_queue_size,
    violations_by_area_query, violations_by_category_query, violations_by_check,
)
from rbac import get_current_user, require_admin
from schemas import (
    AdminDashboardResponse, AdminViolationItem, AdminViolationsResponse, AuditEntryOut, CheckTally, CreateUserRequest,
    OverrideFindingRequest, RepeatOffender, RepeatOffenderHistory, RepeatOffendersResponse, ResetInstallRequest,
    ResultCounts, RuleVersionOut, ToggleRuleVersionRequest, TrendPoint, UpdateUserRequest, UserOut,
)

router = APIRouter(prefix="/admin", tags=["admin"])


def _counts(d: dict) -> ResultCounts:
    return ResultCounts(
        total=d.get("total") or 0,
        compliant=d.get("compliant") or 0,
        violation=d.get("violation") or 0,
        not_assessed=d.get("not_assessed") or 0,
        out_of_scope=d.get("out_of_scope") or 0,
    )
@router.get("/dashboard", response_model=AdminDashboardResponse)
def dashboard(start: date | None = Query(default=None),
              end: date | None = Query(default=None),
              area: str | None = Query(default=None),
              user: User = Depends(require_admin),
              db: Session = Depends(get_db)):
    end = end or date.today()
    start = start or (end - timedelta(days=29))
    stats = admin_stats(db, start, end, area=area)
    top = [
        CheckTally(check_id=r["check_id"], title=r["title"], count=r["count"])
        for r in violations_by_check(db, start, end, area=area)
    ]
    trend = [
        TrendPoint(day=r["inspection_date"], counts=_counts(r))
        for r in inspection_trend(db, start, end, area=area)
    ]
    cats = violations_by_category_query(db, start, end, area=area)
    areas_breakdown = violations_by_area_query(db, start, end, area=area)
    return AdminDashboardResponse(
        period_start=start, period_end=end,
        inspections=stats.get("inspections") or 0,
        active_inspectors=stats.get("active_inspectors") or 0,
        stores_visited=stats.get("stores_visited") or 0,
        counts=_counts(stats),
        review_queue=stats.get("review_queue") or 0,
        top_failed_checks=top, trend=trend,
        violations_by_category=cats,
        violations_by_area=areas_breakdown,
    )


@router.get("/repeat-violators")
def repeat_offenders(start: date | None = Query(default=None),
                     end: date | None = Query(default=None),
                     limit: int = Query(default=20, ge=1, le=100),
                     user: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """Stores ranked by violation count — answers 'which premises re-offend'
    for enforcement prioritisation. Live scans only, duplicates excluded."""
    end = end or date.today()
    start = start or (end - timedelta(days=29))
    rows = repeat_violators(db, start, end, limit)
    return {
        "period_start": start.isoformat(), "period_end": end.isoformat(),
        "stores": [
            {"store_id": r["store_id"], "store_name": r["store_name"],
             "violations": r["violations"],
             "last_violation_date": r["last_violation_date"].isoformat()
              if r["last_violation_date"] else None}
            for r in rows
        ],
    }


@router.get("/repeat-offenders", response_model=RepeatOffendersResponse)
def repeat_offenders_alias(start: date | None = Query(default=None),
                           end: date | None = Query(default=None),
                           limit: int = Query(default=20, ge=1, le=100),
                           user: User = Depends(require_admin),
                           db: Session = Depends(get_db)):
    """Manufacturer-level repeat offenders matching PRD §5.9 and Portal UI."""
    end = end or date.today()
    start = start or (end - timedelta(days=29))

    # Backward-compat store rows
    store_rows = repeat_violators(db, start, end, limit)
    stores_compat = [
        {
            "store_id": r["store_id"],
            "store_name": r["store_name"],
            "violations": r["violations"],
            "last_violation_date": r["last_violation_date"].isoformat()
            if r["last_violation_date"]
            else None,
        }
        for r in store_rows
    ]

    # Query all live violation findings in the window
    violation_rows = admin_violations_list(db, start=start, end=end, limit=500)

    by_mfg: dict[str, dict] = {}
    for r in violation_rows:
        brand = (r.get("brand_name") or "").strip()
        mfg = (r.get("manufacturer") or "").strip()
        name = brand if (brand and brand != "—") else (mfg if mfg else "Unknown")
        if not name:
            name = "Unknown"

        if name not in by_mfg:
            slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.lower()).strip("-") or "unknown"
            by_mfg[name] = {
                "id": slug,
                "name": name,
                "violations": 0,
                "store_ids": set(),
                "brands": set(),
                "regions": set(),
                "last_violation": None,
                "history": [],
            }
        entry = by_mfg[name]
        entry["violations"] += 1
        if r.get("store_id"):
            entry["store_ids"].add(r["store_id"])
        if brand and brand != "—":
            entry["brands"].add(brand)
        if r.get("area") and r["area"] != "—":
            entry["regions"].add(r["area"])

        v_date = r.get("date")
        if v_date:
            if not entry["last_violation"] or v_date > entry["last_violation"]:
                entry["last_violation"] = v_date

        entry["history"].append(
            RepeatOffenderHistory(
                id=r["id"],
                scan_id=r.get("scan_id"),
                check_id=r.get("check_id") or "CHK",
                title=r.get("title") or r.get("reason"),
                citation=r.get("citation") or r.get("rule"),
                severity="major",
                brand=brand if (brand and brand != "—") else None,
                region=r.get("area"),
                store_id=r["store_id"],
                shopName=r.get("store_name"),
                inspector_id=r.get("inspector_id"),
                inspector=r.get("inspector"),
                product=r.get("product_name"),
                date=r.get("date"),
            )
        )

    offenders = []
    for name, entry in by_mfg.items():
        offenders.append(
            RepeatOffender(
                id=entry["id"],
                name=entry["name"],
                violations=entry["violations"],
                stores=len(entry["store_ids"]),
                brands=sorted(list(entry["brands"])),
                regions=sorted(list(entry["regions"])),
                last_violation=entry["last_violation"],
                history=sorted(entry["history"], key=lambda h: h.date or "", reverse=True),
            )
        )

    offenders.sort(key=lambda o: (o.violations, o.stores, o.last_violation or ""), reverse=True)

    return RepeatOffendersResponse(
        period_start=start.isoformat(),
        period_end=end.isoformat(),
        offenders=offenders[:limit],
        stores=stores_compat,
    )


@router.get("/violations", response_model=AdminViolationsResponse)
def get_admin_violations(
    start: date | None = Query(default=None),
    end: date | None = Query(default=None),
    store_id: int | None = Query(default=None),
    limit: int = Query(default=200, ge=1, le=500),
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """List live statutory violations across jurisdiction for regulator review."""
    rows = admin_violations_list(db, start=start, end=end, store_id=store_id, limit=limit)
    counts = {}
    for r in rows:
        cat = r["category"]
        counts[cat] = counts.get(cat, 0) + 1
    top = [{"category": k, "count": v} for k, v in sorted(counts.items(), key=lambda x: x[1], reverse=True)]
    return AdminViolationsResponse(
        total=len(rows),
        violations=[AdminViolationItem(**r) for r in rows],
        top_violations=top,
    )


@router.get("/rule-versions", response_model=list[RuleVersionOut])
def get_rule_versions(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Statutory catalog timeline for Legal Metrology Rules amendments."""
    RuleVersionState.__table__.create(db.bind, checkfirst=True)
    states = {s.id: s.is_active for s in db.query(RuleVersionState).all()}

    defaults = [
        {
            "id": "rv-2009-act",
            "year": 2009,
            "name": "Legal Metrology Act, 2009",
            "gazette_ref": "Act No. 1 of 2010 · Ministry of Law and Justice",
            "effective_from": "2009-10-01",
            "effective_to": "2011-03-06",
            "description": "Parent statutory Act establishing standards of weights and measures, inspection authority, and legal framework for packaged goods.",
            "default_active": False,
            "summary": "The primary parliamentary statute replacing the Standards of Weights and Measures Act, 1976. Established statutory definition of pre-packaged commodities (Sec 2(l)), mandatory packaging declarations (Sec 18), inspection and seizure powers (Sec 15), and statutory penalties for non-standard commodities (Sec 36).",
            "rules": [
                {"section": "Section 18(1)", "title": "Mandatory Declarations on Pre-packaged Commodities", "category": "Statutory Mandate", "status": "Mandatory"},
                {"section": "Section 36(1)", "title": "Penalty for Non-Standard Packages", "category": "Penal Provisions", "status": "Enforced"},
                {"section": "Section 36(2)", "title": "Penalty for Non-Declaration on Package", "category": "Penal Provisions", "status": "Enforced"},
            ],
        },
        {
            "id": "rv-2011-pcr",
            "year": 2011,
            "name": "Legal Metrology (Packaged Commodities) Rules, 2011",
            "gazette_ref": "G.S.R. 202(E) · Ministry of Consumer Affairs",
            "effective_from": "2011-03-07",
            "effective_to": "2017-12-31",
            "description": "Comprehensive subordinate legislation establishing Rule 6 mandatory declarations, Second Schedule prescribed sizes, font size tables, and inspection procedures.",
            "default_active": False,
            "summary": "Enacted under Section 52(2)(j) & (q) of Act 1 of 2010. Laid down 8 core declarations (Rule 6(1)), area-to-height font ratios (Rule 7), retail price display (Rule 6(1)(e)), and maximum permissible errors (First Schedule).",
            "rules": [
                {"section": "Rule 6(1)", "title": "Mandatory Declarations on Retail Packages", "category": "Core Mandate", "status": "Superseded"},
                {"section": "Rule 7", "title": "General Provisions relating to Display of Declarations", "category": "Typography", "status": "Superseded"},
            ],
        },
        {
            "id": "rv-2017-amend",
            "year": 2017,
            "name": "Legal Metrology (Packaged Commodities) Amendment Rules, 2017",
            "gazette_ref": "G.S.R. 629(E) · w.e.f. 01-01-2018",
            "effective_from": "2018-01-01",
            "effective_to": "2022-06-30",
            "description": "Major landmark amendment introducing Rule 6(10) E-Commerce marketplace declarations, dual MRP prohibitions, bar on altering MRP, and expanded medical device exemptions.",
            "default_active": False,
            "summary": "Introduced mandatory e-commerce declarations (Rule 6(10)), barred charging higher price on different channels, tightened sticker rules, and strengthened consumer care declaration requirements.",
            "rules": [
                {"section": "Rule 6(10)", "title": "E-Commerce Entity Declarations", "category": "Digital / E-Commerce", "status": "Amended"},
                {"section": "Rule 6(3)", "title": "Sticker Prohibitions & Exceptions", "category": "Stickers / Overwrite", "status": "Active"},
            ],
        },
        {
            "id": "rv-2026-current",
            "year": 2026,
            "name": "Legal Metrology (Packaged Commodities) Rules, 2011 (As Amended 2026)",
            "gazette_ref": "G.S.R. 226(E) & 521(E) · Consolidated 2026 Edition",
            "effective_from": "2026-07-01",
            "effective_to": None,
            "description": "Current active statutory rulebook enforced by NiyamNetra v2.0 Compliance Engine. Incorporates unit sale price mandates, QR code provisions, and enhanced schedule standards.",
            "default_active": True,
            "summary": "Consolidated rules in force as at 2026-07-01. Governs all 19 algorithmic checks in NiyamNetra including Rule 6(1)(f) Unit Sale Price, Second Schedule standard sizing, and digital e-commerce compliance.",
            "rules": [
                {"section": "Rule 6(1)(e)", "title": "Maximum Retail Price (MRP) & Sticker Rules", "category": "Pricing", "status": "Active"},
                {"section": "Rule 6(1)(c)", "title": "Net Quantity & Numeral Height (Rule 7)", "category": "Quantity", "status": "Active"},
                {"section": "Rule 6(1)(d)", "title": "Month & Year of Manufacture / Pre-packing", "category": "Dates", "status": "Active"},
                {"section": "Rule 6(1)(a)", "title": "Name & Address of Manufacturer / Packer", "category": "Identity", "status": "Active"},
                {"section": "Rule 6(1)(f)", "title": "Unit Sale Price (USP) per g/ml/piece", "category": "Pricing", "status": "Active"},
                {"section": "Rule 6(10)", "title": "E-Commerce Marketplace Compliance", "category": "Digital", "status": "Active"},
            ],
        },
    ]

    out = []
    for d in defaults:
        vid = d["id"]
        is_act = states[vid] if vid in states else d["default_active"]
        st = "Active" if is_act else ("Archived" if d["effective_to"] else "Inactive")
        out.append(
            RuleVersionOut(
                id=d["id"],
                year=d["year"],
                name=d["name"],
                gazette_ref=d["gazette_ref"],
                effective_from=d["effective_from"],
                effective_to=d["effective_to"],
                description=d["description"],
                status=st,
                is_active=is_act,
                summary=d["summary"],
                rules=d["rules"],
            )
        )
    return out


@router.patch("/rule-versions/{version_id}", response_model=RuleVersionOut)
def toggle_rule_version(
    version_id: str,
    body: ToggleRuleVersionRequest,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Toggle a statutory rule version Active/Inactive state with audit logging."""
    RuleVersionState.__table__.create(db.bind, checkfirst=True)
    st = db.query(RuleVersionState).filter(RuleVersionState.id == version_id).first()
    if not st:
        st = RuleVersionState(id=version_id, is_active=body.is_active)
        db.add(st)
    else:
        st.is_active = body.is_active
    db.commit()
    append_audit(db, user_id=user.id, action="rule_version_toggled",
                 new_value=f"{version_id}:{body.is_active}")

    versions = get_rule_versions(user=user, db=db)
    for v in versions:
        if v.id == version_id:
            return v
    raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Rule version not found")


@router.get("/users", response_model=list[UserOut])
def list_users(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    return db.query(User).order_by(User.employee_id).all()


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: CreateUserRequest, user: User = Depends(require_admin),
                db: Session = Depends(get_db)):
    from sqlalchemy.exc import IntegrityError
    if db.query(User).filter(User.employee_id == body.employee_id).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Employee ID exists")
    if body.email and db.query(User).filter(User.email == body.email).first():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already exists")
    target = User(
        employee_id=body.employee_id, full_name=body.full_name,
        password_hash=hash_password(body.password), role=body.role,
        jurisdiction=body.jurisdiction, email=body.email, phone=body.phone,
    )
    db.add(target)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="User already exists")
    db.refresh(target)
    append_audit(db, user_id=user.id, action="user_created",
                 new_value=f"{target.employee_id}:{target.role}")
    return target


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user(user_id: int, body: UpdateUserRequest,
                user: User = Depends(require_admin), db: Session = Depends(get_db)):
    from sqlalchemy.exc import IntegrityError
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    changes = body.model_dump(exclude_unset=True)
    if "email" in changes and changes["email"]:
        clash = db.query(User).filter(User.email == changes["email"], User.id != user_id).first()
        if clash:
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already exists")
    # Self-lockout guard: an admin must not deactivate themselves or remove
    # their own admin role in a single call (would lock all admin access).
    if target.id == user.id:
        if changes.get("is_active") is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="Cannot deactivate your own admin account")
        if "role" in changes and changes["role"] != "admin" and target.role == "admin":
            raise HTTPException(status.HTTP_400_BAD_REQUEST,
                                detail="Cannot remove your own admin role")
    for field, value in changes.items():
        setattr(target, field, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="User update conflicts with existing data")
    db.refresh(target)
    append_audit(db, user_id=user.id, action="user_updated",
                 new_value=f"{target.employee_id}:{sorted(changes)}")
    return target


@router.post("/users/{user_id}/reset-install")
def reset_install(user_id: int, body: ResetInstallRequest,
                  user: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Unbind the device and invalidate its live sessions. The next login on any
    device rebinds. token_epoch is bumped so refresh tokens minted for the old
    install die immediately (§3.2)."""
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="User not found")
    old = target.install_id
    target.install_id = None
    target.install_bound_at = None
    target.token_epoch += 1
    db.commit()
    append_audit(db, user_id=user.id, action="install_reset",
                 old_value=old, reason=body.reason)
    return {"user_id": user_id, "install_id": None}


@router.get("/review-queue")
def review_queue(user: User = Depends(require_admin), db: Session = Depends(get_db)):    # The same count the badge shows, computed the same way (queries §review).
    return {"count": review_queue_size(db)}


@router.get("/review-queue/items")
def review_queue_items(limit: int = Query(default=200, ge=1, le=500),
                       user: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """P1 fix: the count endpoint is not listable — the portal walked up to
    200 inspections client-side and edited_offline was unlistable at all.
    Returns the three summed terms as rows (bounded by limit) so badge == page.
    """
    from models import Inspection
    from queries import LIVE
    from sqlalchemy import or_
    na_scans = (
        db.query(Scan)
        .join(Inspection, Inspection.id == Scan.inspection_id)
        .filter(
            Inspection.status == "submitted",
            Scan.overall_result == "not_assessed",
            LIVE,
        )
        .order_by(Scan.id.desc())
        .limit(limit)
        .all()
    )
    low_conf = (
        db.query(Finding)
        .join(Scan, Scan.id == Finding.scan_id)
        .join(Inspection, Inspection.id == Scan.inspection_id)
        .filter(
            Inspection.status == "submitted",
            Finding.confidence.is_not(None),
            Finding.confidence < 0.60,
            Finding.human_verdict.is_(None),
            LIVE,
        )
        .order_by(Finding.id.desc())
        .limit(limit)
        .all()
    )
    offline = (
        db.query(Inspection)
        .filter(
            Inspection.status == "submitted",
            Inspection.edited_offline.is_(True),
        )
        .order_by(Inspection.id.desc())
        .limit(limit)
        .all()
    )
    return {
        "limit": limit,
        "not_assessed_scans": [
            {"scan_id": s.id, "inspection_id": s.inspection_id,
             "commodity_generic": s.commodity_generic,
             "overall_result": s.overall_result} for s in na_scans],
        "low_confidence_findings": [
            {"finding_id": f.id, "scan_id": f.scan_id, "check_id": f.check_id,
             "title": f.title, "confidence": f.confidence} for f in low_conf],
        "offline_edits": [
            {"inspection_id": i.id, "store_id": i.store_id,
             "clock_skew_seconds": i.clock_skew_seconds,
             "edited_offline": True} for i in offline],
    }


@router.patch("/findings/{finding_id}")
def override_finding(
    finding_id: int,
    body: OverrideFindingRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A human may disagree with the engine, or an inspector may attach a
    statutory remark/clarification. C11.

    engine_verdict is protected by a database trigger as well (§10.3), so a
    stray UPDATE from a script cannot do what this endpoint refuses to do.

    After the override the parent Scan.overall_result is recomputed from the
    effective verdicts of ALL its findings: any fail -> violation; all pass
    -> compliant; otherwise not_assessed. A scan that was out_of_scope keeps
    that result (an override cannot pull a package back into scope).
    Submitted inspections are frozen (409).
    """
    from sqlalchemy.exc import IntegrityError

    from models import Inspection as _Inspection
    f = db.get(Finding, finding_id)
    if f is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Finding not found")

    scan = db.get(Scan, f.scan_id)
    if scan is not None and scan.inspection_id is not None:
        _insp = db.get(_Inspection, scan.inspection_id)
        if _insp is not None:
            if user.role != "admin" and _insp.user_id != user.id:
                raise HTTPException(status.HTTP_403_FORBIDDEN,
                                    detail="Not permitted to modify this finding")
            if _insp.status == "submitted":
                raise HTTPException(status.HTTP_409_CONFLICT,
                                    detail="Inspection submitted; findings frozen")

    old = f.human_verdict or f.engine_verdict
    new_verdict = body.human_verdict if body.human_verdict is not None else f.human_verdict
    if new_verdict in ("compliant", "pass"):
        new_verdict = "pass"
    elif new_verdict in ("violation", "fail"):
        new_verdict = "fail"
    f.human_verdict = new_verdict
    f.override_reason = body.override_reason
    f.overridden_by = user.id
    f.overridden_at = datetime.now(timezone.utc)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Override conflicts with existing data")

    scan = db.get(Scan, f.scan_id)
    if scan is not None and scan.overall_result != "out_of_scope":
        try:
            _rows = db.query(Finding).filter(Finding.scan_id == scan.id).all()
            _eff = [(r.human_verdict or r.engine_verdict) for r in _rows]
            if any(v == "fail" for v in _eff):
                scan.overall_result = "violation"
            elif _eff and all(v == "pass" for v in _eff):
                scan.overall_result = "compliant"
            else:
                scan.overall_result = "not_assessed"
            scan.checks_total = len(_rows)
            scan.checks_assessed = sum(1 for v in _eff if v in ("pass", "fail"))
            db.commit()
        except IntegrityError:
            db.rollback()
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Recompute conflicts with existing data")
    action_type = "finding_overridden" if body.human_verdict is not None and body.human_verdict != old else "finding_remark_added"
    append_audit(
        db, inspection_id=scan.inspection_id if scan else None, scan_id=scan.id if scan else None, user_id=user.id,
        action=action_type,
        old_value=f"{f.check_id}={old}",
        new_value=f"{f.check_id}={new_verdict or old}",
        reason=body.override_reason,
    )
    return {
        "finding_id": f.id,
        "engine_verdict": f.engine_verdict,
        "human_verdict": f.human_verdict,
        "effective_verdict": f.effective_verdict,
        "override_reason": f.override_reason,
    }


@router.get("/audit")
def audit_log(limit: int = Query(default=50, ge=1, le=500),
              offset: int = Query(default=0, ge=0),
              user: User = Depends(require_admin), db: Session = Depends(get_db)):
    total = db.query(AuditLog).count()
    rows = (db.query(AuditLog).order_by(AuditLog.seq.desc())
            .offset(offset).limit(limit).all())
    state = verify_chain(db)
    return {
        "total": total, "limit": limit, "offset": offset,
        "chain_intact": state.get("intact"), "chain_head": state.get("head"),
        "entries": [AuditEntryOut.model_validate(r) for r in rows],
    }


@router.get("/rules")
def rules_status(user: User = Depends(require_admin), db: Session = Depends(get_db)):
    from rules_engine import ALL_CHECK_IDS, catalog_hash, load_catalog
    cat = load_catalog()
    return {
        "rules_as_at": settings.RULES_AS_AT,
        "engine_version": settings.ENGINE_VERSION,
        "catalog_hash": catalog_hash(),
        "checks_registered": len(ALL_CHECK_IDS),
        "second_schedule_populated": bool(cat.get("second_schedule")),
        "net_quantity_heights_populated": bool(cat.get("net_quantity_heights")),
        "meta": cat.get("_meta"),
    }


class RuleTablesRequest(BaseModel):
    """Transcribe gazette tables to activate CHK10 / CHK06b. Only from a
    verified gazette reading — the engine never guesses schedule contents.

    second_schedule: { "<category lowercased>": {"sizes": [<numbers>] } }
    net_quantity_heights: { "bands": [{ "unit": "g|kg|ml|l",
      "upper": <inclusive max>, "min_height_mm": <number> }] }
    Omit a table (null) to leave it as-is; pass {} to clear it back to
    not_assessed. At least one table must be provided.
    """
    second_schedule: dict | None = None
    net_quantity_heights: dict | None = None


def _validate_second_schedule(v: dict) -> None:
    if not isinstance(v, dict):
        raise ValueError("second_schedule must be an object")
    for cat, grp in v.items():
        if not isinstance(cat, str) or not cat.strip():
            raise ValueError("second_schedule categories must be non-empty strings")
        if not isinstance(grp, dict) or not isinstance(grp.get("sizes"), list) or not grp["sizes"]:
            raise ValueError(f"second_schedule[{cat!r}] needs a non-empty 'sizes' list")
        for s in grp["sizes"]:
            if not isinstance(s, (int, float)) or not math.isfinite(s) or s <= 0:
                raise ValueError(f"second_schedule[{cat!r}] sizes must be positive numbers")


def _validate_nq_heights(v: dict) -> None:
    if not isinstance(v, dict) or not isinstance(v.get("bands"), list) or not v["bands"]:
        raise ValueError("net_quantity_heights needs a non-empty 'bands' list")
    for b in v["bands"]:
        if not isinstance(b, dict):
            raise ValueError("each band must be an object")
        if str(b.get("unit", "")).lower() not in {"g", "kg", "mg", "ml", "l", "ltr", "litre"}:
            raise ValueError("band.unit must be a net-quantity unit (g|kg|mg|ml|l|...)")
        for k in ("upper", "min_height_mm"):
            if not isinstance(b.get(k), (int, float)) or not math.isfinite(b[k]) or b[k] <= 0:
                raise ValueError(f"band.{k} must be a positive number")


@router.put("/rules/tables")
def update_rule_tables(body: RuleTablesRequest,
                       user: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Activate the two data-blocked checks by transcribing verified gazette
    tables. Writes the catalog JSON (timestamped .bak kept), clears the
    engine caches so the next assess uses the new tables, and audits the
    change with old/new hashes. Populate only from a verified gazette."""
    import json
    if body.second_schedule is None and body.net_quantity_heights is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY,
                            detail="Provide second_schedule and/or net_quantity_heights")
    from rules_engine import catalog_hash as _ch, load_catalog as _lc
    old_hash = _ch()
    cat = dict(_lc())
    if body.second_schedule is not None:
        _validate_second_schedule(body.second_schedule)
        cat["second_schedule"] = {k.lower(): v for k, v in body.second_schedule.items()}
    if body.net_quantity_heights is not None:
        _validate_nq_heights(body.net_quantity_heights)
        cat["net_quantity_heights"] = body.net_quantity_heights
    path = settings.RULES_CATALOG
    try:
        backup = path.with_suffix(f".bak-{datetime.now(timezone.utc):%Y%m%d%H%M%S}.json")
        backup.write_text(path.read_text(encoding="utf-8"), encoding="utf-8")
        path.write_text(json.dumps(cat, indent=2, sort_keys=True), encoding="utf-8")
    except OSError as e:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"Catalog store unavailable: {e}")
    try:
        _lc.cache.clear()
    except Exception:
        pass
    try:
        _ch.cache_clear()
    except Exception:
        pass
    new_hash = _ch()
    append_audit(db, user_id=user.id, action="rule_tables_updated",
                 old_value=old_hash[:16], new_value=new_hash[:16],
                 reason="gazette transcription by admin")
    return {
        "catalog_hash": new_hash,
        "second_schedule_populated": bool(cat.get("second_schedule")),
        "net_quantity_heights_populated": bool(cat.get("net_quantity_heights")),
    }


@router.get("/reports/range.{fmt}")
def admin_range_report(fmt: str, start: date = Query(...), end: date = Query(...),
                       user_id: int | None = Query(default=None),
                       user: User = Depends(require_admin),
                       db: Session = Depends(get_db)):
    """Office-wide range document (all inspectors, or one via user_id).
    fmt is pdf|docx|xlsx|csv. The fix for 'a month of work = N downloads'."""
    from routers.reports import _range_blocks_for, _range_doc
    from audit import verify_chain as _vc
    ids = [user_id] if user_id is not None else None
    blocks = _range_blocks_for(db, start, end, ids)
    owner = db.get(User, user_id) if user_id is not None else user
    return _range_doc(fmt, start, end, blocks, owner or user, _vc(db).get("head") or "",
                      db=db, caller=user, kind="office-range")


@router.get("/proximity-flags")
def proximity_review(meters: float = Query(default=50.0, gt=0, le=5000),
                     days: int = Query(default=29, ge=1, le=365),
                     limit: int = Query(default=50, ge=1, le=200),
                     user: User = Depends(require_admin),
                     db: Session = Depends(get_db)):
    """Different stores within `meters` of each other — duplicated shop
    records, mis-tagged visits, or GPS trouble. Review items, never verdicts."""
    end = date.today()
    start = end - timedelta(days=days)
    return {
        "meters": meters, "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "flags": proximity_flags(db, start, end, meters, limit),
    }


@router.get("/reports/history")
def report_history(limit: int = Query(default=50, ge=1, le=200),
                   user: User = Depends(require_admin),
                   db: Session = Depends(get_db)):
    """Archive list of generated documents: who generated what, when, and the
    file sha256 so a kept copy verifies. Files stay ephemeral; rows are kept."""
    from models import ReportRecord
    rows = (db.query(ReportRecord).order_by(ReportRecord.id.desc()).limit(limit).all())
    return {
        "items": [
            {"id": r.id, "generated_by": r.generated_by, "kind": r.kind,
             "fmt": r.fmt, "label": r.label, "file_sha256": r.file_sha256,
             "byte_size": r.byte_size,
             "created_at": r.created_at.isoformat() if r.created_at else None}
            for r in rows
        ],
    }
