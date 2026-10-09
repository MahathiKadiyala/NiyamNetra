# SPLIT NOTE 2026-09-06 (FIX 4.3) — migrate category logic to rules_engine_*.py
"""rules_engine.py"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

from cachetools import TTLCache, cached

from citations import cite
from config import settings

Verdict = Literal["pass", "fail", "not_assessed"]
Severity = Literal["critical", "major", "minor", "advisory"]
Source = Literal["OPERATOR", "PACKAGE", "LISTING", "PLATFORM"]


@dataclass(slots=True)
class FindingResult:
    check_id: str
    title: str
    verdict: Verdict
    severity: Severity
    reason: str | None = None          # mandatory when verdict == "not_assessed"
    observed: str | None = None
    required: str | None = None
    citation: str | None = None
    ledger_ref: str | None = None
    confidence: float | None = None
    limb: str | None = None            # "36(1)" | "36(2)" — set only on fail
    rule_id: str | None = None
    rule_pack_version: str | None = None
    remediation: str | None = None
    evidence_provenance: dict[str, Any] | None = None
    evaluation_timestamp: str | None = None
    # --- visual finding attributes (Phase 4C) ---
    measured_value: float | None = None
    measured_unit: str | None = None
    threshold_value: float | None = None
    measurement_uncertainty: float | None = None
    calibration_source: str | None = None
    engineering_signal: dict[str, Any] | None = None
    artifact_type: str | None = None
    target_bbox: tuple[int, int, int, int] | None = None
    image_id: str | None = None
    panel: str | None = None


@dataclass(slots=True)
class CheckContext:
    """Everything a check may look at. Nothing is fetched from inside a check."""
    # --- operator-declared ---
    transaction_type: str | None = None
    commodity_generic: str | None = None
    commodity_category: str | None = None
    net_quantity_value: float | None = None
    net_quantity_unit: str | None = None
    is_imported: bool | None = None
    is_perishable: bool | None = None
    is_medical_device: bool | None = None
    is_tobacco: bool | None = None
    has_sticker: bool | None = None
    sticker_reduces_price: bool | None = None
    sticker_covers_original: bool | None = None

    # --- geometry ---
    panel_shape: str | None = None
    panel_height_mm: float | None = None
    panel_width_mm: float | None = None
    panel_diameter_mm: float | None = None
    total_surface_area_cm2: float | None = None
    is_blown_moulded: bool = False

    # --- measurement ---
    mm_per_pixel: float | None = None
    mm_per_pixel_uncertainty: float | None = None
    scale_source: str = "none"

    # --- OCR ---
    ocr_available: bool = False
    ocr_failure_reason: str | None = None
    ocr_mean_confidence: float | None = None
    # Full concatenated OCR text (all panels), for scan.ocr_text persistence.
    # Not read by any check; staged by build_context, consumed by assess.
    ocr_full_text: str | None = None
    fields: dict = field(default_factory=dict)      # name -> ExtractedField
    measured_heights_mm: dict = field(default_factory=dict)
    measured_widths_mm: dict = field(default_factory=dict)
    clear_space_mm: dict = field(default_factory=dict)
    contrast_ratio: float | None = None
    llm_result: Any = None  # StructuredDeclarationResult (Phase 3)

    # --- image quality ---
    image_usable: bool = True
    image_quality_reason: str | None = None
    panels_captured: set[str] = field(default_factory=set)

    # --- listing, only when the scan came from a web listing ---
    listing_available: bool = False
    listing_fields: dict = field(default_factory=dict)
    platform_has_origin_filter: bool | None = None

    rules_as_at: date = field(default_factory=lambda: date.fromisoformat(settings.RULES_AS_AT))

    # --- Phase 4C visual, geometry & calibration extensions ---
    calibration_spec: Any = None
    rectified_image: Any = None
    analysis_image: Any = None
    declaration_bboxes: dict = field(default_factory=dict)
    clear_space_image_verified: bool | None = None
    verify_clear_space_image: bool = False
    engineering_contrast_signal: dict = field(default_factory=dict)
    use_visual_evaluator: bool = False
    declaration_outside_pdp: bool = False
    pdp_detected: bool = False
    pdp_panel_id: str | None = None
    established_pdp_panel: str | None = None
    pdp_surface_established: bool = False
    blown_moulded_inscribed_verified: bool | None = None
    surface_material: str | None = None
    declaration_medium: str | None = None
    coverage_sufficient: bool | None = None

    # --- Rule 6(4A)(d) / former Rule 6(8) origin marking extensions (GSR 826(E)) ---
    commodity_category: str | None = None
    product_origin: str | None = None          # "vegetarian" | "non_vegetarian" | "unknown"
    origin_symbol_detected: bool | None = None
    origin_symbol_panel: str | None = None
    origin_symbol_image_id: str | int | None = None
    origin_symbol_bbox: Any = None
    origin_symbol_colour: str | None = None     # "green" | "red" | "brown"
    origin_symbol_placement: str | None = None  # "top_pdp" | "bottom_pdp" | ...
    origin_symbol_engineering_signal: dict = field(default_factory=dict)

    # --- set by the runner, read by later checks ---
    halted: str | None = None          # the check_id that halted the run
    halt_reason: str | None = None
    phase3_halted: bool = False


@cached(TTLCache(maxsize=1, ttl=300))
def load_catalog() -> dict:
    """TTLCache, not lru_cache: an admin editing the catalog must take effect
    without a process restart, and must not take effect silently forever."""
    return json.loads(settings.RULES_CATALOG.read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def catalog_hash() -> str:
    import hashlib
    return hashlib.sha256(settings.RULES_CATALOG.read_bytes()).hexdigest()


# Rule 7, Table-I as substituted by GSR 629(E) w.e.f 01.01.2018.
# Ledger L-11 — UNVERIFIED. Consistent across drafts but not read from the
# gazette. The engine uses these numbers and says so in every report.
# (area_upper_cm2_inclusive, min_height_mm_normal, min_height_mm_blown)
TABLE_I = (
    (50.0,    1.0, 1.5),
    (100.0,   1.5, 3.0),
    (500.0,   2.5, 4.0),
    (2500.0,  4.0, 6.0),
    (math.inf, 6.0, 6.0),
)


def min_height_mm(pdp_area_cm2: float, blown: bool) -> float:
    for upper, normal, blown_h in TABLE_I:
        if pdp_area_cm2 <= upper:
            return blown_h if blown else normal
    raise AssertionError("TABLE_I must end with an infinite upper bound")


def pdp_area_cm2(ctx: CheckContext) -> tuple[float | None, str | None]:
    """Rule 7(4). Returns (area, reason_if_unavailable).

    rectangular : height x width
    cylindrical : 0.4 x height x pi x diameter
    other       : 40% of total surface area
    """
    if ctx.panel_shape == "rectangular" and ctx.panel_height_mm and ctx.panel_width_mm:
        return (ctx.panel_height_mm * ctx.panel_width_mm) / 100.0, None
    if ctx.panel_shape == "cylindrical" and ctx.panel_height_mm and ctx.panel_diameter_mm:
        area_mm2 = 0.4 * ctx.panel_height_mm * math.pi * ctx.panel_diameter_mm
        return area_mm2 / 100.0, None
    if ctx.panel_shape == "other" and ctx.total_surface_area_cm2:
        return 0.4 * ctx.total_surface_area_cm2, None
    return None, (
        "Principal display panel area cannot be computed: the package shape and "
        "its dimensions were not recorded, and Rule 7(4) requires them before "
        "any Table-I band can be selected."
    )


NO_SCALE = (
    "No millimetre scale could be established for this capture, so letter "
    "heights cannot be measured. Record the panel height, or include a "
    "reference object, and re-capture."
)


def measurement_blocked(ctx: CheckContext) -> str | None:
    """Returns a reason if millimetre measurement is impossible, else None."""
    if not ctx.image_usable:
        return ctx.image_quality_reason
    if not ctx.ocr_available:
        return ctx.ocr_failure_reason or "No text was recognised on the panel."
    if ctx.mm_per_pixel is None or ctx.scale_source == "none":
        return NO_SCALE
    return None


def compare_with_uncertainty(
    measured_mm: float, minimum_mm: float, uncertainty_mm: float | None
) -> tuple[Verdict, str]:
    """A measurement inside its own uncertainty band of the threshold is not a
    finding either way. 02 §7.4 requires not_assessed there, because a
    prosecution cannot rest on a difference smaller than the instrument's error.
    """
    u = uncertainty_mm or 0.0
    if measured_mm - u >= minimum_mm:
        return "pass", ""
    if measured_mm + u < minimum_mm:
        return "fail", ""
    return "not_assessed", (
        f"Measured {measured_mm:.2f} mm +/- {u:.2f} mm against a "
        f"{minimum_mm:.1f} mm minimum. The shortfall is inside the measurement "
        f"uncertainty, so compliance cannot be determined from this capture. "
        f"Physical verification with a calibrated gauge is required."
    )


CHECKS: list[tuple[str, Callable[[CheckContext], FindingResult]]] = []


def check(check_id: str):
    def deco(fn):
        CHECKS.append((check_id, fn))
        return fn
    return deco


OUT_OF_SCOPE_TRANSACTIONS = {
    "wholesale": "a wholesale dealing, not a retail sale",
    "institutional": "an institutional supply",
    "industrial": "an industrial supply",
    "packed_in_presence": "a package made up in the purchaser's presence",
    "export": "a package for export",
}

# Rule 3 thresholds. Chapter II does not apply above these.
RETAIL_WEIGHT_CEILING_KG = 25.0
RETAIL_VOLUME_CEILING_L = 25.0
AGRI_CEILING_KG = 50.0          # cement, fertiliser and agricultural produce
AGRI_CATEGORIES = {"cement", "fertiliser", "fertilizer", "agricultural_produce"}


@check("CHK03")
def chk03_chapter_ii_applicability(ctx: CheckContext) -> FindingResult:
    """Rule 3. Halts everything if Chapter II does not apply."""
    t = FindingResult(
        "CHK03", "Chapter II applies to this package", "pass", "critical",
        citation=cite("R3", "Rule 3, Legal Metrology (Packaged Commodities) Rules 2011"),
    )
    if ctx.transaction_type is None:
        t.verdict, t.reason = "not_assessed", (
            "The transaction type was not recorded, so it cannot be determined "
            "whether Chapter II applies. Rule 3 excludes wholesale, industrial "
            "and institutional dealings and packages made up in the purchaser's "
            "presence."
        )
        return t

    if ctx.transaction_type in OUT_OF_SCOPE_TRANSACTIONS:
        t.verdict = "pass"     # the check itself succeeded: it correctly found no scope
        t.observed = f"Recorded as {OUT_OF_SCOPE_TRANSACTIONS[ctx.transaction_type]}."
        t.required = "Chapter II applies only to packages intended for retail sale."
        ctx.halted, ctx.halt_reason = "CHK03", (
            f"Out of scope: this is {OUT_OF_SCOPE_TRANSACTIONS[ctx.transaction_type]}, "
            f"to which Chapter II of the Rules does not apply."
        )
        return t

    ceiling, unit = None, None
    # Unit recognition goes through _to_kg/_to_litres (covers g/gm/grams/gram,
    # l/ltr/litre/litres/liters/liter/ml) so spelling variants still hit the
    # Rule 3 ceilings instead of silently skipping them.
    _kg = _to_kg(ctx.net_quantity_value, ctx.net_quantity_unit or "") if ctx.net_quantity_value else None
    _l = _to_litres(ctx.net_quantity_value, ctx.net_quantity_unit or "") if ctx.net_quantity_value else None
    if _kg is not None:
        kg = _kg
        ceiling = (
            AGRI_CEILING_KG
            if (ctx.commodity_category or "").lower() in AGRI_CATEGORIES
            else RETAIL_WEIGHT_CEILING_KG
        )
        unit = "kg"
        if kg is not None and kg > ceiling:
            t.observed = f"Net quantity {kg:g} kg exceeds the {ceiling:g} kg ceiling."
            ctx.halted, ctx.halt_reason = "CHK03", (
                f"Out of scope: net quantity {kg:g} kg is above the {ceiling:g} kg "
                f"limit in Rule 3 for this commodity class."
            )
            return t
    elif _l is not None:
        litres = _l
        if litres is not None and litres > RETAIL_VOLUME_CEILING_L:
            t.observed = f"Net quantity {litres:g} L exceeds the 25 L ceiling."
            ctx.halted, ctx.halt_reason = "CHK03", (
                f"Out of scope: net quantity {litres:g} L is above the 25 L limit in Rule 3."
            )
            return t

    if ctx.net_quantity_value is not None and ctx.net_quantity_unit:
        t.observed = f"Retail sale ({ctx.net_quantity_value:g} {ctx.net_quantity_unit}), within the Rule 3 statutory quantity limits."
    else:
        t.observed = "Retail sale transaction confirmed; net quantity not declared or observed on package."
    return t


def _to_kg(value: float, unit: str) -> float | None:
    u = (unit or "").strip().lower()
    return {"kg": value, "g": value / 1000, "gm": value / 1000, "gram": value / 1000,
            "grams": value / 1000, "mg": value / 1_000_000}.get(u)


def _to_litres(value: float, unit: str) -> float | None:
    u = (unit or "").strip().lower()
    return {"l": value, "ltr": value, "litre": value, "litres": value,
            "liter": value, "liters": value, "ml": value / 1000}.get(u)


@check("CHK02")
def chk02_small_package_exemption(ctx: CheckContext) -> FindingResult:
    """Rule 26(a): packages of net quantity 10 g / 10 ml or less are exempt
    from the Rule 6 declarations — EXCEPT tobacco products.

    The 10-20 g proviso was withdrawn w.e.f 01.07.2012 (ledger L-15), so there
    is no intermediate band. Everything above 10 g / 10 ml is fully in.
    """
    t = FindingResult(
        "CHK02", "Small-package exemption under Rule 26(a)", "pass", "critical",
        citation=cite("R26a", "Rule 26(a) small-package exemption"),
        ledger_ref="L-15",
    )
    if ctx.halted:
        return _halted(t, ctx)

    if ctx.net_quantity_value is None or ctx.net_quantity_unit is None:
        t.verdict, t.reason = "not_assessed", (
            "Net quantity could not be read, so the Rule 26(a) exemption cannot "
            "be evaluated. The declarations are checked on the assumption that "
            "the package is not exempt; confirm the quantity physically."
        )
        return t

    grams = _to_kg(ctx.net_quantity_value, ctx.net_quantity_unit)
    millilitres = _to_litres(ctx.net_quantity_value, ctx.net_quantity_unit)
    small = (grams is not None and grams * 1000 <= 10) or (
        millilitres is not None and millilitres * 1000 <= 10
    )

    if not small:
        t.observed = (
            f"Net quantity {ctx.net_quantity_value:g} {ctx.net_quantity_unit} is "
            f"above the 10 g / 10 ml exemption threshold; Rule 6 applies in full."
        )
        return t

    # The carve-out. THIS BRANCH MUST EXIST — v1.x tested tobacco inline with
    # `if category not in [...]` and no else, so a tobacco package silently
    # produced no CHK02 row at all.
    if ctx.is_tobacco:
        t.observed = (
            f"Net quantity {ctx.net_quantity_value:g} {ctx.net_quantity_unit} is "
            f"within the small-package threshold, but the exemption in Rule 26(a) "
            f"does not extend to tobacco products, so all declarations remain due."
        )
        t.required = "Rule 6 declarations apply notwithstanding the package size."
        return t

    if ctx.is_tobacco is None:
        t.verdict, t.reason = "not_assessed", (
            f"Net quantity {ctx.net_quantity_value:g} {ctx.net_quantity_unit} is "
            f"within the small-package threshold, but whether this is a tobacco "
            f"product was not recorded, and Rule 26(a) does not exempt tobacco. "
            f"Confirm the commodity class."
        )
        return t

    t.observed = (
        f"Exempt: net quantity {ctx.net_quantity_value:g} {ctx.net_quantity_unit} "
        f"is within the 10 g / 10 ml threshold and this is not a tobacco product."
    )
    ctx.halted, ctx.halt_reason = "CHK02", (
        f"Exempt under Rule 26(a): net quantity "
        f"{ctx.net_quantity_value:g} {ctx.net_quantity_unit} is at or below "
        f"10 g / 10 ml, and the tobacco carve-out does not apply."
    )
    return t


@check("CHK14")
def chk14_medical_device_routing(ctx: CheckContext) -> FindingResult:
    """Proviso to Rule 2(h), as amended w.e.f 23.10.2025: a medical device is
    labelled under the Medical Devices Rules 2017, not under these Rules.

    Halts phase 3 only. The scope and identification findings still stand, and
    the report routes the package to the drug authority rather than closing it.
    """
    t = FindingResult(
        "CHK14", "Medical device — labelling routed to Medical Devices Rules 2017",
        "pass", "critical",
        citation=cite("R2h", "proviso to Rule 2(h), as amended w.e.f 23 October 2025"),
    )
    if ctx.halted:
        return _halted(t, ctx)

    if ctx.is_medical_device is None:
        t.verdict, t.reason = (
            "not_assessed",
            "Whether this is a medical device was not recorded. Since 23 October "
            "2025 medical devices are labelled under the Medical Devices Rules "
            "2017, so the applicable regime cannot be settled without it.",
        )
        return t

    if ctx.is_medical_device:
        ctx.phase3_halted = True
        t.observed = (
            "Recorded as a medical device. Declaration and typography "
            "requirements under these Rules do not apply; refer to the State "
            "Drug Controller under the Medical Devices Rules 2017."
        )
        t.required = "Labelling assessed under the Medical Devices Rules 2017."
        return t

    t.observed = "Not a medical device; these Rules apply."
    return t


# Rule 6(1) and 6(2). Each entry: field key, human label, provision key, limb.
RULE_6_DECLARATIONS = (
    ("manufacturer", "Name and address of the manufacturer, packer or importer",
     "R6-1-a", "36(1)"),
    ("commodity", "Common or generic name of the commodity", "R6-1-b", "36(1)"),
    ("net_quantity", "Net quantity in standard units", "R6-1-c", "36(2)"),
    ("date_of_manufacture", "Month and year of manufacture or packing",
     "R6-1-d", "36(1)"),
    ("mrp", "Retail sale price as maximum retail price", "R6-1-e", "36(1)"),
    ("consumer_care", "Consumer care contact — name, address, telephone or email",
     "R6-1-f", "36(1)"),
    ("dimensions", "Dimensions, where the commodity is sold by number or length",
     "R6-1-g", "36(1)"),
)


@check("CHK01")
def chk01_declarations_present(ctx: CheckContext) -> FindingResult:
    if getattr(ctx, "llm_result", None) is not None:
        try:
            from llm.schema import StructuredDeclarationResult
            if isinstance(ctx.llm_result, StructuredDeclarationResult):
                from rules.evaluator import evaluate_mandatory_declarations
                return evaluate_mandatory_declarations(ctx.llm_result, ctx)
        except Exception:
            pass
    t = FindingResult(
        "CHK01", "All mandatory Rule 6 declarations present", "pass", "critical",
        citation=cite("R6", "Rule 6(1) and 6(2), mandatory declarations"),
    )
    if ctx.halted:
        return _halted(t, ctx)
    if not ctx.ocr_available:
        t.verdict, t.reason = "not_assessed", (
            ctx.ocr_failure_reason
            or "No text was recognised on the package, so the presence of the "
               "mandatory declarations cannot be determined."
        )
        return t

    missing, unreadable = [], []
    for key, label, _prov, _limb in RULE_6_DECLARATIONS:
        f = ctx.fields.get(key)
        if key == "dimensions" and ctx.net_quantity_unit not in {"pcs", "pc", "m", "cm", "mm"}:
            continue        # 6(1)(g) is conditional on sale by number or length
        if f is None or not f.found:
            missing.append(label)
        elif f.confidence is not None and f.confidence < settings.OCR_MIN_CONFIDENCE:
            unreadable.append(f"{label} (read at {f.confidence:.0%} confidence)")

    if missing:
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = f"{len(missing)} declaration(s) absent: " + "; ".join(missing) + "."
        t.required = "Every declaration in Rule 6(1) must appear on the principal display panel."
        return t
    if unreadable:
        t.verdict, t.reason = "not_assessed", (
            "All declarations appear present, but "
            + "; ".join(unreadable)
            + " fell below the confidence floor, so their content cannot be "
              "certified from this capture."
        )
        return t
    t.observed = "All applicable Rule 6(1) declarations located on the panel."
    t.confidence = ctx.ocr_mean_confidence
    return t


@check("CHK04")
def chk04_mrp_form(ctx: CheckContext) -> FindingResult:
    """Rule 6(1)(e) with Rule 2(m); dual MRP prohibited by Rule 6(2A)."""
    if getattr(ctx, "llm_result", None) is not None:
        try:
            from llm.schema import StructuredDeclarationResult
            if isinstance(ctx.llm_result, StructuredDeclarationResult):
                from rules.evaluator import evaluate_mrp_expression
                return evaluate_mrp_expression(ctx.llm_result, ctx)
        except Exception:
            pass
    t = FindingResult(
        "CHK04", "Retail sale price correctly expressed", "pass", "major",
        citation=cite("R6-1-e", "Rule 6(1)(e) read with Rule 2(m)"),
        ledger_ref="L-02",
    )
    if ctx.halted:
        return _halted(t, ctx)
    mrp = ctx.fields.get("mrp")
    if not ctx.ocr_available or mrp is None or not mrp.found:
        t.verdict, t.reason = "not_assessed", (
            ctx.ocr_failure_reason
            or "No retail sale price was read, so its form cannot be checked. "
               "Absence of the price itself is reported under CHK01."
        )
        return t

    problems = []
    if not ctx.fields.get("mrp_inclusive_wording", _absent()).found:
        problems.append(
            'the price is not qualified as "inclusive of all taxes" or an '
            "equivalent expression"
        )

    rounding = _mrp_rounding_note(mrp.value)
    if rounding:
        problems.append(rounding)

    # Rule 6(2A) — no second, higher price on the same package.
    if _has_dual_price(ctx):
        problems.append(
            "two different retail sale prices appear on the package, which "
            "Rule 6(2A) prohibits"
        )

    if problems:
        t.verdict, t.limb = "fail", "36(1)"
        t.observed = f'Price read as "{mrp.value}"; ' + "; ".join(problems) + "."
        t.required = (
            "The retail sale price must be declared as the maximum retail price "
            "inclusive of all taxes, in a single amount."
        )
        return t
    t.observed = f'Price "{mrp.value}" declared inclusive of all taxes, single amount.'
    t.confidence = mrp.confidence
    return t


def _mrp_rounding_note(value: str | None) -> str | None:
    """Rule 2(m) rounding. Ledger L-02 — the exact wording is UNVERIFIED, so
    this reports the observation and does not assert a breach of a provision
    it cannot quote.

    v1.x had a check_mrp_rounding() that returned True on every branch — it
    could not fail, and the 95-99 paise case it was written for was unhandled.
    """
    if not value:
        return None
    try:
        paise = round(float(value.replace(",", "")) * 100) % 100
    except ValueError:
        return None
    if paise == 0:
        return None
    if paise == 50:
        return None                       # half-rupee is conventionally accepted
    return (
        f"the price ends in {paise:02d} paise, which does not follow the rounding "
        f"convention in Rule 2(m) (ledger L-02, wording unverified — recorded as "
        f"an observation, not asserted as a breach)"
    )


def _has_dual_price(ctx: CheckContext) -> bool:
    import re as _re
    amounts = {
        m.group(1)
        for m in _re.finditer(r"(?:M\.?R\.?P\.?|Rs\.?|INR|₹)\s?(\d+(?:[.,]\d{1,2})?)",
                              " ".join(f.source_line or "" for f in ctx.fields.values()),
                              _re.I)
    }
    return len(amounts) > 1


def _absent():
    from ocr_engine import ExtractedField
    return ExtractedField(None, None, None, False)


# Rules 12-13. Ledger L-01: the sub-rule number for the prohibited-qualifier
# provision has been cited as both 12(6) and 13 in earlier drafts, so cite()
# prints the descriptive form until that is resolved.
FORBIDDEN_QUALIFIERS_PATH = settings.RULES_CATALOG.parent / "forbidden_words.json"


@cached(TTLCache(maxsize=1, ttl=300))
def forbidden_qualifiers() -> tuple[str, ...]:
    """One source of truth. v1.x defined three divergent copies of this list in
    the same file, so which words were prohibited depended on which function
    happened to run."""
    return tuple(json.loads(FORBIDDEN_QUALIFIERS_PATH.read_text(encoding="utf-8"))["words"])


SI_UNITS = {
    "kg", "g", "mg", "l", "ml", "m", "cm", "mm",
}
COUNT_UNITS = {"n", "u"}     # numbers/units — permissible for count, not SI


@check("CHK05")
def chk05_net_quantity_expression(ctx: CheckContext) -> FindingResult:
    if getattr(ctx, "llm_result", None) is not None:
        try:
            from llm.schema import StructuredDeclarationResult
            if isinstance(ctx.llm_result, StructuredDeclarationResult):
                from rules.evaluator import evaluate_net_quantity_expression
                return evaluate_net_quantity_expression(ctx.llm_result, ctx)
        except Exception:
            pass
    t = FindingResult(
        "CHK05", "Net quantity free of qualifiers and in permitted units",
        "pass", "major",
        citation=cite("R12-13", "the prohibited-qualifier provision in Rules 12-13"),
        ledger_ref="L-01",
    )
    if ctx.halted:
        return _halted(t, ctx)
    nq = ctx.fields.get("net_quantity")
    if not ctx.ocr_available or nq is None or not nq.found:
        t.verdict, t.reason = "not_assessed", (
            ctx.ocr_failure_reason
            or "Net quantity was not read, so its expression cannot be checked."
        )
        return t

    line = (nq.source_line or "").lower()
    hits = [w for w in forbidden_qualifiers() if re.search(rf"\b{re.escape(w)}\b", line)]

    unit = (ctx.net_quantity_unit or "").lower()
    unit_problem = None
    if unit in COUNT_UNITS:
        # "N" and "U" are not SI units. They are permissible where the Fourth
        # Schedule prescribes declaration by number, and not otherwise.
        # Ledger L-06 — the Fourth Schedule is only partly transcribed.
        t.verdict, t.reason = "not_assessed", (
            f'Quantity declared in "{ctx.net_quantity_unit}", which denotes a '
            f"count rather than an SI unit. Whether a count is the prescribed "
            f"form for this commodity depends on the Fourth Schedule, which is "
            f"only partly transcribed (ledger L-06)."
        )
        t.ledger_ref = "L-06"
        return t
    if unit and unit not in SI_UNITS:
        unit_problem = f'"{ctx.net_quantity_unit}" is not a permitted unit'

    if hits or unit_problem:
        t.verdict, t.limb = "fail", "36(2)"
        parts = []
        if hits:
            parts.append("prohibited qualifier(s) " + ", ".join(f'"{h}"' for h in hits))
        if unit_problem:
            parts.append(unit_problem)
        t.observed = f'Net quantity read as "{nq.source_line}": ' + "; ".join(parts) + "."
        t.required = (
            "Net quantity must be declared in the prescribed unit without any "
            "qualifying word such as those in the prohibited list."
        )
        return t
    t.observed = f'Net quantity "{nq.source_line}" in permitted units, unqualified.'
    t.confidence = nq.confidence
    return t


@check("CHK11")
def chk11_sticker(ctx: CheckContext) -> FindingResult:
    """Rules 6(3) to 6(4A): a sticker may only reduce the declared price and
    must not obscure the original declaration."""
    t = FindingResult(
        "CHK11", "Any sticker only reduces price and does not obscure the original",
        "pass", "major",
        citation=cite("R6-3", "Rules 6(3) to 6(4A) on stickers and corrections"),
    )
    if ctx.halted:
        return _halted(t, ctx)
    if ctx.has_sticker is None:
        t.verdict, t.reason = "not_assessed", (
            "Whether a price sticker is applied to the package was not recorded, "
            "so Rules 6(3) to 6(4A) cannot be applied. This is an operator "
            "observation the app requests at capture."
        )
        return t
    if not ctx.has_sticker:
        t.observed = "No sticker applied; the printed declarations stand as made."
        return t

    problems = []
    if ctx.sticker_reduces_price is False:
        problems.append("the sticker states a price higher than the printed price")
    if ctx.sticker_covers_original is True:
        problems.append("the sticker conceals the original declaration")
    if ctx.sticker_reduces_price is None or ctx.sticker_covers_original is None:
        t.verdict, t.reason = "not_assessed", (
            "A sticker is present, but it was not recorded whether it reduces the "
            "price and whether it conceals the original declaration. Both are "
            "required before Rule 6(4A) can be applied."
        )
        return t
    if problems:
        t.verdict, t.limb = "fail", "36(1)"
        t.observed = "Sticker present and " + "; ".join(problems) + "."
        t.required = (
            "A sticker may only declare a reduced price and must leave the "
            "original declaration legible."
        )
        return t
    t.observed = "Sticker reduces the price and leaves the original declaration visible."
    return t


@check("CHK12")
def chk12_country_of_origin(ctx: CheckContext) -> FindingResult:
    """Rule 6(1)(aa), inserted by GSR 629(E) w.e.f 01.01.2018."""
    if getattr(ctx, "llm_result", None) is not None:
        try:
            from llm.schema import StructuredDeclarationResult
            if isinstance(ctx.llm_result, StructuredDeclarationResult):
                from rules.evaluator import evaluate_country_of_origin_declaration
                return evaluate_country_of_origin_declaration(ctx.llm_result, ctx)
        except Exception:
            pass
    t = FindingResult(
        "CHK12", "Country of origin declared on imported goods", "pass", "major",
        citation=cite("R6-1-aa", "Rule 6(1)(aa), country of origin"),
    )
    if ctx.halted:
        return _halted(t, ctx)

    # THIS BRANCH MUST EXIST. v1.x gated the whole check on `if is_imported:`
    # with no else, so a domestic package produced no CHK12 row and an
    # unrecorded import status produced no row either.
    if ctx.is_imported is None:
        t.verdict, t.reason = "not_assessed", (
            "Whether the goods are imported was not recorded. Rule 6(1)(aa) "
            "requires the country of origin on imported packages, so the "
            "obligation cannot be determined."
        )
        return t
    if not ctx.is_imported:
        t.observed = (
            "Recorded as domestically manufactured, so Rule 6(1)(aa) does not "
            "impose a country-of-origin declaration."
        )
        return t

    coo = ctx.fields.get("country_of_origin")
    if not ctx.ocr_available:
        t.verdict, t.reason = "not_assessed", (
            ctx.ocr_failure_reason
            or "The panel text could not be read, so the country-of-origin "
               "declaration cannot be located."
        )
        return t
    if coo is None or not coo.found:
        t.verdict, t.limb = "fail", "36(1)"
        t.observed = "No country of origin declared on an imported package."
        t.required = "Imported packages must declare the country of origin."
        return t
    t.observed = f'Country of origin declared as "{coo.value}".'
    t.confidence = coo.confidence
    return t


@check("CHK13")
def chk13_best_before(ctx: CheckContext) -> FindingResult:
    """Rule 6(1)(da), inserted by GSR 629(E) w.e.f 01.01.2018."""
    if getattr(ctx, "llm_result", None) is not None:
        try:
            from llm.schema import StructuredDeclarationResult
            if isinstance(ctx.llm_result, StructuredDeclarationResult):
                from rules.evaluator import evaluate_perishable_expiry_declaration
                return evaluate_perishable_expiry_declaration(ctx.llm_result, ctx)
        except Exception:
            pass
    t = FindingResult(
        "CHK13", "Best-before or use-by declared where the commodity is perishable",
        "pass", "major",
        citation=cite("R6-1-da", "Rule 6(1)(da), best-before declaration"),
    )
    if ctx.halted:
        return _halted(t, ctx)
    if ctx.is_perishable is None:
        t.verdict, t.reason = "not_assessed", (
            "Whether the commodity is perishable was not recorded, so it cannot "
            "be determined whether Rule 6(1)(da) requires a best-before date."
        )
        return t
    if not ctx.is_perishable:
        t.observed = (
            "Recorded as non-perishable, so no best-before declaration is due "
            "under Rule 6(1)(da)."
        )
        return t
    bb = ctx.fields.get("best_before")
    if not ctx.ocr_available:
        t.verdict, t.reason = "not_assessed", (
            ctx.ocr_failure_reason or "The panel text could not be read."
        )
        return t
    if bb is None or not bb.found:
        t.verdict, t.limb = "fail", "36(1)"
        t.observed = "No best-before or use-by declaration on a perishable commodity."
        t.required = "Perishable commodities must declare a best-before or use-by date."
        return t
    t.observed = f'Best-before declared as "{bb.value}".'
    t.confidence = bb.confidence
    return t


@check("CHK10")
def chk10_prescribed_quantity(ctx: CheckContext) -> FindingResult:
    """Rule 5 with the Second Schedule. Ledger L-05: the Second Schedule has
    not been transcribed, so this check FAILS OPEN — it returns not_assessed
    rather than guessing at prescribed sizes.
    """
    t = FindingResult(
        "CHK10", "Net quantity is a prescribed standard pack size", "not_assessed",
        "minor",
        citation=cite("R5", "Rule 5 read with the Second Schedule"),
        ledger_ref="L-05",
        reason=(
            "The Second Schedule table of prescribed pack sizes has not been "
            "transcribed into the rule catalog (ledger L-05), so whether this "
            "quantity is a prescribed size cannot be determined. The engine "
            "does not guess at schedule contents."
        ),
    )
    if ctx.halted:
        return _halted(t, ctx)

    catalog = load_catalog()
    table = catalog.get("second_schedule", {})
    group = table.get((ctx.commodity_category or "").lower())
    if not group:
        return t                       # stays not_assessed, with the reason above

    if ctx.net_quantity_value is None:
        t.reason = "Net quantity was not read, so it cannot be matched to the schedule."
        return t

    if ctx.net_quantity_value in group.get("sizes", []):
        t.verdict, t.reason = "pass", None
        t.observed = (
            f"{ctx.net_quantity_value:g} {ctx.net_quantity_unit} is a prescribed "
            f"size for {ctx.commodity_category}."
        )
        return t
    t.verdict, t.reason, t.limb = "fail", None, "36(1)"
    t.observed = (
        f"{ctx.net_quantity_value:g} {ctx.net_quantity_unit} is not among the "
        f"prescribed sizes for {ctx.commodity_category}: "
        f"{', '.join(str(s) for s in group['sizes'])}."
    )
    t.required = "Commodities in this group may only be packed in the prescribed sizes."
    return t


@check("CHK17")
def chk17_fssai_advisory(ctx: CheckContext) -> FindingResult:
    """ADVISORY ONLY. The Food Safety and Standards Act is not administered by
    Legal Metrology, so a missing licence number is an observation to pass to
    the food authority, never a finding under these Rules.

    Because it is advisory it can return only pass or not_assessed. The
    database CHECK constraint ck_finding_severity_coherent enforces that.
    """
    t = FindingResult(
        "CHK17", "FSSAI licence number visible (advisory — not a Legal Metrology finding)",
        "pass", "advisory",
        citation="Food Safety and Standards Act 2006 — referred, not enforced here",
    )
    if ctx.halted:
        return _halted(t, ctx)
    if not ctx.ocr_available:
        t.verdict, t.reason = "not_assessed", (
            ctx.ocr_failure_reason or "The panel text could not be read."
        )
        return t
    if (ctx.commodity_category or "").lower() not in {"food", "beverage", "nutraceutical"}:
        t.observed = "Not a food commodity; no FSSAI observation recorded."
        return t
    m = re.search(r"\b(\d{14})\b", "\n".join(
        f.source_line or "" for f in ctx.fields.values()
    ))
    if m:
        t.observed = f"A 14-digit FSSAI licence number is present ({m.group(1)})."
    else:
        t.verdict, t.reason = "not_assessed", (
            "No 14-digit FSSAI licence number was located. This is recorded for "
            "referral to the food safety authority and is not assessed as a "
            "Legal Metrology matter."
        )
    return t


def _phase3_gate(t: FindingResult, ctx: CheckContext) -> FindingResult | None:
    if ctx.halted:
        return _halted(t, ctx)
    if ctx.phase3_halted:
        t.verdict, t.reason = "not_assessed", (
            "Not assessed: the package is a medical device, whose labelling is "
            "governed by the Medical Devices Rules 2017 rather than by the "
            "typography requirements of these Rules."
        )
        return t
    blocked = measurement_blocked(ctx)
    if blocked:
        t.verdict, t.reason = "not_assessed", blocked
        return t
    return None


@check("CHK06")
def chk06_character_height(ctx: CheckContext) -> FindingResult:
    """Rule 7(2) with Table-I. Ledger L-11."""
    if (
        getattr(ctx, "use_visual_evaluator", False)
        or getattr(ctx, "calibration_spec", None) is not None
        or ctx.scale_source in {"validated_reference", "id1_card", "coin_5inr", "optical_target", "calibrated_ruler"}
    ):
        try:
            from rules.visual_evaluator import evaluate_character_height
            return evaluate_character_height(ctx)
        except Exception:
            pass

    t = FindingResult(
        "CHK06", "Character height meets Table-I for the panel area", "pass", "major",
        citation=cite("R7-2", "Rule 7(2) read with Table-I as substituted w.e.f 01.01.2018"),
        ledger_ref="L-11",
    )
    if (early := _phase3_gate(t, ctx)) is not None:
        return early

    area, area_reason = pdp_area_cm2(ctx)
    if area is None:
        t.verdict, t.reason = "not_assessed", area_reason
        return t
    if not ctx.measured_heights_mm:
        t.verdict, t.reason = "not_assessed", (
            "No character heights could be measured on the rectified panel, so "
            "Table-I compliance cannot be determined."
        )
        return t

    required = min_height_mm(area, ctx.is_blown_moulded)
    smallest_field = min(ctx.measured_heights_mm, key=ctx.measured_heights_mm.get)
    smallest = ctx.measured_heights_mm[smallest_field]
    u = (ctx.mm_per_pixel_uncertainty or 0.0) * 2      # two edges per measurement

    verdict, note = compare_with_uncertainty(smallest, required, u)
    t.verdict = verdict
    t.required = (
        f"Minimum {required:.1f} mm for a principal display panel of "
        f"{area:.0f} cm2"
        + (" (blown-moulded container)" if ctx.is_blown_moulded else "")
        + ". Table-I values are ledger L-11, unverified against the gazette."
    )
    if verdict == "not_assessed":
        t.reason = note
        return t
    from image_processor import format_measurement
    t.observed = (
        f'Smallest measured character height is on "{smallest_field}": '
        + format_measurement(smallest, u, required)
        + f" (scale from {ctx.scale_source})."
    )
    if verdict == "fail":
        t.limb = "36(1)"
    return t


@check("CHK06b")
def chk06b_net_quantity_height(ctx: CheckContext) -> FindingResult:
    """Rule 7 prescribes a separate, larger minimum height for the net quantity
    declaration specifically, keyed to the quantity itself.
    """
    if (
        getattr(ctx, "use_visual_evaluator", False)
        or getattr(ctx, "calibration_spec", None) is not None
        or ctx.scale_source in {"validated_reference", "id1_card", "coin_5inr", "optical_target", "calibrated_ruler"}
    ):
        try:
            from rules.visual_evaluator import evaluate_net_quantity_height
            return evaluate_net_quantity_height(ctx)
        except Exception:
            pass

    t = FindingResult(
        "CHK06b", "Net-quantity declaration meets its own minimum height",
        "not_assessed", "major",
        citation=cite("R7-nq", "the net-quantity-specific minimum height in Rule 7"),
        ledger_ref="L-04",
        reason=(
            "The net-quantity-specific minimum height table has not been "
            "transcribed into the rule catalog (ledger L-04), so this "
            "requirement cannot be evaluated. It is distinct from, and larger "
            "than, the general Table-I minimum checked in CHK06."
        ),
    )
    if ctx.halted:
        return _halted(t, ctx)
    if ctx.phase3_halted:
        t.reason = (
            "Not assessed: medical device, labelled under the Medical Devices "
            "Rules 2017."
        )
        return t

    table = load_catalog().get("net_quantity_heights")
    if not table:
        return t                       # stays not_assessed, reason as constructed

    if (blocked := measurement_blocked(ctx)) is not None:
        t.reason = blocked
        return t
    if ctx.net_quantity_value is None:
        t.reason = "Net quantity was not read, so its height requirement cannot be selected."
        return t
    measured = ctx.measured_heights_mm.get("net_quantity")
    if measured is None:
        t.reason = "The net-quantity declaration was not located on the rectified panel."
        return t

    required = _select_band(table, ctx.net_quantity_value, ctx.net_quantity_unit)
    if required is None:
        t.reason = (
            f"No band in the transcribed table covers "
            f"{ctx.net_quantity_value:g} {ctx.net_quantity_unit}."
        )
        return t
    u = (ctx.mm_per_pixel_uncertainty or 0.0) * 2
    verdict, note = compare_with_uncertainty(measured, required, u)
    t.verdict, t.reason = verdict, (note or None)
    t.required = f"Minimum {required:.1f} mm for the net-quantity declaration (ledger L-04)."
    if verdict != "not_assessed":
        from image_processor import format_measurement
        t.observed = format_measurement(measured, u, required)
    if verdict == "fail":
        t.limb = "36(2)"
    return t


def _select_band(table: dict, value: float, unit: str | None) -> float | None:
    for band in table.get("bands", []):
        if band["unit"] == (unit or "").lower() and value <= band["upper"]:
            return float(band["min_height_mm"])
    return None


# Rule 7(3): character width at least one-third of height — except for the
# narrow glyphs, which have no width requirement.
NARROW_GLYPHS = set("1iIl")
WIDTH_RATIO = 1 / 3


@check("CHK07")
def chk07_character_width(ctx: CheckContext) -> FindingResult:
    if getattr(ctx, "use_visual_evaluator", False):
        try:
            from rules.visual_evaluator import evaluate_character_width
            return evaluate_character_width(ctx)
        except Exception:
            pass

    t = FindingResult(
        "CHK07", "Character width at least one-third of height", "pass", "minor",
        citation=cite("R7-3", "Rule 7(3), character width"),
    )
    if (early := _phase3_gate(t, ctx)) is not None:
        return early
    if not ctx.measured_widths_mm:
        t.verdict, t.reason = "not_assessed", (
            "Character widths could not be measured on the rectified panel."
        )
        return t

    offenders = []
    for label, width in ctx.measured_widths_mm.items():
        if label and all(c in NARROW_GLYPHS for c in label.strip()):
            continue                       # exempt glyphs
        height = ctx.measured_heights_mm.get(label)
        if height and width > 0.1 and width < height * WIDTH_RATIO:
            offenders.append(f'"{label}" {width:.2f} mm wide against {height:.2f} mm high')

    if offenders:
        t.verdict, t.limb = "fail", "36(1)"
        t.observed = "; ".join(offenders) + "."
        t.required = (
            "Character width must be at least one-third of character height, "
            "excluding the glyphs 1, i, I and l."
        )
        return t
    t.observed = "All measured characters meet the one-third width ratio."
    return t


@check("CHK09")
def chk09_clear_space(ctx: CheckContext) -> FindingResult:
    """Rule 8. Ledger L-09 — the multipliers are UNVERIFIED."""
    if (
        getattr(ctx, "use_visual_evaluator", False)
        or getattr(ctx, "clear_space_image_verified", None) is not None
        or getattr(ctx, "verify_clear_space_image", False)
    ):
        try:
            from rules.visual_evaluator import evaluate_clear_space
            return evaluate_clear_space(ctx)
        except Exception:
            pass

    t = FindingResult(
        "CHK09", "Clear space around the net-quantity declaration", "pass", "minor",
        citation=cite("R8", "Rule 8, clear space around the net-quantity declaration"),
        ledger_ref="L-09",
    )
    if (early := _phase3_gate(t, ctx)) is not None:
        return early
    height = ctx.measured_heights_mm.get("net_quantity")
    if height is None or not ctx.clear_space_mm:
        t.verdict, t.reason = "not_assessed", (
            "The net-quantity declaration and the space around it could not both "
            "be measured, so Rule 8 cannot be applied."
        )
        return t

    need = {
        "above": height * 1.0, "below": height * 1.0,
        "left": height * 2.0, "right": height * 2.0,
    }
    u = (ctx.mm_per_pixel_uncertainty or 0.0) * 2
    short = []
    inconclusive = []
    for side, required in need.items():
        got = ctx.clear_space_mm.get(side)
        if got is None:
            inconclusive.append(side)
            continue
        v, _ = compare_with_uncertainty(got, required, u)
        if v == "fail":
            short.append(f"{side} {got:.1f} mm against {required:.1f} mm required")
        elif v == "not_assessed":
            inconclusive.append(side)

    if short:
        t.verdict, t.limb = "fail", "36(2)"
        t.observed = "; ".join(short) + "."
        t.required = (
            "One character height clear above and below, two character heights "
            "left and right (multipliers per ledger L-09, unverified)."
        )
        return t
    if inconclusive:
        t.verdict, t.reason = "not_assessed", (
            "Clear space on the "
            + ", ".join(inconclusive)
            + " side(s) could not be determined within measurement uncertainty."
        )
        return t
    t.observed = "Clear space on all four sides meets the Rule 8 multipliers."
    return t


@check("CHK08")
def chk08_contrast(ctx: CheckContext) -> FindingResult:
    """Rule 9 says the declarations must contrast conspicuously and sets no
    number. Ledger L-10 records the assumption that no numeric threshold
    exists. The engine therefore reports the measured ratio and returns an
    INDICATIVE observation — it never invents a statutory threshold.
    """
    if getattr(ctx, "use_visual_evaluator", False) or getattr(ctx, "engineering_contrast_signal", None):
        try:
            from rules.visual_evaluator import evaluate_conspicuous_contrast
            return evaluate_conspicuous_contrast(ctx)
        except Exception:
            pass

    t = FindingResult(
        "CHK08", "Declarations contrast conspicuously with the background",
        "pass", "minor",
        citation=cite("R9", "Rule 9, conspicuous contrast"),
        ledger_ref="L-10",
    )
    if ctx.halted:
        return _halted(t, ctx)
    if ctx.phase3_halted:
        t.verdict, t.reason = "not_assessed", (
            "Not assessed: medical device, labelled under the Medical Devices "
            "Rules 2017."
        )
        return t
    if not ctx.image_usable:
        t.verdict, t.reason = "not_assessed", ctx.image_quality_reason
        return t
    if ctx.contrast_ratio is None:
        t.verdict, t.reason = "not_assessed", (
            "Text and background luminance could not be separated on this "
            "capture, so contrast cannot be reported."
        )
        return t

    t.observed = (
        f"Measured luminance contrast ratio {ctx.contrast_ratio:.1f}:1 between "
        f"the declarations and their background."
    )
    t.required = (
        "Rule 9 requires conspicuous contrast and prescribes no numeric ratio "
        "(ledger L-10). The measured value is reported for the inspector's "
        "judgement; the engine does not convert it into a statutory threshold."
    )
    if ctx.contrast_ratio < 3.0:
        t.verdict, t.reason = "not_assessed", (
            f"Measured contrast {ctx.contrast_ratio:.1f}:1 is low enough to "
            f"warrant a human view, but Rule 9 sets no numeric threshold, so "
            f"the engine will not record a breach. Inspector to determine "
            f"whether the declarations contrast conspicuously."
        )
    return t


@check("CHK15")
def chk15_ecommerce_listing(ctx: CheckContext) -> FindingResult:
    """Rule 6(10): an e-commerce listing must display every Rule 6(1)
    declaration except the month and year of manufacture.

    C5 — a package scan from the mobile app cannot evaluate this, because
    there is no listing to look at. That is not_assessed with a reason, not a
    silent omission and not a pass.
    """
    if getattr(ctx, "llm_result", None) is not None:
        try:
            from llm.schema import StructuredDeclarationResult
            if isinstance(ctx.llm_result, StructuredDeclarationResult):
                from rules.evaluator import evaluate_ecommerce_listing_declarations
                return evaluate_ecommerce_listing_declarations(ctx.llm_result, ctx)
        except Exception:
            pass
    t = FindingResult(
        "CHK15", "E-commerce listing displays the required declarations",
        "pass", "major",
        citation=cite("R6-10", "Rule 6(10), declarations on e-commerce listings"),
    )
    if ctx.halted:
        return _halted(t, ctx)
    if not ctx.listing_available:
        t.verdict, t.reason = "not_assessed", (
            "No e-commerce listing was captured for this package. Rule 6(10) "
            "governs the online listing rather than the physical panel, so it "
            "cannot be assessed from a package scan. Capture the listing in the "
            "portal to assess it."
        )
        return t

    required_keys = [k for k, _l, _p, _lm in RULE_6_DECLARATIONS if k != "date_of_manufacture"]
    missing = [k for k in required_keys if not ctx.listing_fields.get(k)]
    if missing:
        t.verdict, t.limb = "fail", "36(1)"
        t.observed = "Listing omits: " + ", ".join(missing) + "."
        t.required = (
            "An online listing must show every Rule 6(1) declaration except the "
            "month and year of manufacture."
        )
        return t
    t.observed = "Listing shows all declarations required by Rule 6(10)."
    return t


@check("CHK16")
def chk16_platform_origin_filter(ctx: CheckContext) -> FindingResult:
    """Rule 6(10A), inserted by GSR 128(E) dated 13.02.2026, in force from
    01.07.2026: the platform must offer a searchable country-of-origin filter.

    Commencement matters. Before 01.07.2026 there is no obligation to breach,
    so the engine reports that rather than back-dating the requirement.
    Ledger L-13 — the notification and its commencement are UNVERIFIED.
    """
    t = FindingResult(
        "CHK16", "Platform offers a searchable country-of-origin filter",
        "pass", "major",
        citation=cite("R6-10A", "Rule 6(10A), country-of-origin search filter"),
        ledger_ref="L-13",
    )
    if ctx.halted:
        return _halted(t, ctx)

    commencement = date(2026, 7, 1)
    if ctx.rules_as_at < commencement:
        t.verdict, t.reason = "not_assessed", (
            f"Rule 6(10A) commences on {commencement:%d %B %Y} and the rule set "
            f"applied to this scan is as at {ctx.rules_as_at:%d %B %Y}, so no "
            f"obligation existed at the assessed date (ledger L-13)."
        )
        return t
    if ctx.platform_has_origin_filter is None:
        t.verdict, t.reason = "not_assessed", (
            "This is a platform-level obligation, not a package-level one. It "
            "cannot be assessed from a package scan; it requires an inspection "
            "of the marketplace's own search interface."
        )
        return t
    if not ctx.platform_has_origin_filter:
        t.verdict, t.limb = "fail", "36(1)"
        t.observed = "The platform provides no searchable country-of-origin filter."
        t.required = "The platform must allow consumers to filter by country of origin."
        return t
    t.observed = "A searchable country-of-origin filter is available on the platform."
    return t


@check("CHK18")
def chk18_violation_tier(ctx: CheckContext) -> FindingResult:
    """Section 36 as amended by the Jan Vishwas Act 2026 (Act 8 of 2026),
    w.e.f 01.05.2026. Ledger L-12 — the tiers and amounts are UNVERIFIED.

    This row records WHICH LIMB is engaged and what the graduated response is.
    It never prints a rupee amount, because L-12 is unverified and a wrong
    penalty figure in an enforcement document is worse than no figure.

    The verdict is filled in by derive_result(), which is the only function
    that has seen all eighteen other findings.
    """
    return FindingResult(
        "CHK18", "Graduated response under Section 36 as amended", "not_assessed",
        "critical",
        citation=cite("S36", "Section 36, Legal Metrology Act 2009, as amended by "
                             "the Jan Vishwas Act 2026"),
        ledger_ref="L-12",
        reason="Determined after all other checks — see derive_result().",
    )


def _halted(t: FindingResult, ctx: CheckContext) -> FindingResult:
    """Every check downstream of a halt still produces a row, saying why."""
    t.verdict = "not_assessed"
    t.reason = ctx.halt_reason
    return t


ALL_CHECK_IDS = tuple(cid for cid, _ in CHECKS)
assert len(ALL_CHECK_IDS) == 19, f"expected 19 checks, registered {len(ALL_CHECK_IDS)}"
assert len(set(ALL_CHECK_IDS)) == 19, "duplicate check id registered"

# C5 — the two checks a package scan cannot reach.
LISTING_CHECKS = {"CHK15", "CHK16"}


def run_checks(ctx: CheckContext) -> list[FindingResult]:
    """Runs every registered check. Never skips one. Never raises out."""
    results: list[FindingResult] = []
    for check_id, fn in CHECKS:
        try:
            r = fn(ctx)
        except Exception as e:
            # A bug in one check must not delete the other eighteen findings,
            # and must not be reported as compliance.
            r = FindingResult(
                check_id, f"{check_id} (engine error)", "not_assessed", "major",
                reason=(
                    f"The check could not complete: {type(e).__name__}. This is "
                    f"an engine fault, not a finding about the package. The scan "
                    f"is in the review queue."
                ),
            )
        assert r.check_id == check_id, f"{check_id} returned a finding for {r.check_id}"
        assert r.verdict != "not_assessed" or r.reason, (
            f"{check_id} returned not_assessed without a reason"
        )
        results.append(r)

    assert len(results) == 19
    return results


@dataclass(slots=True)
class ScanVerdict:
    overall_result: str            # C3
    violation_limb: str | None
    recommended_action: str | None
    checks_total: int
    checks_assessed: int
    passed: int
    failed: int
    not_assessed: int


def derive_result(findings: list[FindingResult], ctx: CheckContext) -> ScanVerdict:
    """Four states, derived from nineteen three-state findings."""
    assessable = [f for f in findings if f.check_id != "CHK18"]
    passed = sum(1 for f in assessable if f.verdict == "pass")
    failed = sum(1 for f in assessable if f.verdict == "fail")
    na = sum(1 for f in assessable if f.verdict == "not_assessed")

    limbs = {f.limb for f in assessable if f.verdict == "fail" and f.limb}
    limb = (
        "both" if limbs == {"36(1)", "36(2)"}
        else (next(iter(limbs)) if limbs else None)
    )

    # out_of_scope outranks everything: there is no obligation to have breached.
    if ctx.halted == "CHK03":
        result, action = "out_of_scope", None
    elif ctx.halted == "CHK02":
        result, action = "out_of_scope", None
    elif failed:
        result = "violation"
        action = _graduated_action(assessable, limb)
    elif na and passed == 0:
        result, action = "not_assessed", "recapture_required"
    elif na:
        # Some checks passed and some could not be assessed. This is NOT a
        # clean result and must never be reported as one.
        result, action = "not_assessed", "human_review"
    else:
        result, action = "compliant", None

    # CHK18 is filled in here, where the whole picture is visible.
    tier = next(f for f in findings if f.check_id == "CHK18")
    if result == "violation":
        tier.verdict, tier.reason = "fail", None
        tier.severity = "critical"
        tier.observed = _tier_description(assessable, limb)
        tier.limb = "36(2)" if "36(2)" in limbs else ("36(1)" if "36(1)" in limbs else None)
    elif result == "out_of_scope":
        tier.reason = ctx.halt_reason
    elif result == "compliant":
        tier.verdict, tier.reason = "pass", None
        tier.severity = "critical"
        tier.observed = "No breach identified, so no response under Section 36 arises."
    else:
        tier.reason = (
            f"{na} of {len(assessable)} checks could not be assessed, so the "
            f"graduated response under Section 36 cannot be settled on this "
            f"evidence."
        )

    return ScanVerdict(
        overall_result=result,
        violation_limb=limb,
        recommended_action=action,
        checks_total=len(assessable),          # 18 — CHK18 is the derived tier, not an assessed check (C5)
        checks_assessed=len(assessable) - na,  # e.g. 16 of 18 on a package scan with no web listing
        passed=passed,
        failed=failed,
        not_assessed=na,
    )


def _graduated_action(findings: list[FindingResult], limb: str | None) -> str:
    """Section 15 improvement notice versus prosecution under Section 36.

    v1.x had a determine_violation_tier() whose every branch returned
    'improvement_notice', so the tier was decorative.
    """
    critical = [f for f in findings if f.verdict == "fail" and f.severity == "critical"]
    major = [f for f in findings if f.verdict == "fail" and f.severity == "major"]

    if limb in {"36(2)", "both"}:
        # A false or short net quantity is the limb that goes to the consumer's
        # pocket. It is not an improvement-notice matter.
        return "prosecution_36_2"
    if critical:
        return "prosecution_36_1"
    if len(major) >= 3:
        return "prosecution_36_1"
    return "improvement_notice_s15"


def _tier_description(findings: list[FindingResult], limb: str | None) -> str:
    n = sum(1 for f in findings if f.verdict == "fail")
    limb_text = {
        "36(1)": "the declaration limb, section 36(1)",
        "36(2)": "the net-quantity limb, section 36(2)",
        "both": "both the declaration and net-quantity limbs of section 36",
    }.get(limb or "", "section 36")
    return (
        f"{n} breach(es) identified, engaging {limb_text}. Amounts under the "
        f"Jan Vishwas Act 2026 are ledger L-12 and unverified, so no figure is "
        f"stated; the adjudicating officer applies the schedule in force."
    )


def assess(ctx: CheckContext) -> tuple[list[FindingResult], ScanVerdict, dict]:
    """Called by POST /scans/{id}/assess. Returns findings, verdict, provenance."""
    findings = run_checks(ctx)
    verdict = derive_result(findings, ctx)
    provenance = {
        "rules_as_at": ctx.rules_as_at.isoformat(),
        "catalog_hash": catalog_hash(),
        "engine_version": settings.ENGINE_VERSION,
        "rule_pack_version": "2026.07.v1",
    }
    try:
        from rules import load_rule_pack
        _rp = load_rule_pack()
        provenance["rule_pack_version"] = _rp.rule_pack_version
    except Exception:
        pass
    return findings, verdict, provenance
