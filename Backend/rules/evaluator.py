"""Backend/rules/evaluator.py — Deterministic Declaration Compliance Evaluator (Phase 4B).

Core Invariant (Sections 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 20):
- OCR Reads -> LLM Structures -> Rules Decide.
- Operates directly on Phase 3 StructuredDeclarationResult without re-parsing raw OCR text with regex.
- Enforces strict PASS / FAIL / NOT_ASSESSED semantics.
- Distinguishes missing declarations with complete coverage (FAIL) from insufficient coverage (NOT_ASSESSED).
- Grounded in evidence: every finding preserves evidence provenance (panel, image_id, text, bbox, separate confidences).
- Traced to versioned legal rule pack (effective date awareness, source rule, statutory limb).
- Zero LLM calls, zero OCR calls, zero image reprocessing.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from citations import cite
from config import settings
from llm.schema import (
    STATUS_AMBIGUOUS,
    STATUS_CONFIRMED,
    STATUS_NOT_OBSERVED,
    STATUS_UNASSESSABLE,
    DateDeclaration,
    FieldProvenance,
    PartyDeclaration,
    StructuredDeclarationResult,
)
from rules.loader import load_rule_pack
from rules.schema import LegalRuleDefinition
from rules_engine import FindingResult

logger = logging.getLogger("niyamnetra.rules.evaluator")

# Permitted metric units under Rules 12 & 13 of LMPC Rules, 2011
PERMITTED_WEIGHT_UNITS = {"g", "kg", "mg", "gm", "gram", "grams", "kilogram", "kilograms"}
PERMITTED_VOLUME_UNITS = {"ml", "l", "kl", "litre", "litres", "liter", "liters", "millilitre", "millilitres"}
PERMITTED_LENGTH_UNITS = {"m", "cm", "mm", "metre", "metres", "meter", "meters"}
COUNT_UNITS = {"u", "n", "piece", "pieces", "number", "numbers", "count"}


def is_coverage_sufficient(ctx: Any, field_key: str) -> bool:
    """Determine whether the captured inspection evidence is sufficient to establish absence (Section 5).

    If only a single front panel was captured, declarations that legitimately appear
    on back/side/mrp panels (such as manufacturer address, consumer care, or mrp)
    cannot be legally certified as absent; result is NOT_ASSESSED.
    If >= 2 panels or specific relevant panels were captured, absence can be confirmed (FAIL).
    """
    explicit = getattr(ctx, "coverage_sufficient", None)
    if explicit is not None:
        return bool(explicit)

    if getattr(ctx, "ocr_failure_reason", None):
        return False

    failed_panels = set(getattr(ctx, "panels_failed_ocr", {}).keys())
    empty_panels = set(getattr(ctx, "panels_empty_ocr", set()))
    panels = getattr(ctx, "panels_captured", set()) or set()

    if panels and panels.issubset(failed_panels | empty_panels):
        return False

    # PDP-specific declarations (net quantity and commodity name are required on PDP under Rule 6(1) & Rule 7)
    if field_key in ("net_quantity", "commodity_name"):
        pdp_panels = {"front", "principal"} & panels
        if not pdp_panels:
            return False
        if failed_panels and pdp_panels.issubset(failed_panels):
            return False
        if empty_panels and pdp_panels.issubset(empty_panels):
            return False
        return True

    # Dedicated panels
    if field_key == "mrp" and "mrp" in panels:
        if "mrp" not in failed_panels and "mrp" not in empty_panels:
            return True
    if field_key in ("date_of_manufacture", "best_before") and "batch" in panels:
        if "batch" not in failed_panels and "batch" not in empty_panels:
            return True

    # Listing context always has complete listing metadata
    if getattr(ctx, "listing_available", False):
        return True

    usable_panels = panels - failed_panels - empty_panels
    if len(usable_panels) >= 2:
        return True

    return False


def has_valid_provenance(prov: FieldProvenance | None) -> bool:
    """Verify that an extracted declaration is grounded in real OCR evidence (Section 16)."""
    if prov is None:
        return False
    if not prov.source_text and not prov.source_panel and not prov.source_image_id:
        return False
    return True


def format_evidence_provenance(prov: FieldProvenance | None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    """Extract verifiable provenance payload without fabrication (Section 15 & 16)."""
    out: dict[str, Any] = {}
    if prov:
        st = getattr(prov, "source_type", None) or ("ocr" if prov.source_text else "unknown")
        out = {
            "source_type": st,
            "source_panel": prov.source_panel,
            "source_image_id": prov.source_image_id,
            "source_text": prov.source_text,
            "source_bbox": prov.source_bbox,
            "ocr_confidence": prov.ocr_confidence,
            "llm_confidence": prov.llm_confidence,
            "notes": prov.notes,
        }
    if extra:
        out.update(extra)
    return out


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# 1. Mandatory Declarations (CHK01 / RULE_LMPC_01)
# ---------------------------------------------------------------------------

def evaluate_mandatory_declarations(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of mandatory declarations under Rule 6(1) (CHK01)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK01")
    rule_id = rule.rule_id if rule else "RULE_LMPC_01_MANDATORY_DECLARATIONS"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK01",
        "All mandatory Rule 6 declarations present",
        "pass",
        "critical",
        citation=cite("R6", "Rule 6(1) and 6(2), mandatory declarations"),
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    # Effective date check (Section 20)
    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK01 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    if hasattr(ctx, "ocr_available") and ctx.ocr_available is False and not getattr(ctx, "ocr_full_text", None) and not any(
        getattr(getattr(llm_res, f, None), "status", None) == STATUS_CONFIRMED
        for f in ("commodity_name", "mrp", "net_quantity")
    ):
        t.verdict = "not_assessed"
        t.reason = "No OCR text recognised on package"
        return t

    missing_fields: list[str] = []
    insufficient_coverage_fields: list[str] = []
    ambiguous_fields: list[str] = []
    observed_prov: list[dict[str, Any]] = []

    # 1. Commodity Name
    comm = llm_res.commodity_name
    if comm.status == STATUS_CONFIRMED and comm.value:
        if not has_valid_provenance(comm.provenance):
            ambiguous_fields.append("Commodity name lacks evidence provenance")
        else:
            observed_prov.append(format_evidence_provenance(comm.provenance, {"field": "commodity_name"}))
    elif comm.status == STATUS_AMBIGUOUS:
        ambiguous_fields.append("Common/generic commodity name (ambiguous)")
    else:
        if is_coverage_sufficient(ctx, "commodity_name"):
            missing_fields.append("Common or generic name of the commodity")
        else:
            insufficient_coverage_fields.append("Common or generic commodity name")

    # 2. Manufacturer / Packer / Importer Identity & Address (Section 10)
    confirmed_parties = [p for p in llm_res.parties if p.status == STATUS_CONFIRMED and p.name]
    if confirmed_parties:
        party = confirmed_parties[0]
        if not has_valid_provenance(party.provenance):
            ambiguous_fields.append("Manufacturer details lack evidence provenance")
        else:
            observed_prov.append(format_evidence_provenance(party.provenance, {"field": "manufacturer"}))
            # Verify address presence
            if not party.address or not party.address.strip():
                if is_coverage_sufficient(ctx, "manufacturer"):
                    missing_fields.append("Complete address of manufacturer/packer/importer")
                else:
                    insufficient_coverage_fields.append("Complete address of manufacturer/packer/importer")
    else:
        ambiguous_parties = [p for p in llm_res.parties if p.status == STATUS_AMBIGUOUS]
        if ambiguous_parties:
            ambiguous_fields.append("Manufacturer/packer details (ambiguous)")
        else:
            if is_coverage_sufficient(ctx, "manufacturer"):
                missing_fields.append("Name and address of manufacturer, packer or importer")
            else:
                insufficient_coverage_fields.append("Name and address of manufacturer, packer or importer")

    # 3. Net Quantity
    nq = llm_res.net_quantity
    if nq.status == STATUS_CONFIRMED and nq.value is not None:
        if not has_valid_provenance(nq.provenance):
            ambiguous_fields.append("Net quantity lacks evidence provenance")
        else:
            observed_prov.append(format_evidence_provenance(nq.provenance, {"field": "net_quantity"}))
    elif nq.status == STATUS_AMBIGUOUS:
        ambiguous_fields.append("Net quantity (ambiguous)")
    else:
        if is_coverage_sufficient(ctx, "net_quantity"):
            missing_fields.append("Net quantity in standard units")
        else:
            insufficient_coverage_fields.append("Net quantity in standard units")

    # 4. Month and Year of Manufacture / Packing
    confirmed_dates = [d for d in llm_res.dates if d.status == STATUS_CONFIRMED and d.date_type_candidate in ("mfg", "packing", "manufacturing")]
    if confirmed_dates:
        d = confirmed_dates[0]
        if not has_valid_provenance(d.provenance):
            ambiguous_fields.append("Manufacture/packing date lacks evidence provenance")
        else:
            observed_prov.append(format_evidence_provenance(d.provenance, {"field": "date_of_manufacture"}))
    else:
        ambiguous_dates = [d for d in llm_res.dates if d.status == STATUS_AMBIGUOUS or d.date_type_candidate in ("unknown", None)]
        if ambiguous_dates:
            ambiguous_fields.append("Month and year of manufacture/packing (unclassified date)")
        else:
            if is_coverage_sufficient(ctx, "date_of_manufacture"):
                missing_fields.append("Month and year of manufacture or packing")
            else:
                insufficient_coverage_fields.append("Month and year of manufacture or packing")

    # 5. Retail Sale Price (MRP)
    mrp = llm_res.mrp
    if mrp.status == STATUS_CONFIRMED and mrp.value is not None:
        if not has_valid_provenance(mrp.provenance):
            ambiguous_fields.append("MRP lacks evidence provenance")
        else:
            observed_prov.append(format_evidence_provenance(mrp.provenance, {"field": "mrp"}))
    elif mrp.status == STATUS_AMBIGUOUS:
        ambiguous_fields.append("Retail sale price (MRP ambiguous)")
    else:
        if is_coverage_sufficient(ctx, "mrp"):
            missing_fields.append("Retail sale price as maximum retail price")
        else:
            insufficient_coverage_fields.append("Retail sale price (MRP)")

    # 6. Consumer Care Contact Details
    cc = llm_res.consumer_care
    if cc.status == STATUS_CONFIRMED and (cc.phone or cc.email or cc.address or cc.website):
        if not has_valid_provenance(cc.provenance):
            ambiguous_fields.append("Consumer care lacks evidence provenance")
        else:
            observed_prov.append(format_evidence_provenance(cc.provenance, {"field": "consumer_care"}))
    elif cc.status == STATUS_AMBIGUOUS:
        ambiguous_fields.append("Consumer care contact (ambiguous)")
    else:
        if is_coverage_sufficient(ctx, "consumer_care"):
            missing_fields.append("Consumer care contact — name, address, telephone or email")
        else:
            insufficient_coverage_fields.append("Consumer care contact")

    # 7. Dimensions (conditional on sale by number/length under Rule 6(1)(m))
    net_unit = (getattr(ctx, "net_quantity_unit", None) or "").lower()
    if net_unit in {"pcs", "pc", "m", "cm", "mm"}:
        dim = llm_res.dimensions
        if dim.status == STATUS_CONFIRMED and dim.value:
            if not has_valid_provenance(dim.provenance):
                ambiguous_fields.append("Dimensions lack evidence provenance")
            else:
                observed_prov.append(format_evidence_provenance(dim.provenance, {"field": "dimensions"}))
        elif dim.status == STATUS_AMBIGUOUS:
            ambiguous_fields.append("Dimensions (ambiguous)")
        else:
            if is_coverage_sufficient(ctx, "dimensions"):
                missing_fields.append("Dimensions of commodity")
            else:
                insufficient_coverage_fields.append("Dimensions of commodity")

    # Evaluation outcome (Section 4 & 5)
    if missing_fields:
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = f"{len(missing_fields)} mandatory declaration(s) absent in inspected evidence: " + "; ".join(missing_fields) + "."
        t.required = "Every mandatory declaration in Rule 6(1) must visibly appear on the package."
        t.remediation = rule.remediation if rule else "Affix all mandatory Rule 6(1) declarations visibly on the package."
        t.evidence_provenance = {"observed": observed_prov}
        return t

    if insufficient_coverage_fields or ambiguous_fields:
        t.verdict = "not_assessed"
        reasons = []
        if insufficient_coverage_fields:
            panels_str = ", ".join(getattr(ctx, "panels_captured", [])) or "single panel"
            reasons.append(
                f"Declarations ({'; '.join(insufficient_coverage_fields)}) were not observed, but available "
                f"inspection coverage ({panels_str}) is insufficient to establish absence."
            )
        if ambiguous_fields:
            reasons.append(f"Uncertain evidence for: {'; '.join(ambiguous_fields)}.")
        t.reason = " ".join(reasons)
        t.remediation = "Recapture additional package panels to establish complete declaration coverage."
        t.evidence_provenance = {"observed": observed_prov}
        return t

    t.verdict = "pass"
    t.observed = "All applicable Rule 6(1) mandatory declarations visibly verified on package."
    t.evidence_provenance = {"observed": observed_prov}
    return t


# ---------------------------------------------------------------------------
# 2. MRP Expression & Tax Inclusion (CHK04 / RULE_LMPC_04)
# ---------------------------------------------------------------------------

def evaluate_mrp_expression(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of MRP expression under Rule 6(1)(e) & Rule 2(m) (CHK04)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK04")
    rule_id = rule.rule_id if rule else "RULE_LMPC_04_MRP_FORM"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK04",
        "Retail sale price correctly expressed",
        "pass",
        "major",
        citation=cite("R6-1-e", "Rule 6(1)(e) read with Rule 2(m)"),
        ledger_ref="L-02",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK04 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    mrp = llm_res.mrp
    t.evidence_provenance = format_evidence_provenance(mrp.provenance)

    if mrp.status == STATUS_AMBIGUOUS:
        cands_str = ", ".join(f"{c.raw_text}" for c in mrp.candidates if c.raw_text) or mrp.raw_text or "conflicting text"
        t.verdict = "not_assessed"
        t.reason = f"MRP expression is ambiguous in OCR evidence ({cands_str}); compliant price expression cannot be certified."
        t.remediation = "Ensure the MRP is clearly legible, unsmudged, and unambiguously printed."
        return t

    if mrp.status in (STATUS_NOT_OBSERVED, STATUS_UNASSESSABLE) or mrp.value is None:
        failed_panels = getattr(ctx, "panels_failed_ocr", {})
        non_front = (getattr(ctx, "panels_captured", set()) or set()) - {"front", "principal"}
        if failed_panels and ("mrp" in failed_panels or (non_front and non_front.issubset(set(failed_panels.keys())))):
            err_p = "mrp" if "mrp" in failed_panels else next(iter(failed_panels.keys()))
            t.verdict = "not_assessed"
            t.reason = f"Retail sale price panel could not be read due to OCR failure on panel '{err_p}' ({failed_panels[err_p]}); cannot certify absence."
            t.remediation = "Re-capture the panel displaying MRP."
            return t

        empty_panels = getattr(ctx, "panels_empty_ocr", set())
        if empty_panels and ("mrp" in empty_panels or (non_front and non_front.issubset(empty_panels))):
            t.verdict = "not_assessed"
            t.reason = "No readable text detected on panel(s) where retail sale price is declared; cannot certify absence."
            t.remediation = "Re-capture the panel displaying MRP with clear, legible text."
            return t
        if is_coverage_sufficient(ctx, "mrp"):
            t.verdict = "fail"
            t.limb = "36(1)"
            t.observed = "No retail sale price declaration observed on inspected package."
            t.required = "Retail sale price must be clearly declared as Maximum Retail Price (MRP) in Rupees."
            t.remediation = rule.remediation if rule else "Affix clear MRP declaration inclusive of all taxes."
        else:
            t.verdict = "not_assessed"
            t.reason = "No retail sale price was observed in available evidence; full package panel coverage not available."
            t.remediation = "Capture the panel displaying MRP (commonly back, top, or bottom panel)."
        return t

    # Provenance grounding check (Section 16)
    if not has_valid_provenance(mrp.provenance):
        t.verdict = "not_assessed"
        t.reason = "MRP was extracted as confirmed, but lacks verifiable evidence provenance (source text/image/panel); cannot certify compliance."
        t.remediation = "Ensure the scan preserves image panel and text bounding box provenance."
        return t

    # Evaluate confirmed MRP
    problems = []

    # Currency check
    if mrp.currency and mrp.currency not in ("INR", "Rs", "₹", "Rs."):
        problems.append(f"declared in foreign/non-standard currency '{mrp.currency}', Indian Rupees required")

    # Tax inclusive wording check (Rule 6(1)(e))
    raw_lower = (mrp.raw_text or "").lower()
    has_inclusive_wording = mrp.inclusive_of_taxes is True or any(
        phrase in raw_lower for phrase in ["incl", "inclusive", "all taxes", "कर सहित"]
    )
    if not has_inclusive_wording:
        problems.append("price is not qualified as 'inclusive of all taxes' or an equivalent statutory expression")

    # Rounding check under Rule 2(m)
    val = mrp.value
    cents = round((val - int(val)) * 100)
    if cents not in (0, 50):
        problems.append(f"declared as ₹{val:.2f}, with fractional paise ({cents:02d}) not rounded to nearest whole rupee or 50 paise under Rule 2(m)")

    # Conflicting candidates check (Rule 6(2A) - dual pricing)
    if len(mrp.candidates) > 1:
        vals = {c.value for c in mrp.candidates if c.value is not None}
        if len(vals) > 1:
            t.verdict = "fail"
            t.limb = "36(1)"
            t.observed = f"Conflicting multiple MRP values detected on package: {', '.join(f'₹{v}' for v in sorted(vals))}."
            t.required = "A package must not bear more than one retail sale price under Rule 6(2A)."
            t.remediation = "Do not display multiple conflicting retail sale prices on the same package."
            return t

    if problems:
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = f"Retail sale price read as '{mrp.raw_text or val}': " + "; ".join(problems) + "."
        t.required = "MRP must be declared in Rupees qualified as 'inclusive of all taxes' and rounded in compliance with Rule 2(m)."
        t.remediation = rule.remediation if rule else "Declare MRP in Indian Rupees qualified as 'incl. of all taxes' and rounded to nearest rupee or 50 paise."
        return t

    t.verdict = "pass"
    t.observed = f"Retail sale price declared as ₹{val:.2f} inclusive of all taxes in evidence '{mrp.raw_text}'."
    return t


# ---------------------------------------------------------------------------
# 3. Net Quantity Expression & Permitted Units (CHK05 / RULE_LMPC_05)
# ---------------------------------------------------------------------------

def evaluate_net_quantity_expression(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of Net Quantity units and forbidden qualifiers (CHK05)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK05")
    rule_id = rule.rule_id if rule else "RULE_LMPC_05_NET_QUANTITY_UNITS"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK05",
        "Net quantity free of qualifiers and in permitted units",
        "pass",
        "major",
        citation=cite("R12-13", "the prohibited-qualifier provision in Rules 12-13"),
        ledger_ref="L-01",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK05 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    nq = llm_res.net_quantity
    t.evidence_provenance = format_evidence_provenance(nq.provenance)

    if nq.status == STATUS_AMBIGUOUS:
        t.verdict = "not_assessed"
        t.reason = f"Net quantity declaration is ambiguous in OCR evidence ('{nq.raw_text}'); permitted expression cannot be confirmed."
        t.remediation = "Ensure the declared quantity is clear, unobstructed, and unambiguous."
        return t

    if nq.status in (STATUS_NOT_OBSERVED, STATUS_UNASSESSABLE) or nq.value is None:
        failed_panels = getattr(ctx, "panels_failed_ocr", {})
        pdp_panels = ({"front", "principal"} & (getattr(ctx, "panels_captured", set()) or set())) or set()
        if failed_panels and pdp_panels and pdp_panels.issubset(set(failed_panels.keys())):
            err_p = failed_panels.get("front") or failed_panels.get("principal") or next(iter(failed_panels.values()))
            t.verdict = "not_assessed"
            t.reason = f"Principal display panel could not be read due to OCR failure ({err_p}); net quantity cannot be certified as absent."
            t.remediation = "Re-capture the principal display panel."
            return t

        empty_panels = getattr(ctx, "panels_empty_ocr", set())
        if empty_panels and pdp_panels and pdp_panels.issubset(empty_panels):
            t.verdict = "not_assessed"
            t.reason = "No readable text detected on principal display panel; net quantity cannot be certified as absent."
            t.remediation = "Re-capture the principal display panel with clear, legible text."
            return t
        if is_coverage_sufficient(ctx, "net_quantity"):
            t.verdict = "fail"
            t.limb = "36(2)"
            t.observed = "No net quantity declaration observed on package principal display panel."
            t.required = "Net quantity must be declared in standard prescribed metric units on the principal display panel."
            t.remediation = rule.remediation if rule else "Affix standard net quantity declaration on the principal display panel."
        else:
            t.verdict = "not_assessed"
            t.reason = "Net quantity was not read; package evidence coverage is incomplete."
            t.remediation = "Capture the principal display panel to assess net quantity."
        return t

    # Provenance grounding check (Section 16)
    if not has_valid_provenance(nq.provenance):
        t.verdict = "not_assessed"
        t.reason = "Net quantity was extracted as confirmed, but lacks verifiable evidence provenance; cannot certify compliance."
        t.remediation = "Ensure the scan preserves image panel and text bounding box provenance."
        return t

    # Check prohibited qualifier words from catalog (Rules 12 & 13)
    from rules_engine import forbidden_qualifiers
    raw_lower = (nq.raw_text or "").lower()
    hits = [w for w in forbidden_qualifiers() if re.search(rf"\b{re.escape(w)}\b", raw_lower)]

    # Check unit
    unit = (nq.unit or getattr(ctx, "net_quantity_unit", None) or "").strip().lower()
    unit_problem = None

    if unit in COUNT_UNITS:
        t.verdict = "not_assessed"
        t.reason = (
            f"Quantity declared in count unit '{unit}'. Whether declaration by number "
            f"is prescribed for this commodity depends on the Second/Fourth Schedule."
        )
        t.ledger_ref = "L-06"
        t.remediation = "Verify scheduled commodity specifications for declaration by count/number."
        return t

    all_metric = PERMITTED_WEIGHT_UNITS | PERMITTED_VOLUME_UNITS | PERMITTED_LENGTH_UNITS
    if not unit or unit not in all_metric:
        unit_problem = f"'{unit or 'unspecified'}' is not a permitted metric unit under Legal Metrology Rules"

    if nq.value <= 0:
        unit_problem = f"declared quantity value {nq.value} is non-positive"

    if hits or unit_problem:
        t.verdict = "fail"
        t.limb = "36(2)"
        parts = []
        if hits:
            parts.append(f"prohibited qualifier word(s): {', '.join(repr(h) for h in hits)}")
        if unit_problem:
            parts.append(unit_problem)
        t.observed = f"Net quantity read as '{nq.raw_text}': " + "; ".join(parts) + "."
        t.required = "Net quantity must be declared in standard metric units without any qualifying words (Rule 12 & 13)."
        t.remediation = rule.remediation if rule else "Declare net quantity using standard metric units (g, kg, ml, l) without misleading qualifiers."
        return t

    t.verdict = "pass"
    t.observed = f"Net quantity declared as {nq.value} {nq.unit or ''} in compliant metric form without prohibited qualifiers."
    return t


# ---------------------------------------------------------------------------
# 4. Country of Origin on Imported Goods (CHK12 / RULE_LMPC_12)
# ---------------------------------------------------------------------------

def evaluate_country_of_origin_declaration(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of Country of Origin under Rule 6(1)(aa) (CHK12)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK12")
    rule_id = rule.rule_id if rule else "RULE_LMPC_12_COUNTRY_OF_ORIGIN_IMPORT"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK12",
        "Country of origin declared on imported goods",
        "pass",
        "major",
        citation=cite("R6-1-aa", "Rule 6(1)(aa), country of origin"),
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK12 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    # Applicability check (Section 12)
    is_imported = getattr(ctx, "is_imported", None)
    if is_imported is None:
        t.verdict = "not_assessed"
        t.reason = (
            "Whether the goods are imported was not recorded. Rule 6(1)(aa) requires "
            "country of origin on imported packages, so statutory applicability cannot be determined."
        )
        t.remediation = "Record whether the package is an imported product during inspection."
        return t

    if not is_imported:
        t.verdict = "pass"
        t.observed = "Recorded as domestically manufactured; Rule 6(1)(aa) import country-of-origin requirement does not apply."
        return t

    coo = llm_res.country_of_origin
    t.evidence_provenance = format_evidence_provenance(coo.provenance)

    if coo.status == STATUS_AMBIGUOUS:
        t.verdict = "not_assessed"
        t.reason = f"Country of origin declaration is ambiguous in OCR evidence ('{coo.raw_text}')."
        t.remediation = "Ensure the Country of Origin marking is clear and unobstructed."
        return t

    if coo.status in (STATUS_NOT_OBSERVED, STATUS_UNASSESSABLE) or not coo.value:
        if is_coverage_sufficient(ctx, "country_of_origin"):
            t.verdict = "fail"
            t.limb = "36(1)"
            t.observed = "No country of origin declared on an imported pre-packaged commodity."
            t.required = "Imported pre-packaged commodities must visibly declare the Country of Origin (Rule 6(1)(aa))."
            t.remediation = rule.remediation if rule else "Affix prominent Country of Origin declaration on imported package."
        else:
            t.verdict = "not_assessed"
            t.reason = "Country of origin was not observed; inspection panel coverage is incomplete."
            t.remediation = "Capture all package panels to verify presence of Country of Origin."
        return t

    # Provenance grounding check (Section 16)
    if not has_valid_provenance(coo.provenance):
        t.verdict = "not_assessed"
        t.reason = "Country of origin was extracted as confirmed, but lacks verifiable evidence provenance; cannot certify compliance."
        t.remediation = "Ensure the scan preserves image panel and text bounding box provenance."
        return t

    t.verdict = "pass"
    t.observed = f"Country of origin declared as '{coo.value}' on imported package (evidence: '{coo.raw_text}')."
    return t


# ---------------------------------------------------------------------------
# 5. Best-Before / Expiry for Perishables (CHK13 / RULE_LMPC_13)
# ---------------------------------------------------------------------------

def evaluate_perishable_expiry_declaration(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of Best-Before / Expiry date under Rule 6(1)(da) (CHK13)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK13")
    rule_id = rule.rule_id if rule else "RULE_LMPC_13_EXPIRY_PERISHABLE"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK13",
        "Best-before or use-by declared where the commodity is perishable",
        "pass",
        "major",
        citation=cite("R6-1-da", "Rule 6(1)(da), best-before declaration"),
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK13 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    is_perishable = getattr(ctx, "is_perishable", None)
    if is_perishable is None:
        t.verdict = "not_assessed"
        t.reason = "Whether the commodity is perishable was not recorded; Rule 6(1)(da) applicability cannot be settled."
        t.remediation = "Record perishable commodity status during inspection."
        return t

    if not is_perishable:
        t.verdict = "pass"
        t.observed = "Recorded as non-perishable; no best-before declaration required under Rule 6(1)(da)."
        return t

    # Search for confirmed expiry or best before dates
    expiry_dates = [
        d for d in llm_res.dates
        if d.status == STATUS_CONFIRMED and d.date_type_candidate in ("expiry", "best_before", "use_by")
    ]
    if expiry_dates:
        d = expiry_dates[0]
        t.evidence_provenance = format_evidence_provenance(d.provenance)
        if not has_valid_provenance(d.provenance):
            t.verdict = "not_assessed"
            t.reason = "Best-before date was confirmed by extraction, but lacks evidence provenance; traceability cannot be established."
            return t
        t.verdict = "pass"
        t.observed = f"Best-before / use-by date declared as '{d.normalized_date or d.raw_text}' on perishable commodity."
        return t

    ambiguous_dates = [
        d for d in llm_res.dates
        if d.status == STATUS_AMBIGUOUS or d.date_type_candidate in ("unknown", None)
    ]
    if ambiguous_dates:
        t.verdict = "not_assessed"
        t.reason = f"Date declaration observed ('{ambiguous_dates[0].raw_text}'), but cannot be confirmed as best-before/use-by."
        t.remediation = "Ensure best-before or expiry date wording ('Best Before', 'Use By') is clearly prefixed."
        return t

    if is_coverage_sufficient(ctx, "best_before"):
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = "No best-before or use-by date declared on a perishable commodity."
        t.required = "Perishable commodities must declare best-before or use-by date under Rule 6(1)(da)."
        t.remediation = rule.remediation if rule else "Affix Best Before or Expiry date declaration on perishable package."
    else:
        t.verdict = "not_assessed"
        t.reason = "Best-before date was not observed; package panel coverage is incomplete."
        t.remediation = "Capture all package panels to check for best-before date."
    return t


# ---------------------------------------------------------------------------
# 6. E-Commerce Listing Declarations (CHK15 / RULE_LMPC_15)
# ---------------------------------------------------------------------------

def evaluate_ecommerce_listing_declarations(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of E-Commerce listing declarations under Rule 6(10) (CHK15)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK15")
    rule_id = rule.rule_id if rule else "RULE_LMPC_15_ECOMMERCE_LISTING_DECLARATIONS"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK15",
        "E-commerce listing shows mandatory declarations",
        "pass",
        "major",
        citation=cite("R6-10", "Rule 6(10), e-commerce declarations"),
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK15 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if not getattr(ctx, "listing_available", False):
        t.verdict = "not_assessed"
        t.reason = (
            "This was a physical package scan, not an e-commerce listing scan. "
            "Rule 6(10) governs the digital marketplace display and cannot be assessed from a package photo."
        )
        return t

    listing_fields = getattr(ctx, "listing_fields", {}) or {}
    required = ["commodity", "manufacturer", "net_quantity", "mrp", "consumer_care"]
    missing = [k for k in required if not listing_fields.get(k)]

    if missing:
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = "Online marketplace listing omits mandatory declaration(s): " + ", ".join(missing) + "."
        t.required = "An e-commerce listing must show all Rule 6(1) declarations prior to purchase under Rule 6(10)."
        t.remediation = rule.remediation if rule else "Update e-commerce listing to display all mandatory declarations before consumer purchase."
        return t

    t.verdict = "pass"
    t.observed = "E-commerce listing displays all statutory declarations required by Rule 6(10)."
    return t


# ---------------------------------------------------------------------------
# 7. Unit Sale Price (CHK19 / RULE_LMPC_19)
# ---------------------------------------------------------------------------

def evaluate_unit_sale_price(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of Unit Sale Price under Rule 6(1)(g) (CHK19)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK19")
    rule_id = rule.rule_id if rule else "RULE_LMPC_19_UNIT_SALE_PRICE"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK19",
        "Unit Sale Price declaration",
        "pass",
        "major",
        citation=cite("R6-1-g", "Rule 6(1)(g), Unit Sale Price declaration"),
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK19 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    # Applicability check: USP is mandatory for packages containing > 1 unit, > 1 kg, or > 1 litre
    nq_val = getattr(ctx, "net_quantity_value", None)
    nq_unit = (getattr(ctx, "net_quantity_unit", None) or "").lower()
    if nq_val is not None and nq_val <= 1.0 and nq_unit in ("kg", "l", "litre", "liter", "u", "n", "pc", "pcs"):
        t.verdict = "pass"
        t.observed = f"Net quantity is {nq_val} {nq_unit} (<= 1 unit/kg/litre); Unit Sale Price declaration is not required under Rule 6(1)(g)."
        return t

    usp = llm_res.unit_sale_price
    t.evidence_provenance = format_evidence_provenance(usp.provenance)

    if usp.status == STATUS_CONFIRMED and usp.value:
        if not has_valid_provenance(usp.provenance):
            t.verdict = "not_assessed"
            t.reason = "Unit Sale Price was confirmed by extraction, but lacks evidence provenance; traceability cannot be established."
            return t
        t.verdict = "pass"
        t.observed = f"Unit Sale Price declared as '{usp.value}' (evidence: '{usp.raw_text}')."
        return t

    if usp.status == STATUS_AMBIGUOUS:
        t.verdict = "not_assessed"
        t.reason = f"Unit Sale Price expression is ambiguous in OCR evidence ('{usp.raw_text}')."
        t.remediation = "Ensure Unit Sale Price (e.g. ₹ per g / per ml) is clearly legible."
        return t

    if is_coverage_sufficient(ctx, "unit_sale_price"):
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = "No Unit Sale Price declaration observed on package with quantity exceeding 1 unit/kg/litre."
        t.required = "Packages containing more than 1 kg, 1 litre, or 1 unit must declare Unit Sale Price under Rule 6(1)(g)."
        t.remediation = rule.remediation if rule else "Declare Unit Sale Price (per gram/kg or per ml/litre) alongside MRP."
    else:
        t.verdict = "not_assessed"
        t.reason = "Unit Sale Price was not observed; inspection panel coverage is incomplete."
        t.remediation = "Capture all package panels to check for Unit Sale Price declaration."
    return t


# ---------------------------------------------------------------------------
# 8. Dimensions (CHK20 / RULE_LMPC_20)
# ---------------------------------------------------------------------------

def evaluate_dimensions(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> FindingResult:
    """Deterministic evaluation of Dimensions under Rule 6(1)(m) (CHK20)."""
    pack = load_rule_pack()
    rule = pack.get_rule_by_id("CHK20")
    rule_id = rule.rule_id if rule else "RULE_LMPC_20_DIMENSIONS"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK20",
        "Dimensions declaration where size is relevant",
        "pass",
        "major",
        citation=cite("R6-1-m", "Rule 6(1)(m), dimensions declaration"),
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    as_at = getattr(ctx, "rules_as_at", None)
    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK20 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from})."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    net_unit = (getattr(ctx, "net_quantity_unit", None) or "").lower()
    if net_unit not in {"pcs", "pc", "m", "cm", "mm"}:
        t.verdict = "pass"
        t.observed = f"Commodity unit is '{net_unit or 'mass/volume'}', not sold by length or number; Rule 6(1)(m) dimensions do not apply."
        return t

    dim = llm_res.dimensions
    t.evidence_provenance = format_evidence_provenance(dim.provenance)

    if dim.status == STATUS_CONFIRMED and dim.value:
        if not has_valid_provenance(dim.provenance):
            t.verdict = "not_assessed"
            t.reason = "Dimensions were confirmed by extraction, but lack evidence provenance; traceability cannot be established."
            return t
        t.verdict = "pass"
        t.observed = f"Dimensions declared as '{dim.value}' on commodity sold by count/length (evidence: '{dim.raw_text}')."
        return t

    if dim.status == STATUS_AMBIGUOUS:
        t.verdict = "not_assessed"
        t.reason = f"Dimensions declaration is ambiguous in OCR evidence ('{dim.raw_text}')."
        t.remediation = "Ensure dimensions (e.g. length x width in cm or mm) are clearly printed."
        return t

    if is_coverage_sufficient(ctx, "dimensions"):
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = "No dimensions declared on commodity sold by length or count."
        t.required = "Commodities sold by length or number must declare dimensions under Rule 6(1)(m)."
        t.remediation = rule.remediation if rule else "Declare size/dimensions in standard units (cm, mm, m)."
    else:
        t.verdict = "not_assessed"
        t.reason = "Dimensions declaration was not observed; inspection panel coverage is incomplete."
        t.remediation = "Capture all package panels to check for dimensions declaration."
    return t


# ---------------------------------------------------------------------------
# Unified Evaluation Pipeline
# ---------------------------------------------------------------------------

def evaluate_declaration_rules(
    llm_res: StructuredDeclarationResult,
    ctx: Any,
) -> list[FindingResult]:
    """Execute all deterministic declaration rule evaluations directly from StructuredDeclarationResult.

    Consumes Phase 3 output without re-parsing raw OCR text with regex.
    """
    findings: list[FindingResult] = [
        evaluate_mandatory_declarations(llm_res, ctx),
        evaluate_mrp_expression(llm_res, ctx),
        evaluate_net_quantity_expression(llm_res, ctx),
        evaluate_country_of_origin_declaration(llm_res, ctx),
        evaluate_perishable_expiry_declaration(llm_res, ctx),
        evaluate_ecommerce_listing_declarations(llm_res, ctx),
        evaluate_unit_sale_price(llm_res, ctx),
        evaluate_dimensions(llm_res, ctx),
    ]
    return findings
