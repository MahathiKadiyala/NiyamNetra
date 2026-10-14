"""routers/review.py — Phase 5A: Officer Review Queue + Adjudication Workflow.

Provides a genuine officer-facing workflow for inspections that produce:
- REVIEW_REQUIRED assessments
- Unresolved cross-panel conflicts
- NOT_ASSESSED findings (incomplete coverage)
- Low-confidence OCR / measurement uncertainty
- Offline-edited inspections requiring review

Status model:
    OPEN → IN_REVIEW → RESOLVED

Every status transition is audit-logged with the officer's identity, reason,
and timestamp. The engine_verdict is immutable (C11); human_verdict records
the officer's adjudication without overwriting the deterministic result.

Core invariant:
    OCR READS → LLM STRUCTURES → RULES DECIDE → AGGREGATION ASSESSES → OFFICER ADJUDICATES
"""
from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import and_, case, func, or_
from sqlalchemy.orm import Session, joinedload, selectinload

from audit import append_audit
from database import get_db
from models import AuditLog, Finding, Inspection, Scan, Store, User
from rbac import get_current_user, require_admin, require_inspector

router = APIRouter(prefix="/review", tags=["review"])


# ---------------------------------------------------------------------------
# 1. Review Item Status Model
# ---------------------------------------------------------------------------

REVIEW_STATUSES = ("OPEN", "IN_REVIEW", "RESOLVED")


# ---------------------------------------------------------------------------
# 2. Request / Response Schemas
# ---------------------------------------------------------------------------

class ReviewQueueFilters(BaseModel):
    """Filters for the review queue."""
    status_filter: Literal["OPEN", "IN_REVIEW", "RESOLVED", "ALL"] | None = None
    reason_filter: Literal[
        "not_assessed", "low_confidence", "offline_edit",
        "conflict", "violation", "ALL"
    ] | None = None
    area: str | None = None
    date_from: date | None = None
    date_to: date | None = None


class AdjudicateRequest(BaseModel):
    """Officer adjudication on a review item."""
    inspection_id: int | None = None
    action: Literal["resolve", "escalate", "reassign", "dismiss", "accept", "override"]
    reason: str = Field(min_length=10, max_length=2000)
    resolution_notes: str | None = Field(default=None, max_length=4000)
    # For finding-level adjudication
    finding_id: int | None = None
    human_verdict: Literal["pass", "fail", "not_assessed"] | None = None


class BulkAdjudicateRequest(BaseModel):
    """Bulk adjudication for multiple review items."""
    inspection_ids: list[int] = Field(min_length=1, max_length=50)
    action: Literal["resolve", "escalate"]
    reason: str = Field(min_length=10, max_length=2000)


class ConflictResolutionRequest(BaseModel):
    """Resolve a specific cross-panel conflict."""
    inspection_id: int | None = None
    conflict_id: str
    resolution: Literal["accept_panel_a", "accept_panel_b", "manual_value", "dismiss"]
    resolved_value: str | None = None
    reason: str = Field(min_length=10, max_length=2000)


# ---------------------------------------------------------------------------
# 3. Helper — build review item from DB rows
# ---------------------------------------------------------------------------
def _review_reason(scan: Scan, findings: list[Finding], insp: Inspection) -> list[dict]:
    """Determine why this inspection/scan needs review."""
    reasons = []

    # 1. Not assessed scans
    if scan.overall_result == "not_assessed":
        na_checks = [f for f in findings if f.engine_verdict == "not_assessed"]
        # Check for technical error
        tech_errs = [
            f for f in na_checks
            if f.reason and any(
                k in f.reason.lower()
                for k in ("ocr", "provider", "technical", "timeout", "unavailable", "failed to initialize")
            )
        ]
        if tech_errs:
            reasons.append({
                "type": "not_assessed",
                "subtype": "technical_recovery",
                "severity": "major",
                "detail": f"Technical OCR/provider error: {tech_errs[0].reason[:120]}",
                "affected_checks": [f.check_id for f in tech_errs],
            })
        else:
            reasons.append({
                "type": "not_assessed",
                "subtype": "incomplete_coverage",
                "severity": "major",
                "detail": f"{len(na_checks)} of {scan.checks_total} checks not assessed",
                "affected_checks": [f.check_id for f in na_checks],
            })

    # 2. Low confidence findings (genuine low confidence on unresolved checks only)
    low_conf = [
        f for f in findings
        if f.confidence is not None and f.confidence < 0.60
        and f.human_verdict is None
    ]
    if low_conf:
        reasons.append({
            "type": "low_confidence",
            "severity": "minor",
            "detail": f"{len(low_conf)} findings with low confidence (< 60%)",
            "affected_checks": [f.check_id for f in low_conf],
        })

    # 3. Offline edits
    if getattr(insp, "edited_offline", False):
        reasons.append({
            "type": "offline_edit",
            "severity": "minor",
            "detail": f"Inspection edited offline; clock skew {insp.clock_skew_seconds or 0}s",
        })

    # 4. Violations requiring officer confirmation / enforcement action
    unconfirmed_violations = [
        f for f in findings
        if (f.human_verdict or f.engine_verdict) == "fail"
        and f.human_verdict is None
    ]
    if unconfirmed_violations:
        reasons.append({
            "type": "violation",
            "subtype": "unconfirmed_violation",
            "severity": "critical",
            "detail": f"{len(unconfirmed_violations)} unconfirmed violations awaiting officer review",
            "affected_checks": [f.check_id for f in unconfirmed_violations],
        })
    elif scan.overall_result == "violation":
        reasons.append({
            "type": "violation",
            "subtype": "confirmed_violation",
            "severity": "critical",
            "detail": "Confirmed legal violation awaiting enforcement action",
            "affected_checks": [f.check_id for f in findings if (f.human_verdict or f.engine_verdict) == "fail"],
        })

    return reasons


def _build_review_item(insp: Inspection, scan: Scan,
                       findings: list[Finding],
                       review_status: str = "OPEN") -> dict:
    """Build a single review queue item."""
    reasons = _review_reason(scan, findings, insp)

    # Determine priority from reasons
    severities = [r.get("severity", "minor") for r in reasons]
    if "critical" in severities:
        priority = "high"
    elif "major" in severities:
        priority = "medium"
    else:
        priority = "low"

    # Build finding summaries
    finding_summaries = []
    unconfirmed_count = 0
    for f in findings:
        needs_rev = (
            f.engine_verdict == "not_assessed"
            or (f.engine_verdict == "fail" and f.human_verdict is None)
            or (f.confidence is not None and f.confidence < 0.60 and f.human_verdict is None)
        )
        if needs_rev:
            unconfirmed_count += 1
        finding_summaries.append({
            "id": f.id,
            "check_id": f.check_id,
            "title": f.title,
            "engine_verdict": f.engine_verdict,
            "human_verdict": f.human_verdict,
            "effective_verdict": f.effective_verdict,
            "severity": f.severity,
            "confidence": f.confidence,
            "reason": f.reason,
            "observed": f.observed,
            "required": f.required,
            "citation": f.citation,
            "needs_review": needs_rev,
        })

    store_name = None
    store_area = None
    try:
        if insp.store:
            store_name = insp.store.name
            store_area = insp.store.city or insp.store.district
    except Exception:
        pass

    inspector_name = None
    try:
        if insp.inspector:
            inspector_name = insp.inspector.full_name
    except Exception:
        pass

    return {
        "inspection_id": insp.id,
        "scan_id": scan.id,
        "store_id": insp.store_id,
        "store_name": store_name,
        "store_area": store_area,
        "inspector_name": inspector_name,
        "inspector_id": insp.user_id,
        "inspection_date": insp.inspection_date.isoformat(),
        "commodity_generic": scan.commodity_generic,
        "brand_name": scan.brand_name,
        "overall_result": scan.overall_result,
        "review_status": review_status,
        "priority": priority,
        "review_reasons": reasons,
        "reasons": reasons,
        "unconfirmed_count": unconfirmed_count,
        "checks_total": scan.checks_total,
        "checks_assessed": scan.checks_assessed,
        "findings": finding_summaries,
        "created_at": scan.created_at.isoformat() if scan.created_at else None,
        "rules_as_at": scan.rules_as_at.isoformat() if scan.rules_as_at else None,
        "engine_version": scan.engine_version,
        "rule_pack_version": scan.rule_pack_version,
    }


# ---------------------------------------------------------------------------
# 4. Review Queue Endpoints
# ---------------------------------------------------------------------------

@router.get("/queue")
def get_review_queue(
    status_filter: str | None = Query(default=None),
    reason: str | None = Query(default=None),
    area: str | None = Query(default=None),
    priority: str | None = Query(default=None),
    date_from: date | None = Query(default=None),
    date_to: date | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
):
    """Return the officer review queue with filtering, pagination, and priority ordering.

    Only submitted inspections enter the officer review queue (drafts remain in inspector field flow).
    Items enter this queue when:
    - A scan has overall_result == 'violation' (unconfirmed / confirmed violation)
    - A scan has overall_result == 'not_assessed' (incomplete coverage / technical OCR recovery)
    - An inspection was edited offline
    - A scan has genuine low-confidence findings (< 0.60 on unresolved checks)
    """
    from queries import LIVE
    from sqlalchemy import select

    # Base query for reviewable scans under submitted inspections
    q = (
        db.query(Scan, Inspection)
        .join(Inspection, Inspection.id == Scan.inspection_id)
        .options(
            joinedload(Inspection.store),
            joinedload(Inspection.inspector),
        )
        .filter(
            Inspection.status == "submitted",
            LIVE,
        )
    )

    # Non-admin sees only their own inspections
    if user.role != "admin":
        q = q.filter(Inspection.user_id == user.id)

    # Date range filter
    if date_from:
        q = q.filter(Inspection.inspection_date >= date_from)
    if date_to:
        q = q.filter(Inspection.inspection_date <= date_to)

    # Area filter
    if area and area.strip().lower() != "all":
        a = area.strip()
        q = q.join(Store, Store.id == Inspection.store_id).filter(
            or_(Store.city.ilike(f"%{a}%"), Store.district.ilike(f"%{a}%"))
        )

    # Low-confidence subquery
    low_conf_scan_subq = (
        select(Finding.scan_id)
        .filter(
            Finding.confidence.is_not(None),
            Finding.confidence < 0.60,
            Finding.human_verdict.is_(None),
        )
    )

    # Reason-based filtering
    if reason and reason.lower() != "all":
        if reason == "not_assessed":
            q = q.filter(Scan.overall_result == "not_assessed")
        elif reason == "violation":
            q = q.filter(Scan.overall_result == "violation")
        elif reason == "offline_edit":
            q = q.filter(Inspection.edited_offline.is_(True))
        elif reason == "low_confidence":
            q = q.filter(Scan.id.in_(low_conf_scan_subq))
    else:
        # Default: all review-worthy items
        q = q.filter(
            or_(
                Scan.overall_result.in_(["violation", "not_assessed"]),
                Inspection.edited_offline.is_(True),
                Scan.id.in_(low_conf_scan_subq),
            )
        )

    # Audit log resolution & in-review status
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

    # Apply status filter at SQL query level
    if status_filter == "OPEN":
        q = q.filter(~is_resolved_cond, ~is_in_review_cond)
    elif status_filter == "IN_REVIEW":
        q = q.filter(is_in_review_cond)
    elif status_filter == "RESOLVED":
        q = q.filter(is_resolved_cond)

    # Total matching count
    total_matching = q.with_entities(func.count(func.distinct(Scan.id))).scalar() or 0

    rows = (
        q.order_by(Inspection.inspection_date.desc(), Scan.id.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )

    # Batch query resolution status for fetched items
    scan_ids = [s.id for s, _ in rows]
    insp_ids = list({i.id for _, i in rows})

    resolved_scan_set = set()
    in_review_scan_set = set()
    if scan_ids or insp_ids:
        audit_rows = (
            db.query(AuditLog)
            .filter(
                or_(
                    AuditLog.scan_id.in_(scan_ids),
                    AuditLog.inspection_id.in_(insp_ids),
                ),
                AuditLog.action.in_([
                    "review_started", "review_resolved", "review_escalated",
                    "review_dismissed", "finding_overridden",
                ]),
            )
            .order_by(AuditLog.seq.asc())
            .all()
        )
        for a in audit_rows:
            target_scans = [a.scan_id] if a.scan_id else [s.id for s, insp in rows if insp.id == a.inspection_id]
            for sid in target_scans:
                if a.action in ("review_resolved", "review_dismissed"):
                    resolved_scan_set.add(sid)
                    in_review_scan_set.discard(sid)
                elif a.action in ("review_started", "review_escalated", "finding_overridden"):
                    if sid not in resolved_scan_set:
                        in_review_scan_set.add(sid)

    # Deduplicate (prevent fan out)
    seen_scans = set()
    items = []
    for scan, insp in rows:
        if scan.id in seen_scans:
            continue
        seen_scans.add(scan.id)

        findings = db.query(Finding).filter(Finding.scan_id == scan.id).all()

        if scan.id in resolved_scan_set:
            review_status = "RESOLVED"
        elif scan.id in in_review_scan_set:
            review_status = "IN_REVIEW"
        else:
            review_status = "OPEN"

        item = _build_review_item(insp, scan, findings, review_status)

        # Filter by priority if requested
        if priority and priority.lower() != "all":
            if item["priority"] != priority.lower():
                continue

        items.append(item)

    # Summary counts
    total_open = sum(1 for i in items if i["review_status"] == "OPEN")
    total_in_review = sum(1 for i in items if i["review_status"] == "IN_REVIEW")
    total_resolved = sum(1 for i in items if i["review_status"] == "RESOLVED")

    return {
        "total": total_matching,
        "offset": offset,
        "limit": limit,
        "summary": {
            "open": total_open,
            "in_review": total_in_review,
            "resolved": total_resolved,
            "by_priority": {
                "high": sum(1 for i in items if i["priority"] == "high"),
                "medium": sum(1 for i in items if i["priority"] == "medium"),
                "low": sum(1 for i in items if i["priority"] == "low"),
            },
        },
        "items": items,
    }


@router.get("/queue/summary")
def get_review_queue_summary(
    area: str | None = Query(default=None),
    status_filter: str | None = Query(default=None),
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
):
    """Return database-authoritative summary metrics for the review queue."""
    from queries import get_canonical_queue_metrics
    return get_canonical_queue_metrics(db, user=user, area=area, status_filter=status_filter)


# ---------------------------------------------------------------------------
# 5. Review Detail Endpoint
# ---------------------------------------------------------------------------

@router.get("/inspections/{inspection_id}")
def get_review_detail(
    inspection_id: int,
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
):
    """Detailed review view for a specific inspection, including assessment,
    conflicts, capture requests, and full finding detail."""
    insp = (
        db.query(Inspection)
        .options(
            joinedload(Inspection.store),
            joinedload(Inspection.inspector),
            selectinload(Inspection.scans).selectinload(Scan.images),
            selectinload(Inspection.scans).selectinload(Scan.findings),
        )
        .filter(Inspection.id == inspection_id)
        .first()
    )
    if insp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inspection not found")

    # Access check
    if user.role != "admin" and insp.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted")

    live_scans = [s for s in (insp.scans or []) if s.duplicate_of is None]

    # Build per-scan review items
    scan_reviews = []
    for scan in live_scans:
        findings = db.query(Finding).filter(Finding.scan_id == scan.id).all()
        item = _build_review_item(insp, scan, findings)
        scan_reviews.append(item)

    # Get assessment from the aggregation layer
    assessment_data = None
    try:
        from rules.aggregation import aggregate_inspection_assessment
        from rules_engine import FindingResult

        all_findings = []
        all_images = []
        captured_panels = set()
        for s in live_scans:
            for img in getattr(s, "images", []) or []:
                all_images.append(img)
                if getattr(img, "panel", None):
                    captured_panels.add(img.panel)
            for f in s.findings or []:
                all_findings.append(FindingResult(
                    check_id=f.check_id, title=f.title,
                    verdict=f.effective_verdict or f.engine_verdict,
                    severity=f.severity, reason=f.reason,
                    observed=f.observed, required=f.required,
                    citation=f.citation, ledger_ref=f.ledger_ref,
                    confidence=f.confidence, limb=f.limb,
                ))
        if all_findings:
            assessment = aggregate_inspection_assessment(
                inspection_id=insp.id,
                findings=all_findings,
                rules_as_at=insp.inspection_date,
                captured_panels=captured_panels,
                images=all_images,
            )
            assessment_data = assessment.to_dict()
    except Exception:
        pass

    # Get audit trail for this inspection's review actions
    review_history = (
        db.query(AuditLog)
        .filter(
            AuditLog.inspection_id == inspection_id,
            AuditLog.action.in_([
                "review_started", "review_resolved", "review_escalated",
                "review_dismissed", "finding_overridden", "finding_remark_added",
                "conflict_resolved", "inspector_remark",
            ]),
        )
        .order_by(AuditLog.seq.desc())
        .limit(50)
        .all()
    )

    history_items = []
    for h in review_history:
        history_items.append({
            "seq": h.seq,
            "action": h.action,
            "user_id": h.user_id,
            "old_value": h.old_value,
            "new_value": h.new_value,
            "reason": h.reason,
            "timestamp": h.timestamp.isoformat() if h.timestamp else None,
        })

    # Store and inspector info
    store_info = None
    if insp.store:
        store_info = {
            "id": insp.store.id, "name": insp.store.name,
            "address": insp.store.address, "city": insp.store.city,
            "district": insp.store.district, "state": insp.store.state,
            "pincode": insp.store.pincode,
        }

    inspector_info = None
    if insp.inspector:
        inspector_info = {
            "id": insp.inspector.id, "employee_id": insp.inspector.employee_id,
            "full_name": insp.inspector.full_name,
            "jurisdiction": insp.inspector.jurisdiction,
        }

    return {
        "inspection_id": insp.id,
        "inspection_date": insp.inspection_date.isoformat(),
        "status": insp.status,
        "transaction_type": insp.transaction_type,
        "in_scope": insp.in_scope,
        "geofence_status": insp.geofence_status,
        "signature_status": insp.signature_status,
        "edited_offline": bool(insp.edited_offline),
        "notes": insp.notes,
        "store": store_info,
        "inspector": inspector_info,
        "scan_reviews": scan_reviews,
        "assessment": assessment_data,
        "review_history": history_items,
        "total_scans": len(live_scans),
    }


# ---------------------------------------------------------------------------
# 6. Adjudication Actions
# ---------------------------------------------------------------------------

@router.post("/inspections/{inspection_id}/adjudicate")
def adjudicate_review(
    inspection_id: int,
    body: AdjudicateRequest,
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
):
    """Officer adjudicates a review item. Status transitions:
    OPEN → IN_REVIEW (implicit on first action)
    IN_REVIEW → RESOLVED (on 'resolve')
    Any → escalated (on 'escalate')

    If finding_id is provided with human_verdict, the specific finding
    is adjudicated. The engine_verdict remains immutable (C11).
    """
    insp = db.get(Inspection, inspection_id)
    if insp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inspection not found")

    if user.role != "admin" and insp.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted")

    # Handle finding-level adjudication
    if body.finding_id is not None:
        finding = db.get(Finding, body.finding_id)
        if finding is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Finding not found")

        scan = db.get(Scan, finding.scan_id)
        if scan is None or scan.inspection_id != inspection_id:
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                detail="Finding does not belong to this inspection")

        old_verdict = finding.human_verdict or finding.engine_verdict

        if body.human_verdict is not None:
            finding.human_verdict = body.human_verdict
        finding.override_reason = body.reason
        finding.overridden_by = user.id
        finding.overridden_at = datetime.now(timezone.utc)
        db.flush()

        # Recompute scan overall_result
        if scan.overall_result != "out_of_scope":
            all_f = db.query(Finding).filter(Finding.scan_id == scan.id).all()
            eff = [(f.human_verdict or f.engine_verdict) for f in all_f]
            if any(v == "fail" for v in eff):
                scan.overall_result = "violation"
            elif eff and all(v == "pass" for v in eff):
                scan.overall_result = "compliant"
            else:
                scan.overall_result = "not_assessed"

        db.commit()

        append_audit(
            db, inspection_id=inspection_id, scan_id=scan.id,
            user_id=user.id,
            action="finding_overridden",
            old_value=f"{finding.check_id}={old_verdict}",
            new_value=f"{finding.check_id}={body.human_verdict or old_verdict}",
            reason=body.reason,
        )

        return {
            "success": True,
            "action": "finding_adjudicated",
            "finding_id": finding.id,
            "check_id": finding.check_id,
            "old_verdict": old_verdict,
            "new_verdict": body.human_verdict or old_verdict,
            "scan_result": scan.overall_result,
        }

    # Handle inspection-level adjudication
    action_map = {
        "resolve": "review_resolved",
        "escalate": "review_escalated",
        "dismiss": "review_dismissed",
        "reassign": "review_reassigned",
        "accept": "review_accepted",
        "override": "review_overridden",
    }
    audit_action = action_map.get(body.action, "review_action")

    append_audit(
        db, inspection_id=inspection_id, user_id=user.id,
        action=audit_action,
        old_value=insp.status,
        new_value=body.action,
        reason=body.reason,
    )

    # Store resolution notes
    if body.resolution_notes:
        existing_notes = insp.notes or ""
        timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        review_note = f"\n[Review {body.action} by {user.full_name} at {timestamp}]: {body.resolution_notes}"
        insp.notes = (existing_notes + review_note).strip()
        db.commit()

    return {
        "success": True,
        "action": body.action,
        "inspection_id": inspection_id,
        "audit_action": audit_action,
        "reason": body.reason,
        "adjudicated_by": user.id,
        "adjudicated_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/adjudicate")
def adjudicate_review_root(
    body: AdjudicateRequest,
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
):
    """Direct alias endpoint for /review/adjudicate with inspection_id in body."""
    if body.inspection_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="inspection_id required in request body")
    return adjudicate_review(inspection_id=body.inspection_id, body=body, user=user, db=db)


@router.post("/inspections/{inspection_id}/conflicts/{conflict_id}/resolve")
def resolve_conflict(
    inspection_id: int,
    conflict_id: str,
    body: ConflictResolutionRequest,
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
):
    """Resolve a specific cross-panel conflict identified by the aggregation layer."""
    insp = db.get(Inspection, inspection_id)
    if insp is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Inspection not found")

    if user.role != "admin" and insp.user_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Not permitted")

    append_audit(
        db, inspection_id=inspection_id, user_id=user.id,
        action="conflict_resolved",
        old_value=conflict_id,
        new_value=json.dumps({
            "resolution": body.resolution,
            "resolved_value": body.resolved_value,
        }),
        reason=body.reason,
    )

    return {
        "success": True,
        "conflict_id": conflict_id,
        "resolution": body.resolution,
        "resolved_by": user.id,
        "resolved_at": datetime.now(timezone.utc).isoformat(),
    }


@router.post("/resolve-conflict")
def resolve_conflict_root(
    body: ConflictResolutionRequest,
    user: User = Depends(require_inspector),
    db: Session = Depends(get_db),
):
    """Direct alias endpoint for /review/resolve-conflict with inspection_id in body."""
    if body.inspection_id is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="inspection_id required in request body")
    return resolve_conflict(inspection_id=body.inspection_id, conflict_id=body.conflict_id, body=body, user=user, db=db)


# ---------------------------------------------------------------------------
# 7. Bulk Operations
# ---------------------------------------------------------------------------

@router.post("/queue/bulk-adjudicate")
def bulk_adjudicate(
    body: BulkAdjudicateRequest,
    user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Bulk resolve or escalate multiple review items. Admin only."""
    results = []
    for insp_id in body.inspection_ids:
        insp = db.get(Inspection, insp_id)
        if insp is None:
            results.append({"inspection_id": insp_id, "ok": False, "error": "not_found"})
            continue

        action_map = {
            "resolve": "review_resolved",
            "escalate": "review_escalated",
        }
        audit_action = action_map.get(body.action, "review_action")

        append_audit(
            db, inspection_id=insp_id, user_id=user.id,
            action=audit_action,
            old_value="bulk",
            new_value=body.action,
            reason=body.reason,
        )
        results.append({"inspection_id": insp_id, "ok": True, "action": body.action})

    return {
        "total": len(results),
        "succeeded": sum(1 for r in results if r["ok"]),
        "failed": sum(1 for r in results if not r["ok"]),
        "results": results,
    }


# ---------------------------------------------------------------------------
# 8. Review Statistics (for dashboard integration)
# ---------------------------------------------------------------------------

@router.get("/stats")
def review_stats(
    days: int = Query(default=30, ge=1, le=365),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Review queue statistics for the dashboard."""
    end = date.today()
    start = end - timedelta(days=days)

    from queries import LIVE

    base = (
        db.query(Scan)
        .join(Inspection, Inspection.id == Scan.inspection_id)
        .filter(LIVE, Inspection.inspection_date.between(start, end))
    )
    if user.role != "admin":
        base = base.filter(Inspection.user_id == user.id)

    total_scans = base.count()
    na_scans = base.filter(Scan.overall_result == "not_assessed").count()
    violation_scans = base.filter(Scan.overall_result == "violation").count()
    compliant_scans = base.filter(Scan.overall_result == "compliant").count()

    # Resolution rate: how many review items were resolved
    resolved_count = (
        db.query(func.count(AuditLog.id))
        .filter(
            AuditLog.action == "review_resolved",
            AuditLog.timestamp >= datetime(start.year, start.month, start.day, tzinfo=timezone.utc),
        )
    )
    if user.role != "admin":
        resolved_count = resolved_count.filter(AuditLog.user_id == user.id)
    resolved_count = resolved_count.scalar() or 0

    # Average time to resolution (from the last 50 resolved items)
    avg_resolution_hours = None
    # Simplified: not computed to keep deterministic

    return {
        "period_start": start.isoformat(),
        "period_end": end.isoformat(),
        "total_scans": total_scans,
        "pending_review": na_scans + violation_scans,
        "not_assessed": na_scans,
        "violations_pending": violation_scans,
        "compliant": compliant_scans,
        "resolved_in_period": resolved_count,
        "resolution_rate": (
            round(resolved_count / (na_scans + violation_scans + resolved_count) * 100, 1)
            if (na_scans + violation_scans + resolved_count) > 0 else 0.0
        ),
    }
