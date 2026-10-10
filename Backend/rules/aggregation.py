"""Backend/rules/aggregation.py — Multi-Panel Evidence Aggregation & Final Assessment (Phase 4D).

Core Invariants (Sections 1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17):
- OCR READS -> LLM STRUCTURES -> RULES DECIDE -> AGGREGATION ASSESSES.
- Zero AI / LLM / OCR calls. Operates deterministically on already evaluated findings and structured evidence.
- Multi-panel evidence aggregation: preserves full evidence provenance per panel, image, and coordinate.
- Cross-panel consistency:
  * Detects conflicting MRP across panels (produces REVIEW_REQUIRED conflict).
  * Detects conflicting Net Quantity across panels (produces REVIEW_REQUIRED conflict).
  * Detects conflicting Date across panels within same semantic date type (MFG vs MFG; EXP vs EXP).
  * Detects conflicting Party declarations across panels using deterministic normalization only.
- Deterministic conflict model: StatutoryConflict with conflict_id, field, conflicting_values, evidence_refs, severity.
- Finding deduplication: eliminates duplicate findings across OCR lines/artifacts while preserving all evidence references.
- Rule result precedence: Confirmed FAIL -> VIOLATION; Unresolved conflict or mandatory NOT_ASSESSED -> REVIEW_REQUIRED; Complete clean pass -> COMPLIANT.
- Explicit assessment completeness model (COMPLETE vs PARTIAL). Incomplete coverage can never be COMPLIANT.
- Violation Dossier and Review Dossier generators for audit and officer workflows.
- Actionable structured capture requests to guide field inspectors.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import Any

from llm.schema import FieldProvenance
from rules.loader import load_rule_pack
from rules_engine import CheckContext, FindingResult, ScanVerdict


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Evidence Provenance & References (Section 4 & 13)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EvidenceReference:
    """Traceable pointer to physical or digital evidence (Section 4 & 13)."""
    panel: str
    inspection_id: str | int | None = None
    scan_id: str | int | None = None
    image_id: str | int | None = None
    artifact_type: str = "original"  # original | rectified_panel | analysis_image | ocr_derived | crop
    source_text: str | None = None
    source_bbox: list[Any] | None = None
    measurement_data: dict[str, Any] | None = None
    file_path: str | None = None
    sha256: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class EvidenceSummary:
    """Consolidated summary of evidence captured across panels."""
    total_panels_captured: int = 0
    panels_captured: list[str] = field(default_factory=list)
    total_images: int = 0
    images_by_panel: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    pdp_established: bool = False
    established_pdp_panel: str | None = None
    scale_source: str | None = None
    mm_per_pixel: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 2. Conflict Model (Section 8)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class StatutoryConflict:
    """Deterministic conflict object representing contradictory evidence across panels (Section 8)."""
    conflict_id: str
    inspection_id: str
    field: str
    conflicting_values: list[Any]
    evidence_refs: list[dict[str, Any]]
    severity: str  # critical | major | minor
    reason: str
    resolution_status: str = "unresolved"  # unresolved | resolved_by_rule | resolved_by_officer

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 3. Assessment Completeness Model (Section 11)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class AssessmentCompleteness:
    """Explicit assessment coverage and completeness metrics (Section 11)."""
    status: str  # COMPLETE | PARTIAL
    total_applicable_rules: int = 0
    evaluated_rules: int = 0
    passed_rules: int = 0
    failed_rules: int = 0
    not_assessed_rules: int = 0
    not_applicable_rules: int = 0
    unresolved_conflicts: int = 0
    coverage_sufficient: bool = False
    missing_panels: list[str] = field(default_factory=list)
    evaluated_percentage: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 4. Actionable Capture Request Model (Section 17)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CaptureRequest:
    """Actionable structured capture guidance for field inspectors (Section 17)."""
    request_id: str
    target_panel: str
    reason: str
    expected_declaration_or_evidence: str
    priority: str  # high | medium | low
    suggested_action: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# 5. Violation and Review Dossier Models (Section 15 & 16)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ViolationItem:
    """Single evidence-backed statutory violation record (Section 15)."""
    rule_id: str
    rule_code: str
    title: str
    severity: str
    statutory_limb: str | None
    reason: str
    observed: str | None
    required: str | None
    remediation: str | None
    evidence: list[dict[str, Any]]
    legal_provenance: dict[str, Any]
    inspector_explanation: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ViolationDossier:
    """Audit-ready violation compilation for enforcement under Section 36 (Section 15)."""
    inspection_id: str
    dossier_id: str
    generated_at: str
    violations: list[ViolationItem]
    total_violations: int
    affected_limbs: list[str]
    statutory_summary: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspection_id": self.inspection_id,
            "dossier_id": self.dossier_id,
            "generated_at": self.generated_at,
            "violations": [v.to_dict() for v in self.violations],
            "total_violations": self.total_violations,
            "affected_limbs": self.affected_limbs,
            "statutory_summary": self.statutory_summary,
        }


@dataclass(slots=True)
class ReviewItem:
    """Actionable review matter requiring inspector judgment or recapture (Section 16)."""
    item_id: str
    review_type: str  # conflict | unassessed_rule | missing_evidence | measurement_uncertainty | incomplete_coverage
    affected_rule: str | None
    affected_panel: str | None
    title: str
    reason: str
    evidence: list[dict[str, Any]]
    conflicting_records: list[Any] | None
    required_inspector_action: str
    priority: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class ReviewDossier:
    """Audit-ready review dossier when inspection cannot be certified compliant (Section 16)."""
    inspection_id: str
    dossier_id: str
    generated_at: str
    review_items: list[ReviewItem]
    total_review_items: int
    unresolved_conflicts_count: int
    not_assessed_rules_count: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspection_id": self.inspection_id,
            "dossier_id": self.dossier_id,
            "generated_at": self.generated_at,
            "review_items": [r.to_dict() for r in self.review_items],
            "total_review_items": self.total_review_items,
            "unresolved_conflicts_count": self.unresolved_conflicts_count,
            "not_assessed_rules_count": self.not_assessed_rules_count,
        }


# ---------------------------------------------------------------------------
# 6. Final Inspection Assessment Result Model (Section 14)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class InspectionAssessment:
    """Complete, self-contained multi-panel inspection assessment (Section 14)."""
    inspection_id: str
    overall_verdict: str  # COMPLIANT | VIOLATION | REVIEW_REQUIRED | OUT_OF_SCOPE
    assessment_completeness: AssessmentCompleteness
    rule_pack_version: str
    rules_as_at: str
    evaluated_at: str
    total_rules: int
    passed_rules: int
    failed_rules: int
    not_assessed_rules: int
    not_applicable_rules: int
    conflicts: list[StatutoryConflict]
    findings: list[FindingResult]
    review_items: list[ReviewItem]
    evidence_summary: EvidenceSummary
    violation_dossier: ViolationDossier | None = None
    review_dossier: ReviewDossier | None = None
    actionable_capture_requests: list[CaptureRequest] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inspection_id": self.inspection_id,
            "overall_verdict": self.overall_verdict,
            "assessment_completeness": self.assessment_completeness.to_dict(),
            "rule_pack_version": self.rule_pack_version,
            "rules_as_at": self.rules_as_at,
            "evaluated_at": self.evaluated_at,
            "total_rules": self.total_rules,
            "passed_rules": self.passed_rules,
            "failed_rules": self.failed_rules,
            "not_assessed_rules": self.not_assessed_rules,
            "not_applicable_rules": self.not_applicable_rules,
            "conflicts": [c.to_dict() for c in self.conflicts],
            "findings": [f.to_dict() if hasattr(f, "to_dict") else asdict(f) for f in self.findings],
            "review_items": [r.to_dict() for r in self.review_items],
            "evidence_summary": self.evidence_summary.to_dict(),
            "violation_dossier": self.violation_dossier.to_dict() if self.violation_dossier else None,
            "review_dossier": self.review_dossier.to_dict() if self.review_dossier else None,
            "actionable_capture_requests": [req.to_dict() for req in self.actionable_capture_requests],
        }


# ---------------------------------------------------------------------------
# 7. Deterministic Normalization Utilities (Section 7)
# ---------------------------------------------------------------------------

def normalize_party_text(text: str | None) -> str:
    """Deterministic normalization for party names and addresses (Section 7).

    Preserves exact legal meaning while neutralizing case, punctuation, whitespace,
    and standard corporate/geographical abbreviations without fuzzy AI similarity.
    """
    if not text:
        return ""
    s = text.lower().strip()
    # Punctuation normalization
    s = re.sub(r"[,\.;:\-\(\)\[\]/\\_#]", " ", s)
    s = re.sub(r"\s+", " ", s).strip()

    # Standard corporate and geographical abbreviation expansion
    abbrevs = [
        (r"\bpvt\s+ltd\b", "private limited"),
        (r"\bpvt\b", "private"),
        (r"\bltd\b", "limited"),
        (r"\bmfg\b", "manufacturer"),
        (r"\bmfr\b", "manufacturer"),
        (r"\bco\b", "company"),
        (r"\bcorp\b", "corporation"),
        (r"\binc\b", "incorporated"),
        (r"\bst\b", "street"),
        (r"\brd\b", "road"),
        (r"\bave\b", "avenue"),
        (r"\bplot\s+no\b", "plot"),
        (r"\bno\b", "number"),
        (r"\bopp\b", "opposite"),
        (r"\bnr\b", "near"),
        (r"\bindl\b", "industrial"),
        (r"\bind\b", "industrial"),
        (r"\bdist\b", "district"),
    ]
    for pattern, repl in abbrevs:
        s = re.sub(pattern, repl, s)

    return re.sub(r"\s+", " ", s).strip()


def normalize_commodity_name(text: str | None) -> str:
    """Deterministic normalization for commodity names."""
    if not text:
        return ""
    s = text.lower().strip()
    s = re.sub(r"[,\.;:\-\(\)\[\]/]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def normalize_date_semantic(raw_or_norm: str | None) -> str:
    """Normalize date string to standard comparable format (YYYY-MM-DD or YYYY-MM)."""
    if not raw_or_norm:
        return ""
    s = raw_or_norm.strip()
    if re.match(r"^\d{4}-\d{2}(-\d{2})?$", s):
        return s
    m_full = re.match(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$", s)
    if m_full:
        d, mth, y = m_full.groups()
        return f"{y}-{int(mth):02d}-{int(d):02d}"
    m_my = re.match(r"^(\d{1,2})[/.-](\d{4})$", s)
    if m_my:
        mth, y = m_my.groups()
        return f"{y}-{int(mth):02d}"
    return s.lower()


# ---------------------------------------------------------------------------
# 8. Cross-Panel Consistency & Conflict Detection (Sections 5, 6, 7, 8)
# ---------------------------------------------------------------------------

def detect_cross_panel_conflicts(
    inspection_id: str,
    llm_result: Any | None = None,
    panel_declarations: dict[str, Any] | list[Any] | None = None,
) -> list[StatutoryConflict]:
    """Detect conflicting declarations across different panels (Sections 5, 6, 7, 8).

    Examines:
    - Retail sale price (MRP) dual/conflicting values under Rule 6(2A)
    - Net quantity numeral/unit differences across panels
    - Date inconsistencies within identical statutory types (MFG vs MFG, EXP vs EXP)
    - Party inconsistencies after deterministic normalization
    - Materially conflicting commodity names
    """
    conflicts: list[StatutoryConflict] = []

    # Assemble panel-attributed declarations
    # 1. Extract MRP declarations across panels
    mrp_records: list[dict[str, Any]] = []
    # 2. Extract Net Quantity declarations across panels
    nq_records: list[dict[str, Any]] = []
    # 3. Extract Dates across panels
    date_records: list[dict[str, Any]] = []
    # 4. Extract Parties across panels
    party_records: list[dict[str, Any]] = []
    # 5. Extract Commodity Names across panels
    comm_records: list[dict[str, Any]] = []

    # Ingestion path A: panel_declarations dictionary or list
    if panel_declarations:
        items = (
            panel_declarations.items()
            if isinstance(panel_declarations, dict)
            else [(getattr(d, "panel", f"panel_{i}"), d) for i, d in enumerate(panel_declarations)]
        )
        for panel, decl in items:
            if not decl:
                continue
            # MRP
            m = getattr(decl, "mrp", None)
            if m and getattr(m, "value", None) is not None:
                prov = getattr(m, "provenance", None)
                mrp_records.append({
                    "panel": getattr(prov, "source_panel", None) or panel,
                    "value": float(m.value),
                    "raw_text": getattr(m, "raw_text", str(m.value)),
                    "provenance": prov,
                })
            # Net quantity
            nq = getattr(decl, "net_quantity", None)
            if nq and getattr(nq, "value", None) is not None:
                prov = getattr(nq, "provenance", None)
                nq_records.append({
                    "panel": getattr(prov, "source_panel", None) or panel,
                    "value": float(nq.value),
                    "unit": (getattr(nq, "unit", "") or "").lower(),
                    "norm_value": getattr(nq, "normalized_value", None) or float(nq.value),
                    "norm_unit": (getattr(nq, "normalized_unit", None) or getattr(nq, "unit", "") or "").lower(),
                    "raw_text": getattr(nq, "raw_text", f"{nq.value} {nq.unit or ''}"),
                    "provenance": prov,
                })
            # Dates
            for d in getattr(decl, "dates", []) or []:
                if getattr(d, "status", None) == "confirmed" or getattr(d, "normalized_date", None):
                    prov = getattr(d, "provenance", None)
                    date_records.append({
                        "panel": getattr(prov, "source_panel", None) or panel,
                        "type": getattr(d, "date_type_candidate", "unknown") or "unknown",
                        "date": normalize_date_semantic(getattr(d, "normalized_date", None) or getattr(d, "raw_text", None)),
                        "raw_text": getattr(d, "raw_text", ""),
                        "provenance": prov,
                    })
            # Parties
            for p in getattr(decl, "parties", []) or []:
                if getattr(p, "name", None):
                    prov = getattr(p, "provenance", None)
                    party_records.append({
                        "panel": getattr(prov, "source_panel", None) or panel,
                        "type": getattr(p, "party_type", "manufacturer") or "manufacturer",
                        "name": getattr(p, "name", ""),
                        "address": getattr(p, "address", ""),
                        "provenance": prov,
                    })
            # Commodity name
            c = getattr(decl, "commodity_name", None)
            if c and getattr(c, "value", None):
                prov = getattr(c, "provenance", None)
                comm_records.append({
                    "panel": getattr(prov, "source_panel", None) or panel,
                    "name": getattr(c, "value", ""),
                    "provenance": prov,
                })

    # Ingestion path B: single StructuredDeclarationResult with candidates or multi-panel records
    if llm_result:
        # MRP & candidates
        m = getattr(llm_result, "mrp", None)
        if m and getattr(m, "value", None) is not None:
            prov = getattr(m, "provenance", None)
            panel = getattr(prov, "source_panel", "front") or "front"
            if not any(r["panel"] == panel and abs(r["value"] - float(m.value)) < 0.01 for r in mrp_records):
                mrp_records.append({
                    "panel": panel,
                    "value": float(m.value),
                    "raw_text": getattr(m, "raw_text", str(m.value)),
                    "provenance": prov,
                })
        for cand in getattr(m, "candidates", []) or []:
            c_val = getattr(cand, "value", None)
            c_pan = getattr(cand, "panel", None)
            if c_val is not None and c_pan:
                try:
                    f_val = float(str(c_val).replace("₹", "").replace(",", "").strip())
                    if not any(r["panel"] == c_pan and abs(r["value"] - f_val) < 0.01 for r in mrp_records):
                        mrp_records.append({
                            "panel": c_pan,
                            "value": f_val,
                            "raw_text": getattr(cand, "raw_text", str(c_val)),
                            "provenance": FieldProvenance(source_panel=c_pan, source_text=str(c_val)),
                        })
                except (ValueError, TypeError):
                    pass

        # Net quantity & candidates
        nq = getattr(llm_result, "net_quantity", None)
        if nq and getattr(nq, "value", None) is not None:
            prov = getattr(nq, "provenance", None)
            panel = getattr(prov, "source_panel", "front") or "front"
            if not any(r["panel"] == panel and abs(r["value"] - float(nq.value)) < 0.01 for r in nq_records):
                nq_records.append({
                    "panel": panel,
                    "value": float(nq.value),
                    "unit": (getattr(nq, "unit", "") or "").lower(),
                    "norm_value": getattr(nq, "normalized_value", None) or float(nq.value),
                    "norm_unit": (getattr(nq, "normalized_unit", None) or getattr(nq, "unit", "") or "").lower(),
                    "raw_text": getattr(nq, "raw_text", f"{nq.value} {nq.unit or ''}"),
                    "provenance": prov,
                })
        for cand in getattr(nq, "candidates", []) or []:
            c_val = getattr(cand, "value", None)
            c_pan = getattr(cand, "panel", None)
            if c_val is not None and c_pan:
                try:
                    f_val = float(str(c_val).split()[0].replace(",", ""))
                    u_cand = str(c_val).split()[-1] if len(str(c_val).split()) > 1 else ""
                    if not any(r["panel"] == c_pan and abs(r["value"] - f_val) < 0.01 for r in nq_records):
                        nq_records.append({
                            "panel": c_pan,
                            "value": f_val,
                            "unit": u_cand.lower(),
                            "norm_value": f_val,
                            "norm_unit": u_cand.lower(),
                            "raw_text": getattr(cand, "raw_text", str(c_val)),
                            "provenance": FieldProvenance(source_panel=c_pan, source_text=str(c_val)),
                        })
                except (ValueError, TypeError):
                    pass

        # Dates
        for d in getattr(llm_result, "dates", []) or []:
            if getattr(d, "status", None) == "confirmed" or getattr(d, "normalized_date", None):
                prov = getattr(d, "provenance", None)
                p = getattr(prov, "source_panel", None) or "front"
                date_records.append({
                    "panel": p,
                    "type": getattr(d, "date_type_candidate", "unknown") or "unknown",
                    "date": normalize_date_semantic(getattr(d, "normalized_date", None) or getattr(d, "raw_text", None)),
                    "raw_text": getattr(d, "raw_text", ""),
                    "provenance": prov,
                })

        # Parties
        for p in getattr(llm_result, "parties", []) or []:
            if getattr(p, "name", None):
                prov = getattr(p, "provenance", None)
                pan = getattr(prov, "source_panel", None) or "front"
                party_records.append({
                    "panel": pan,
                    "type": getattr(p, "party_type", "manufacturer") or "manufacturer",
                    "name": getattr(p, "name", ""),
                    "address": getattr(p, "address", ""),
                    "provenance": prov,
                })

        # Commodity name
        c = getattr(llm_result, "commodity_name", None)
        if c and getattr(c, "value", None):
            prov = getattr(c, "provenance", None)
            pan = getattr(prov, "source_panel", None) or "front"
            comm_records.append({
                "panel": pan,
                "name": getattr(c, "value", ""),
                "provenance": prov,
            })

    # --- Conflict Detection Logic ---

    # 1. MRP Conflict (Section 5)
    if len(mrp_records) > 1:
        # Check if values differ across distinct panels
        distinct_mrp = {}
        for r in mrp_records:
            distinct_mrp.setdefault(r["panel"], []).append(r)
        panels = list(distinct_mrp.keys())
        for i in range(len(panels)):
            for j in range(i + 1, len(panels)):
                p1, p2 = panels[i], panels[j]
                v1 = distinct_mrp[p1][0]["value"]
                v2 = distinct_mrp[p2][0]["value"]
                if abs(v1 - v2) > 0.01:
                    c_id = f"CONF_MRP_{inspection_id}_{p1}_{p2}"
                    refs = [
                        {"panel": p1, "value": v1, "text": distinct_mrp[p1][0].get("raw_text")},
                        {"panel": p2, "value": v2, "text": distinct_mrp[p2][0].get("raw_text")},
                    ]
                    conflicts.append(StatutoryConflict(
                        conflict_id=c_id,
                        inspection_id=str(inspection_id),
                        field="mrp",
                        conflicting_values=[{"panel": p1, "amount": v1}, {"panel": p2, "amount": v2}],
                        evidence_refs=refs,
                        severity="critical",
                        reason=(
                            f"Conflicting retail sale prices (MRP) observed across panels: "
                            f"₹{v1:.2f} on '{p1}' vs ₹{v2:.2f} on '{p2}'. "
                            f"Rule 6(2A) prohibits dual/conflicting retail pricing."
                        ),
                    ))

    # 2. Net Quantity Conflict (Section 5)
    if len(nq_records) > 1:
        distinct_nq = {}
        for r in nq_records:
            distinct_nq.setdefault(r["panel"], []).append(r)
        panels = list(distinct_nq.keys())
        for i in range(len(panels)):
            for j in range(i + 1, len(panels)):
                p1, p2 = panels[i], panels[j]
                r1, r2 = distinct_nq[p1][0], distinct_nq[p2][0]
                # Compare normalized value and unit
                differs = False
                if r1["norm_unit"] == r2["norm_unit"]:
                    if abs(r1["norm_value"] - r2["norm_value"]) > 0.001:
                        differs = True
                else:
                    differs = True

                if differs:
                    c_id = f"CONF_NQ_{inspection_id}_{p1}_{p2}"
                    conflicts.append(StatutoryConflict(
                        conflict_id=c_id,
                        inspection_id=str(inspection_id),
                        field="net_quantity",
                        conflicting_values=[
                            {"panel": p1, "value": r1["value"], "unit": r1["unit"]},
                            {"panel": p2, "value": r2["value"], "unit": r2["unit"]},
                        ],
                        evidence_refs=[
                            {"panel": p1, "text": r1.get("raw_text"), "value": r1["value"]},
                            {"panel": p2, "text": r2.get("raw_text"), "value": r2["value"]},
                        ],
                        severity="critical",
                        reason=(
                            f"Conflicting net quantity declarations observed across panels: "
                            f"'{r1['value']} {r1['unit']}' on '{p1}' vs '{r2['value']} {r2['unit']}' on '{p2}'."
                        ),
                    ))

    # 3. Date Consistency (Section 6)
    # Group dates by statutory type: mfg, packing, expiry, best_before
    dates_by_type: dict[str, list[dict[str, Any]]] = {}
    for r in date_records:
        t = r["type"].lower()
        if t in ("mfg", "manufacturing", "date_of_manufacture"):
            dates_by_type.setdefault("mfg", []).append(r)
        elif t in ("pkd", "packing", "date_of_packing"):
            dates_by_type.setdefault("packing", []).append(r)
        elif t in ("exp", "expiry", "date_of_expiry"):
            dates_by_type.setdefault("expiry", []).append(r)
        elif t in ("best_before", "use_by"):
            dates_by_type.setdefault("best_before", []).append(r)

    for dtype, recs in dates_by_type.items():
        if len(recs) > 1:
            panels_map = {}
            for r in recs:
                panels_map.setdefault(r["panel"], []).append(r)
            panels = list(panels_map.keys())
            for i in range(len(panels)):
                for j in range(i + 1, len(panels)):
                    p1, p2 = panels[i], panels[j]
                    d1, d2 = panels_map[p1][0]["date"], panels_map[p2][0]["date"]
                    if d1 and d2 and d1 != d2:
                        c_id = f"CONF_DATE_{dtype.upper()}_{inspection_id}_{p1}_{p2}"
                        conflicts.append(StatutoryConflict(
                            conflict_id=c_id,
                            inspection_id=str(inspection_id),
                            field=f"date_{dtype}",
                            conflicting_values=[{"panel": p1, "date": d1}, {"panel": p2, "date": d2}],
                            evidence_refs=[
                                {"panel": p1, "text": panels_map[p1][0].get("raw_text"), "date": d1},
                                {"panel": p2, "text": panels_map[p2][0].get("raw_text"), "date": d2},
                            ],
                            severity="major",
                            reason=(
                                f"Conflicting {dtype.upper()} dates observed across panels: "
                                f"'{d1}' on '{p1}' vs '{d2}' on '{p2}'."
                            ),
                        ))

    # 4. Party Consistency (Section 7)
    parties_by_type: dict[str, list[dict[str, Any]]] = {}
    for r in party_records:
        ptype = r["type"].lower()
        parties_by_type.setdefault(ptype, []).append(r)

    for ptype, recs in parties_by_type.items():
        if len(recs) > 1:
            panels_map = {}
            for r in recs:
                panels_map.setdefault(r["panel"], []).append(r)
            panels = list(panels_map.keys())
            for i in range(len(panels)):
                for j in range(i + 1, len(panels)):
                    p1, p2 = panels[i], panels[j]
                    n1 = normalize_party_text(panels_map[p1][0]["name"])
                    n2 = normalize_party_text(panels_map[p2][0]["name"])
                    if n1 and n2 and n1 != n2:
                        c_id = f"CONF_PARTY_{ptype.upper()}_{inspection_id}_{p1}_{p2}"
                        conflicts.append(StatutoryConflict(
                            conflict_id=c_id,
                            inspection_id=str(inspection_id),
                            field=f"party_{ptype}_name",
                            conflicting_values=[
                                {"panel": p1, "name": panels_map[p1][0]["name"]},
                                {"panel": p2, "name": panels_map[p2][0]["name"]},
                            ],
                            evidence_refs=[
                                {"panel": p1, "name": panels_map[p1][0]["name"], "normalized": n1},
                                {"panel": p2, "name": panels_map[p2][0]["name"], "normalized": n2},
                            ],
                            severity="major",
                            reason=(
                                f"Conflicting {ptype} names observed across panels: "
                                f"'{panels_map[p1][0]['name']}' on '{p1}' vs '{panels_map[p2][0]['name']}' on '{p2}'."
                            ),
                        ))

    # 5. Commodity Name Consistency (Section 5)
    if len(comm_records) > 1:
        panels_map = {}
        for r in comm_records:
            panels_map.setdefault(r["panel"], []).append(r)
        panels = list(panels_map.keys())
        for i in range(len(panels)):
            for j in range(i + 1, len(panels)):
                p1, p2 = panels[i], panels[j]
                c1 = normalize_commodity_name(panels_map[p1][0]["name"])
                c2 = normalize_commodity_name(panels_map[p2][0]["name"])
                if c1 and c2 and c1 != c2:
                    c_id = f"CONF_COMMODITY_{inspection_id}_{p1}_{p2}"
                    conflicts.append(StatutoryConflict(
                        conflict_id=c_id,
                        inspection_id=str(inspection_id),
                        field="commodity_name",
                        conflicting_values=[
                            {"panel": p1, "commodity": panels_map[p1][0]["name"]},
                            {"panel": p2, "commodity": panels_map[p2][0]["name"]},
                        ],
                        evidence_refs=[
                            {"panel": p1, "text": panels_map[p1][0]["name"]},
                            {"panel": p2, "text": panels_map[p2][0]["name"]},
                        ],
                        severity="major",
                        reason=(
                            f"Conflicting generic commodity names observed across panels: "
                            f"'{panels_map[p1][0]['name']}' on '{p1}' vs '{panels_map[p2][0]['name']}' on '{p2}'."
                        ),
                    ))

    return conflicts


# ---------------------------------------------------------------------------
# 9. Finding Deduplication (Section 9)
# ---------------------------------------------------------------------------

def deduplicate_findings(findings: list[FindingResult]) -> list[FindingResult]:
    """Deduplicate findings deterministically across multiple OCR lines and panel artifacts (Section 9).

    Precedence rules:
    - If at least one instance of a rule is FAIL, the FAIL verdict wins (confirmed statutory non-compliance).
    - If all instances are PASS, combines into a single PASS with aggregated evidence references.
    - If an instance is PASS and an instance is NOT_ASSESSED, PASS takes precedence (e.g. declaration satisfied on PDP).
    - Preserves all supporting evidence references in `evidence_provenance["supporting_evidence"]`.
    """
    if not findings:
        return []

    grouped: dict[str, list[FindingResult]] = {}
    for f in findings:
        key = f.rule_id or f.check_id
        grouped.setdefault(key, []).append(f)

    deduped: list[FindingResult] = []
    for key, items in grouped.items():
        if len(items) == 1:
            deduped.append(items[0])
            continue

        # Multiple findings for the same statutory rule: resolve precedence
        fails = [i for i in items if i.verdict == "fail"]
        passes = [i for i in items if i.verdict == "pass"]
        not_assessed = [i for i in items if i.verdict == "not_assessed"]

        # 1. FAIL outranks everything
        if fails:
            primary = fails[0]
            # Merge supporting evidence from all failing items
            supporting = []
            for item in items:
                prov = getattr(item, "evidence_provenance", {}) or {}
                supporting.append(prov)
            merged_prov = dict(getattr(primary, "evidence_provenance", {}) or {})
            merged_prov["supporting_evidence"] = supporting
            primary.evidence_provenance = merged_prov
            deduped.append(primary)
        # 2. PASS outranks NOT_ASSESSED
        elif passes:
            primary = passes[0]
            supporting = []
            for item in items:
                prov = getattr(item, "evidence_provenance", {}) or {}
                supporting.append(prov)
            merged_prov = dict(getattr(primary, "evidence_provenance", {}) or {})
            merged_prov["supporting_evidence"] = supporting
            primary.evidence_provenance = merged_prov
            deduped.append(primary)
        # 3. NOT_ASSESSED
        else:
            primary = not_assessed[0]
            supporting = [getattr(item, "evidence_provenance", {}) or {} for item in items]
            merged_prov = dict(getattr(primary, "evidence_provenance", {}) or {})
            merged_prov["supporting_evidence"] = supporting
            primary.evidence_provenance = merged_prov
            deduped.append(primary)

    # Sort deterministically by check_id
    deduped.sort(key=lambda x: x.check_id)
    return deduped


# ---------------------------------------------------------------------------
# 10. Assessment Completeness Determination (Section 11)
# ---------------------------------------------------------------------------

def determine_assessment_completeness(
    findings: list[FindingResult],
    captured_panels: set[str],
    inspection_source: str = "physical_package",
    unresolved_conflicts_count: int = 0,
    coverage_sufficient_override: bool | None = None,
) -> AssessmentCompleteness:
    """Evaluate completeness and coverage of the statutory assessment (Section 11).

    Incomplete coverage must NEVER produce a positive COMPLIANT outcome.
    """
    total = len(findings)
    passed = sum(1 for f in findings if f.verdict == "pass")
    failed = sum(1 for f in findings if f.verdict == "fail")
    not_assessed = sum(1 for f in findings if f.verdict == "not_assessed")
    not_applicable = 0  # Internal non-applicable rules

    # Assess physical package coverage requirements
    missing_panels = []
    coverage_sufficient = True

    if coverage_sufficient_override is not None:
        coverage_sufficient = coverage_sufficient_override
    elif inspection_source == "physical_package":
        # Physical retail package requires at least front/PDP and back/side surfaces
        # to confirm all 7 mandatory declarations
        has_front = bool(captured_panels & {"front", "principal"})
        has_rear_or_side = bool(captured_panels & {"back", "side", "rear", "bottom", "top"})

        if not has_front:
            missing_panels.append("front/principal")
            coverage_sufficient = False
        if not has_rear_or_side and len(captured_panels) < 2:
            missing_panels.append("back/side")
            coverage_sufficient = False

    is_complete = (
        coverage_sufficient
        and not_assessed == 0
        and unresolved_conflicts_count == 0
    )

    status = "COMPLETE" if is_complete else "PARTIAL"
    evaluated_pct = round((passed + failed) / max(total, 1) * 100.0, 1)

    return AssessmentCompleteness(
        status=status,
        total_applicable_rules=total,
        evaluated_rules=passed + failed,
        passed_rules=passed,
        failed_rules=failed,
        not_assessed_rules=not_assessed,
        not_applicable_rules=not_applicable,
        unresolved_conflicts=unresolved_conflicts_count,
        coverage_sufficient=coverage_sufficient,
        missing_panels=missing_panels,
        evaluated_percentage=evaluated_pct,
    )


# ---------------------------------------------------------------------------
# 11. Actionable Capture Request Generator (Section 17)
# ---------------------------------------------------------------------------

def generate_actionable_capture_requests(
    findings: list[FindingResult],
    completeness: AssessmentCompleteness,
    conflicts: list[StatutoryConflict],
    ctx: CheckContext | None = None,
) -> list[CaptureRequest]:
    """Generate structured, actionable capture requests when additional evidence could resolve review conditions (Section 17)."""
    requests: list[CaptureRequest] = []

    # 1. Missing panels
    for mp in completeness.missing_panels:
        target = "back" if "back" in mp else ("front" if "front" in mp else mp)
        requests.append(CaptureRequest(
            request_id=f"CAP_PANEL_{target.upper()}",
            target_panel=target,
            reason=f"Missing {target} panel prevents complete statutory verification of mandatory packaging declarations.",
            expected_declaration_or_evidence=f"{target} panel packaging declarations",
            priority="high",
            suggested_action=f"Capture the {target} panel of the package in clear, well-lit conditions.",
        ))

    # 2. Missing optical scale / uncalibrated geometry
    needs_scale = any(
        f.verdict == "not_assessed"
        and ("calibration" in (f.reason or "").lower() or "scale" in (f.reason or "").lower())
        for f in findings
    )
    if needs_scale:
        requests.append(CaptureRequest(
            request_id="CAP_SCALE_REFERENCE",
            target_panel="principal",
            reason="Physical character and numeral height measurement requires a validated optical calibration standard.",
            expected_declaration_or_evidence="ID-1 reference card or 5-INR coin placed on PDP",
            priority="high",
            suggested_action="Re-photograph the principal display panel with an ID-1 card (standard bank card) or 5-INR coin positioned alongside the net quantity.",
        ))

    # 3. Clear space / glare / image quality
    needs_clear_space = any(
        f.check_id == "CHK09" and f.verdict == "not_assessed"
        for f in findings
    )
    if needs_clear_space:
        requests.append(CaptureRequest(
            request_id="CAP_CLEAR_SPACE_CLEAN",
            target_panel="principal",
            reason="Presence of interfering background graphics or glare prevented clear-space confirmation around net quantity.",
            expected_declaration_or_evidence="unobstructed clear space surrounding net quantity",
            priority="medium",
            suggested_action="Capture a non-glare, orthogonal image focused directly on the net quantity declaration.",
        ))

    # 4. Conflicting declarations across panels
    for c in conflicts:
        p_list = [ref.get("panel", "panel") for ref in c.evidence_refs]
        p_str = ", ".join(set(p_list))
        requests.append(CaptureRequest(
            request_id=f"CAP_CONFLICT_{c.field.upper()}",
            target_panel=p_str or "conflicting_panels",
            reason=f"Conflicting declarations for {c.field} observed across panels: {c.reason}",
            expected_declaration_or_evidence=f"authoritative {c.field} declaration",
            priority="high",
            suggested_action=f"Inspect the package physically to determine which panel contains the genuine {c.field} and recapture that surface.",
        ))

    return requests


# ---------------------------------------------------------------------------
# 12. Violation and Review Dossier Builders (Sections 15 & 16)
# ---------------------------------------------------------------------------

def generate_violation_dossier(
    inspection_id: str,
    failing_findings: list[FindingResult],
) -> ViolationDossier:
    """Generate an audit-ready violation dossier for legal enforcement (Section 15)."""
    violations: list[ViolationItem] = []
    affected_limbs_set: set[str] = set()

    for f in failing_findings:
        limb = getattr(f, "limb", None)
        if limb:
            affected_limbs_set.add(limb)

        # Build clean evidence dict
        prov = getattr(f, "evidence_provenance", {}) or {}
        evidence_list = [prov]
        if "supporting_evidence" in prov and isinstance(prov["supporting_evidence"], list):
            evidence_list.extend(prov["supporting_evidence"])

        legal_prov = {
            "rule_id": f.rule_id,
            "rule_pack_version": f.rule_pack_version,
            "citation": f.citation,
            "ledger_ref": f.ledger_ref,
            "statutory_limb": limb,
        }

        # Concise deterministic inspector explanation (No LLM narrative)
        obs_text = f.observed or f.reason or "Statutory requirement not satisfied"
        req_text = f.required or "Mandatory statutory compliance under LMPC Rules"
        expl = f"Violation under {f.citation or f.check_id}: Observed {obs_text}. Required: {req_text}."

        violations.append(ViolationItem(
            rule_id=f.rule_id or f.check_id,
            rule_code=f.check_id,
            title=f.title,
            severity=f.severity,
            statutory_limb=limb,
            reason=f.reason or obs_text,
            observed=f.observed,
            required=f.required,
            remediation=f.remediation,
            evidence=evidence_list,
            legal_provenance=legal_prov,
            inspector_explanation=expl,
        ))

    affected_limbs = sorted(list(affected_limbs_set))
    limb_desc = " & ".join(f"Section {l}" for l in affected_limbs) if affected_limbs else "Section 36"
    summary = (
        f"{len(violations)} statutory violation(s) confirmed under {limb_desc} of the Legal Metrology Act, 2009. "
        f"Enforcement action and compoundable fine schedules under the Jan Vishwas Act are engaged."
    )

    dossier_id = f"DOSSIER_VIOLATION_{inspection_id}_{hashlib.sha256(summary.encode()).hexdigest()[:10]}"
    return ViolationDossier(
        inspection_id=str(inspection_id),
        dossier_id=dossier_id,
        generated_at=_now_iso(),
        violations=violations,
        total_violations=len(violations),
        affected_limbs=affected_limbs,
        statutory_summary=summary,
    )


def generate_review_dossier(
    inspection_id: str,
    conflicts: list[StatutoryConflict],
    not_assessed_findings: list[FindingResult],
    completeness: AssessmentCompleteness,
) -> ReviewDossier:
    """Generate an audit-ready review dossier identifying matters requiring officer attention (Section 16)."""
    items: list[ReviewItem] = []

    # 1. Unresolved conflicts
    for c in conflicts:
        items.append(ReviewItem(
            item_id=c.conflict_id,
            review_type="conflict",
            affected_rule=None,
            affected_panel=None,
            title=f"Statutory Conflict: {c.field.replace('_', ' ').title()}",
            reason=c.reason,
            evidence=c.evidence_refs,
            conflicting_records=c.conflicting_values,
            required_inspector_action=f"Physically inspect package surfaces to adjudicate genuine {c.field} declaration.",
            priority="high" if c.severity == "critical" else "medium",
        ))

    # 2. Missing panel coverage
    if not completeness.coverage_sufficient:
        items.append(ReviewItem(
            item_id=f"REV_COVERAGE_{inspection_id}",
            review_type="incomplete_coverage",
            affected_rule=None,
            affected_panel=", ".join(completeness.missing_panels),
            title="Incomplete Package Panel Coverage",
            reason=(
                f"Missing package panels ({', '.join(completeness.missing_panels)}) prevent comprehensive statutory assessment. "
                f"A positive compliance verdict cannot be issued on partial packaging evidence."
            ),
            evidence=[],
            conflicting_records=None,
            required_inspector_action=f"Capture missing panels ({', '.join(completeness.missing_panels)}) to complete inspection.",
            priority="high",
        ))

    # 3. Not assessed findings
    for f in not_assessed_findings:
        prov = getattr(f, "evidence_provenance", {}) or {}
        items.append(ReviewItem(
            item_id=f"REV_UNASSESSED_{f.check_id}",
            review_type="unassessed_rule",
            affected_rule=f.rule_id or f.check_id,
            affected_panel=prov.get("source_panel"),
            title=f.title,
            reason=f.reason or "Statutory rule could not be assessed from available evidence.",
            evidence=[prov] if prov else [],
            conflicting_records=None,
            required_inspector_action=f"Provide required evidence ({f.title}) or conduct manual field measurement.",
            priority="high" if f.severity in ("critical", "major") else "low",
        ))

    dossier_id = f"DOSSIER_REVIEW_{inspection_id}_{hashlib.sha256(str(len(items)).encode()).hexdigest()[:10]}"
    return ReviewDossier(
        inspection_id=str(inspection_id),
        dossier_id=dossier_id,
        generated_at=_now_iso(),
        review_items=items,
        total_review_items=len(items),
        unresolved_conflicts_count=len(conflicts),
        not_assessed_rules_count=len(not_assessed_findings),
    )


# ---------------------------------------------------------------------------
# 13. Master Aggregator: aggregate_inspection_assessment (Phase 4D)
# ---------------------------------------------------------------------------

def aggregate_inspection_assessment(
    inspection_id: str | int,
    findings: list[FindingResult],
    *,
    inspection_source: str = "physical_package",
    rules_as_at: date | None = None,
    rule_pack_version: str | None = None,
    captured_panels: set[str] | list[str] | None = None,
    llm_result: Any | None = None,
    panel_declarations: dict[str, Any] | list[Any] | None = None,
    images: list[Any] | None = None,
    ctx: CheckContext | None = None,
) -> InspectionAssessment:
    """Phase 4D Main Orchestrator: Multi-Panel Evidence Aggregation & Final Assessment.

    Combines:
    - Phase 4B declaration findings
    - Phase 4C visual/geometry findings
    - Cross-panel consistency & conflict detection
    - Finding deduplication
    - Precedence & assessment completeness
    - Violation & review dossiers
    - Actionable capture guidance
    """
    str_inspection_id = str(inspection_id)
    pack = load_rule_pack()
    resolved_rule_pack_ver = rule_pack_version or getattr(pack, "rule_pack_version", "2026.07.v1")

    # Resolve inspection date
    effective_as_at = (
        rules_as_at
        or getattr(ctx, "rules_as_at", None)
        or date.today()
    )
    as_at_iso = effective_as_at.isoformat()

    # Resolve captured panels set
    panels_set: set[str] = set()
    if captured_panels:
        panels_set = set(captured_panels)
    elif ctx and getattr(ctx, "panels_captured", None):
        panels_set = set(ctx.panels_captured)
    elif images:
        panels_set = {getattr(img, "panel", "front") for img in images}
    else:
        panels_set = {"front"}

    # 1. Cross-Panel Conflict Detection (Sections 5, 6, 7, 8)
    conflicts = detect_cross_panel_conflicts(
        inspection_id=str_inspection_id,
        llm_result=llm_result or getattr(ctx, "llm_result", None),
        panel_declarations=panel_declarations,
    )

    # 2. Finding Deduplication (Section 9)
    deduped_findings = deduplicate_findings(findings)

    # 3. Assessment Completeness (Section 11)
    coverage_override = getattr(ctx, "coverage_sufficient", None)
    if coverage_override is None and getattr(ctx, "ocr_failure_reason", None):
        coverage_override = False
    completeness = determine_assessment_completeness(
        findings=deduped_findings,
        captured_panels=panels_set,
        inspection_source=inspection_source,
        unresolved_conflicts_count=len(conflicts),
        coverage_sufficient_override=coverage_override,
    )

    # 4. Precedence & Overall Verdict (Section 10)
    # Check if halted out of scope
    is_halted_out_of_scope = (
        getattr(ctx, "halted", None) in ("CHK02", "CHK03")
        or (ctx and getattr(ctx, "transaction_type", None) in ("wholesale", "institutional", "industrial"))
    )

    failing = [f for f in deduped_findings if f.verdict == "fail"]
    not_assessed = [f for f in deduped_findings if f.verdict == "not_assessed"]
    passed = [f for f in deduped_findings if f.verdict == "pass"]

    if is_halted_out_of_scope:
        overall_verdict = "OUT_OF_SCOPE"
    elif failing:
        # Confirmed FAIL outranks everything
        overall_verdict = "VIOLATION"
    elif conflicts or not_assessed or completeness.status == "PARTIAL":
        # Unresolved conflict or incomplete coverage requires review
        overall_verdict = "REVIEW_REQUIRED"
    elif completeness.status == "COMPLETE" and passed and len(passed) == len(deduped_findings):
        # Complete clean pass
        overall_verdict = "COMPLIANT"
    else:
        overall_verdict = "REVIEW_REQUIRED"

    # 5. Build Evidence Summary (Section 13)
    pdp_est = False
    est_panel = None
    scale_src = None
    mm_px = None
    if ctx:
        pdp_est = getattr(ctx, "pdp_surface_established", False) or getattr(ctx, "pdp_detected", False)
        est_panel = getattr(ctx, "established_pdp_panel", None) or getattr(ctx, "pdp_panel_id", None)
        scale_src = getattr(ctx, "scale_source", None)
        mm_px = getattr(ctx, "mm_per_pixel", None)

    images_by_p: dict[str, list[dict[str, Any]]] = {}
    if images:
        for im in images:
            p = getattr(im, "panel", "unknown")
            images_by_p.setdefault(p, []).append({
                "image_id": getattr(im, "id", None),
                "file_path": getattr(im, "file_path", None),
                "sha256": getattr(im, "sha256", None),
            })

    evidence_summary = EvidenceSummary(
        total_panels_captured=len(panels_set),
        panels_captured=sorted(list(panels_set)),
        total_images=len(images) if images else len(panels_set),
        images_by_panel=images_by_p,
        pdp_established=pdp_est,
        established_pdp_panel=est_panel,
        scale_source=scale_src,
        mm_per_pixel=mm_px,
    )

    # 6. Build Violation Dossier (Section 15)
    violation_dossier = None
    if overall_verdict == "VIOLATION":
        violation_dossier = generate_violation_dossier(
            inspection_id=str_inspection_id,
            failing_findings=failing,
        )

    # 7. Build Review Dossier (Section 16)
    review_dossier = None
    review_items: list[ReviewItem] = []
    if overall_verdict == "REVIEW_REQUIRED":
        review_dossier = generate_review_dossier(
            inspection_id=str_inspection_id,
            conflicts=conflicts,
            not_assessed_findings=not_assessed,
            completeness=completeness,
        )
        review_items = review_dossier.review_items

    # 8. Build Actionable Capture Requests (Section 17)
    capture_requests = generate_actionable_capture_requests(
        findings=deduped_findings,
        completeness=completeness,
        conflicts=conflicts,
        ctx=ctx,
    )

    return InspectionAssessment(
        inspection_id=str_inspection_id,
        overall_verdict=overall_verdict,
        assessment_completeness=completeness,
        rule_pack_version=resolved_rule_pack_ver,
        rules_as_at=as_at_iso,
        evaluated_at=_now_iso(),
        total_rules=len(deduped_findings),
        passed_rules=len(passed),
        failed_rules=len(failing),
        not_assessed_rules=len(not_assessed),
        not_applicable_rules=completeness.not_applicable_rules,
        conflicts=conflicts,
        findings=deduped_findings,
        review_items=review_items,
        evidence_summary=evidence_summary,
        violation_dossier=violation_dossier,
        review_dossier=review_dossier,
        actionable_capture_requests=capture_requests,
    )
