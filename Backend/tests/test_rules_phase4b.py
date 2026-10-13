"""Backend/tests/test_rules_phase4b.py — Phase 4B Test Suite.

Validates Deterministic Declaration Compliance Evaluation:
- MRP evaluation (confirmed valid/invalid, ambiguous, missing with sufficient/insufficient coverage, dual pricing, provenance).
- Net quantity evaluation (permitted metric units, invalid units, prohibited qualifiers, count units, ambiguous, missing).
- Date evaluation (MFG, PKD, expiry/best-before, unclassified/ambiguous dates, missing).
- Party evaluation (name+address vs name only, importer, packer, ambiguous).
- Consumer care evaluation (valid contact, missing, insufficient coverage).
- Country of origin (imported + present, imported + missing, domestic non-applicable, unknown status).
- Evidence provenance (source panel, image_id, text, bbox, separate confidences, provenance requirement).
- Applicability (physical vs listing, perishable, imported, effective dates).
- Rule pack versioning and effective date awareness.
- Evaluation latency benchmark (< 5 ms).
"""
import time
from datetime import date
from typing import Any

import pytest

from llm.schema import (
    STATUS_AMBIGUOUS,
    STATUS_CONFIRMED,
    STATUS_NOT_OBSERVED,
    CandidateItem,
    CommonFieldDeclaration,
    ConsumerCareDeclaration,
    DateDeclaration,
    FieldProvenance,
    MrpDeclaration,
    NetQuantityDeclaration,
    PartyDeclaration,
    StructuredDeclarationResult,
)
from rules.evaluator import (
    evaluate_country_of_origin_declaration,
    evaluate_declaration_rules,
    evaluate_dimensions,
    evaluate_ecommerce_listing_declarations,
    evaluate_mandatory_declarations,
    evaluate_mrp_expression,
    evaluate_net_quantity_expression,
    evaluate_perishable_expiry_declaration,
    evaluate_unit_sale_price,
    format_evidence_provenance,
    has_valid_provenance,
    is_coverage_sufficient,
)
from rules_engine import CheckContext, FindingResult, assess, run_checks


# ---------------------------------------------------------------------------
# Fixtures & Helpers
# ---------------------------------------------------------------------------

def make_prov(
    panel: str = "front",
    image_id: int = 101,
    text: str = "SAMPLE TEXT",
    bbox: list | None = None,
    ocr_conf: float = 0.95,
    llm_conf: float = 0.98,
) -> FieldProvenance:
    return FieldProvenance(
        source_panel=panel,
        source_image_id=image_id,
        source_text=text,
        source_bbox=bbox or [[10.0, 20.0], [100.0, 20.0], [100.0, 40.0], [10.0, 40.0]],
        ocr_confidence=ocr_conf,
        llm_confidence=llm_conf,
    )


def make_full_llm_result() -> StructuredDeclarationResult:
    """Helper creating a 100% compliant structured declaration result."""
    return StructuredDeclarationResult(
        commodity_name=CommonFieldDeclaration(
            status=STATUS_CONFIRMED,
            value="Whole Wheat Flour",
            raw_text="Whole Wheat Flour",
            provenance=make_prov(panel="front", text="Whole Wheat Flour"),
        ),
        mrp=MrpDeclaration(
            status=STATUS_CONFIRMED,
            value=250.0,
            currency="INR",
            inclusive_of_taxes=True,
            raw_text="MRP Rs. 250.00 (incl. of all taxes)",
            candidates=[],
            provenance=make_prov(panel="back", text="MRP Rs. 250.00 (incl. of all taxes)"),
        ),
        net_quantity=NetQuantityDeclaration(
            status=STATUS_CONFIRMED,
            value=5.0,
            unit="kg",
            normalized_value=5000.0,
            normalized_unit="g",
            raw_text="Net Qty: 5 kg",
            candidates=[],
            provenance=make_prov(panel="front", text="Net Qty: 5 kg"),
        ),
        dates=[
            DateDeclaration(
                status=STATUS_CONFIRMED,
                raw_text="MFG: 03/2026",
                normalized_date="2026-03",
                date_type_candidate="mfg",
                provenance=make_prov(panel="back", text="MFG: 03/2026"),
            )
        ],
        parties=[
            PartyDeclaration(
                status=STATUS_CONFIRMED,
                name="Acme Agro Foods Pvt Ltd",
                address="Plot 45, Sector 9, Navi Mumbai 400705",
                party_type="manufacturer",
                raw_text="Mfd by: Acme Agro Foods Pvt Ltd, Plot 45, Sector 9, Navi Mumbai 400705",
                provenance=make_prov(panel="back", text="Mfd by: Acme Agro Foods Pvt Ltd"),
            )
        ],
        consumer_care=ConsumerCareDeclaration(
            status=STATUS_CONFIRMED,
            phone="1800-200-3000",
            email="care@acmeagro.com",
            address="Customer Care Cell, Plot 45, Sector 9, Navi Mumbai",
            raw_text="Consumer Care: 1800-200-3000, care@acmeagro.com",
            provenance=make_prov(panel="back", text="Consumer Care: 1800-200-3000"),
        ),
        country_of_origin=CommonFieldDeclaration(
            status=STATUS_CONFIRMED,
            value="India",
            raw_text="Country of Origin: India",
            provenance=make_prov(panel="back", text="Country of Origin: India"),
        ),
    )


# ---------------------------------------------------------------------------
# 1. MRP Evaluation Tests
# ---------------------------------------------------------------------------

class TestMrpEvaluation:
    def test_mrp_confirmed_valid(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(
                status=STATUS_CONFIRMED,
                value=260.0,
                currency="INR",
                inclusive_of_taxes=True,
                raw_text="MRP ₹ 260.00 (inclusive of all taxes)",
                provenance=make_prov(panel="mrp", image_id=102, text="MRP ₹ 260.00 (inclusive of all taxes)"),
            )
        )
        ctx = CheckContext(panels_captured={"front", "mrp"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "pass"
        assert f.check_id == "CHK04"
        assert f.rule_id == "RULE_LMPC_04_MRP_FORM"
        assert f.rule_pack_version == "2026.07.v1"
        assert f.evidence_provenance["source_panel"] == "mrp"
        assert f.evidence_provenance["source_image_id"] == 102

    def test_mrp_confirmed_invalid_tax_wording(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(
                status=STATUS_CONFIRMED,
                value=260.0,
                currency="INR",
                inclusive_of_taxes=False,
                raw_text="MRP ₹ 260.00",
                provenance=make_prov(panel="back", text="MRP ₹ 260.00"),
            )
        )
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "not qualified as 'inclusive of all taxes'" in f.observed

    def test_mrp_confirmed_invalid_rounding(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(
                status=STATUS_CONFIRMED,
                value=260.75,
                currency="INR",
                inclusive_of_taxes=True,
                raw_text="MRP ₹ 260.75 incl of taxes",
                provenance=make_prov(panel="back", text="MRP ₹ 260.75 incl of taxes"),
            )
        )
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "fractional paise (75) not rounded" in f.observed

    def test_mrp_confirmed_foreign_currency(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(
                status=STATUS_CONFIRMED,
                value=50.0,
                currency="USD",
                inclusive_of_taxes=True,
                raw_text="$ 50.00 incl taxes",
                provenance=make_prov(panel="back", text="$ 50.00 incl taxes"),
            )
        )
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert "foreign/non-standard currency" in f.observed

    def test_mrp_conflicting_candidates_dual_pricing(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(
                status=STATUS_CONFIRMED,
                value=250.0,
                currency="INR",
                inclusive_of_taxes=True,
                raw_text="MRP ₹ 250 / ₹ 300",
                candidates=[
                    CandidateItem(value=250.0, raw_text="₹ 250"),
                    CandidateItem(value=300.0, raw_text="₹ 300"),
                ],
                provenance=make_prov(panel="mrp", text="MRP ₹ 250 / ₹ 300"),
            )
        )
        ctx = CheckContext(panels_captured={"front", "mrp"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "Conflicting multiple MRP values detected" in f.observed

    def test_mrp_ambiguous(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(
                status=STATUS_AMBIGUOUS,
                value=None,
                raw_text="MRP smudged",
                candidates=[CandidateItem(raw_text="1?0"), CandidateItem(raw_text="180")],
                provenance=make_prov(panel="back", text="MRP smudged"),
            )
        )
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "ambiguous" in f.reason.lower()

    def test_mrp_missing_with_sufficient_coverage(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(status=STATUS_NOT_OBSERVED, value=None)
        )
        # 2 panels captured: sufficient to establish absence
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "No retail sale price declaration observed" in f.observed

    def test_mrp_missing_with_insufficient_coverage(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(status=STATUS_NOT_OBSERVED, value=None)
        )
        # Single front panel: MRP may legitimately be on back/side/mrp panel
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "full package panel coverage not available" in f.reason

    def test_mrp_lacking_provenance_becomes_not_assessed(self):
        llm_res = StructuredDeclarationResult(
            mrp=MrpDeclaration(
                status=STATUS_CONFIRMED,
                value=260.0,
                currency="INR",
                inclusive_of_taxes=True,
                raw_text="₹ 260 incl taxes",
                provenance=None,  # No provenance
            )
        )
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mrp_expression(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "lacks verifiable evidence provenance" in f.reason


# ---------------------------------------------------------------------------
# 2. Net Quantity Evaluation Tests
# ---------------------------------------------------------------------------

class TestNetQuantityEvaluation:
    def test_net_quantity_confirmed_valid(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(
                status=STATUS_CONFIRMED,
                value=500.0,
                unit="g",
                raw_text="Net Weight: 500 g",
                provenance=make_prov(panel="front", text="Net Weight: 500 g"),
            )
        )
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "pass"
        assert f.check_id == "CHK05"
        assert f.rule_id == "RULE_LMPC_05_NET_QUANTITY_UNITS"
        assert f.rule_pack_version == "2026.07.v1"
        assert "500.0 g" in f.observed

    def test_net_quantity_prohibited_qualifier_word(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(
                status=STATUS_CONFIRMED,
                value=500.0,
                unit="g",
                raw_text="Net Weight: approx 500 g",
                provenance=make_prov(panel="front", text="Net Weight: approx 500 g"),
            )
        )
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(2)"
        assert "prohibited qualifier word(s)" in f.observed
        assert "'approx'" in f.observed

    def test_net_quantity_prohibited_when_packed(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(
                status=STATUS_CONFIRMED,
                value=1.0,
                unit="kg",
                raw_text="Net weight when packed: 1 kg",
                provenance=make_prov(panel="front", text="Net weight when packed: 1 kg"),
            )
        )
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(2)"
        assert "'when packed'" in f.observed

    def test_net_quantity_invalid_non_metric_unit(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(
                status=STATUS_CONFIRMED,
                value=2.0,
                unit="lbs",
                raw_text="Net Wt: 2 lbs",
                provenance=make_prov(panel="front", text="Net Wt: 2 lbs"),
            )
        )
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(2)"
        assert "'lbs' is not a permitted metric unit" in f.observed

    def test_net_quantity_count_unit_resolves_not_assessed(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(
                status=STATUS_CONFIRMED,
                value=10.0,
                unit="u",
                raw_text="Quantity: 10 U",
                provenance=make_prov(panel="front", text="Quantity: 10 U"),
            )
        )
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert f.ledger_ref == "L-06"
        assert "count unit 'u'" in f.reason

    def test_net_quantity_ambiguous(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(
                status=STATUS_AMBIGUOUS,
                value=None,
                raw_text="500g or 1kg",
                provenance=make_prov(panel="front", text="500g or 1kg"),
            )
        )
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "ambiguous" in f.reason.lower()

    def test_net_quantity_missing_on_front_panel_fails(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(status=STATUS_NOT_OBSERVED, value=None)
        )
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(2)"

    def test_net_quantity_missing_without_pdp_coverage_not_assessed(self):
        llm_res = StructuredDeclarationResult(
            net_quantity=NetQuantityDeclaration(status=STATUS_NOT_OBSERVED, value=None)
        )
        # Only side panel captured
        ctx = CheckContext(panels_captured={"side"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_net_quantity_expression(llm_res, ctx)
        assert f.verdict == "not_assessed"


# ---------------------------------------------------------------------------
# 3. Date Evaluation Tests
# ---------------------------------------------------------------------------

class TestDateEvaluation:
    def test_mfg_date_confirmed(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "pass"

    def test_pkd_date_confirmed(self):
        llm_res = make_full_llm_result()
        llm_res.dates = [
            DateDeclaration(
                status=STATUS_CONFIRMED,
                raw_text="PKD: 04/2026",
                normalized_date="2026-04",
                date_type_candidate="packing",
                provenance=make_prov(panel="back", text="PKD: 04/2026"),
            )
        ]
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "pass"

    def test_ambiguous_isolated_date_not_assessed(self):
        llm_res = make_full_llm_result()
        llm_res.dates = [
            DateDeclaration(
                status=STATUS_CONFIRMED,
                raw_text="06/26",
                normalized_date="2026-06",
                date_type_candidate="unknown",  # Type not confirmed as mfg/pkd
                provenance=make_prov(panel="batch", text="06/26"),
            )
        ]
        ctx = CheckContext(panels_captured={"front", "batch"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "unclassified date" in f.reason

    def test_perishable_expiry_confirmed(self):
        llm_res = make_full_llm_result()
        llm_res.dates.append(
            DateDeclaration(
                status=STATUS_CONFIRMED,
                raw_text="Use By: 15/08/2026",
                normalized_date="2026-08-15",
                date_type_candidate="expiry",
                provenance=make_prov(panel="back", text="Use By: 15/08/2026"),
            )
        )
        ctx = CheckContext(is_perishable=True, panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_perishable_expiry_declaration(llm_res, ctx)
        assert f.verdict == "pass"
        assert f.check_id == "CHK13"
        assert f.rule_id == "RULE_LMPC_13_EXPIRY_PERISHABLE"
        assert "2026-08-15" in f.observed

    def test_perishable_expiry_missing_fails(self):
        llm_res = make_full_llm_result()
        # No expiry date in dates list
        ctx = CheckContext(is_perishable=True, panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_perishable_expiry_declaration(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "No best-before or use-by date declared" in f.observed

    def test_perishable_not_perishable_passes(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(is_perishable=False, panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_perishable_expiry_declaration(llm_res, ctx)
        assert f.verdict == "pass"
        assert "non-perishable" in f.observed


# ---------------------------------------------------------------------------
# 4. Party / Manufacturer Evaluation Tests
# ---------------------------------------------------------------------------

class TestPartyEvaluation:
    def test_manufacturer_name_and_complete_address_passes(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "pass"

    def test_manufacturer_name_without_address_fails_with_coverage(self):
        llm_res = make_full_llm_result()
        llm_res.parties = [
            PartyDeclaration(
                status=STATUS_CONFIRMED,
                name="Acme Agro Foods Pvt Ltd",
                address="",  # Address missing!
                party_type="manufacturer",
                raw_text="Mfd by: Acme Agro Foods Pvt Ltd",
                provenance=make_prov(panel="back", text="Mfd by: Acme Agro Foods Pvt Ltd"),
            )
        ]
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "Complete address of manufacturer" in f.observed

    def test_manufacturer_name_without_address_not_assessed_single_panel(self):
        llm_res = make_full_llm_result()
        llm_res.parties = [
            PartyDeclaration(
                status=STATUS_CONFIRMED,
                name="Acme Agro Foods Pvt Ltd",
                address="",
                party_type="manufacturer",
                raw_text="Mfd by: Acme Agro Foods Pvt Ltd",
                provenance=make_prov(panel="front", text="Mfd by: Acme Agro Foods Pvt Ltd"),
            )
        ]
        # Only front panel captured; address could be on back panel
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "Complete address of manufacturer/packer/importer" in f.reason

    def test_ambiguous_party_not_assessed(self):
        llm_res = make_full_llm_result()
        llm_res.parties = [
            PartyDeclaration(
                status=STATUS_AMBIGUOUS,
                name="Unknown Entity",
                party_type="unknown",
                raw_text="Mfd by ... unreadable",
                provenance=make_prov(panel="back", text="unreadable"),
            )
        ]
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "Manufacturer/packer details (ambiguous)" in f.reason


# ---------------------------------------------------------------------------
# 5. Consumer Care Evaluation Tests
# ---------------------------------------------------------------------------

class TestConsumerCareEvaluation:
    def test_consumer_care_confirmed_phone_and_email(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "pass"

    def test_consumer_care_missing_with_sufficient_coverage_fails(self):
        llm_res = make_full_llm_result()
        llm_res.consumer_care = ConsumerCareDeclaration(status=STATUS_NOT_OBSERVED)
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "Consumer care contact" in f.observed

    def test_consumer_care_missing_with_single_panel_not_assessed(self):
        llm_res = make_full_llm_result()
        llm_res.consumer_care = ConsumerCareDeclaration(status=STATUS_NOT_OBSERVED)
        ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "Consumer care contact" in f.reason


# ---------------------------------------------------------------------------
# 6. Country of Origin Evaluation Tests
# ---------------------------------------------------------------------------

class TestCountryOfOriginEvaluation:
    def test_imported_with_confirmed_origin_passes(self):
        llm_res = StructuredDeclarationResult(
            country_of_origin=CommonFieldDeclaration(
                status=STATUS_CONFIRMED,
                value="Italy",
                raw_text="Made in Italy",
                provenance=make_prov(panel="back", text="Made in Italy"),
            )
        )
        ctx = CheckContext(is_imported=True, panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_country_of_origin_declaration(llm_res, ctx)
        assert f.verdict == "pass"
        assert f.check_id == "CHK12"
        assert f.rule_id == "RULE_LMPC_12_COUNTRY_OF_ORIGIN_IMPORT"
        assert "Italy" in f.observed

    def test_imported_missing_origin_with_coverage_fails(self):
        llm_res = StructuredDeclarationResult(
            country_of_origin=CommonFieldDeclaration(status=STATUS_NOT_OBSERVED)
        )
        ctx = CheckContext(is_imported=True, panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_country_of_origin_declaration(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"
        assert "No country of origin declared on an imported" in f.observed

    def test_domestic_product_origin_does_not_apply(self):
        llm_res = StructuredDeclarationResult(
            country_of_origin=CommonFieldDeclaration(status=STATUS_NOT_OBSERVED)
        )
        ctx = CheckContext(is_imported=False, panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_country_of_origin_declaration(llm_res, ctx)
        assert f.verdict == "pass"
        assert "domestically manufactured" in f.observed

    def test_unknown_imported_status_not_assessed(self):
        llm_res = StructuredDeclarationResult()
        ctx = CheckContext(is_imported=None, panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_country_of_origin_declaration(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "Whether the goods are imported was not recorded" in f.reason


# ---------------------------------------------------------------------------
# 7. Unit Sale Price & Dimensions Evaluation Tests
# ---------------------------------------------------------------------------

class TestUspAndDimensionsEvaluation:
    def test_usp_not_required_for_package_under_1kg(self):
        llm_res = StructuredDeclarationResult(
            unit_sale_price=CommonFieldDeclaration(status=STATUS_NOT_OBSERVED)
        )
        ctx = CheckContext(net_quantity_value=0.5, net_quantity_unit="kg", rules_as_at=date(2026, 7, 1))
        f = evaluate_unit_sale_price(llm_res, ctx)
        assert f.verdict == "pass"
        assert "<= 1 unit/kg/litre" in f.observed

    def test_usp_required_and_confirmed_on_large_pack(self):
        llm_res = StructuredDeclarationResult(
            unit_sale_price=CommonFieldDeclaration(
                status=STATUS_CONFIRMED,
                value="₹ 50.00 per kg",
                raw_text="USP: ₹ 50.00 / kg",
                provenance=make_prov(panel="back", text="USP: ₹ 50.00 / kg"),
            )
        )
        ctx = CheckContext(net_quantity_value=5.0, net_quantity_unit="kg", rules_as_at=date(2026, 7, 1))
        f = evaluate_unit_sale_price(llm_res, ctx)
        assert f.verdict == "pass"
        assert "₹ 50.00 per kg" in f.observed

    def test_usp_missing_on_large_pack_fails(self):
        llm_res = StructuredDeclarationResult(
            unit_sale_price=CommonFieldDeclaration(status=STATUS_NOT_OBSERVED)
        )
        ctx = CheckContext(
            net_quantity_value=5.0,
            net_quantity_unit="kg",
            panels_captured={"front", "back"},
            rules_as_at=date(2026, 7, 1),
        )
        f = evaluate_unit_sale_price(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"

    def test_dimensions_not_required_for_weight_commodity(self):
        llm_res = StructuredDeclarationResult()
        ctx = CheckContext(net_quantity_unit="g", rules_as_at=date(2026, 7, 1))
        f = evaluate_dimensions(llm_res, ctx)
        assert f.verdict == "pass"
        assert "not sold by length or number" in f.observed

    def test_dimensions_confirmed_for_count_commodity(self):
        llm_res = StructuredDeclarationResult(
            dimensions=CommonFieldDeclaration(
                status=STATUS_CONFIRMED,
                value="10 cm x 15 cm",
                raw_text="Size: 10 cm x 15 cm",
                provenance=make_prov(panel="front", text="Size: 10 cm x 15 cm"),
            )
        )
        ctx = CheckContext(net_quantity_unit="pcs", rules_as_at=date(2026, 7, 1))
        f = evaluate_dimensions(llm_res, ctx)
        assert f.verdict == "pass"
        assert "10 cm x 15 cm" in f.observed

    def test_dimensions_missing_for_count_commodity_fails(self):
        llm_res = StructuredDeclarationResult(
            dimensions=CommonFieldDeclaration(status=STATUS_NOT_OBSERVED)
        )
        ctx = CheckContext(net_quantity_unit="pcs", panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_dimensions(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"


# ---------------------------------------------------------------------------
# 8. E-Commerce Listing & Applicability Tests
# ---------------------------------------------------------------------------

class TestEcommerceAndApplicability:
    def test_ecommerce_listing_not_assessed_for_package_scan(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(listing_available=False, rules_as_at=date(2026, 7, 1))
        f = evaluate_ecommerce_listing_declarations(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "physical package scan" in f.reason

    def test_ecommerce_listing_complete_passes(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(
            listing_available=True,
            listing_fields={
                "commodity": "Wheat Flour",
                "manufacturer": "Acme Ltd",
                "net_quantity": "5 kg",
                "mrp": "Rs 250",
                "consumer_care": "1800-111-222",
            },
            rules_as_at=date(2026, 7, 1),
        )
        f = evaluate_ecommerce_listing_declarations(llm_res, ctx)
        assert f.verdict == "pass"

    def test_ecommerce_listing_missing_fields_fails(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(
            listing_available=True,
            listing_fields={"commodity": "Wheat Flour"},
            rules_as_at=date(2026, 7, 1),
        )
        f = evaluate_ecommerce_listing_declarations(llm_res, ctx)
        assert f.verdict == "fail"
        assert f.limb == "36(1)"


# ---------------------------------------------------------------------------
# 9. Effective Date Awareness Tests (Section 20)
# ---------------------------------------------------------------------------

class TestEffectiveDateSelection:
    def test_rule_evaluated_when_effective(self):
        llm_res = make_full_llm_result()
        # 2026-07-01 is within effective window
        ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
        f = evaluate_mandatory_declarations(llm_res, ctx)
        assert f.verdict == "pass"

    def test_rule_not_effective_prior_to_effective_date(self):
        llm_res = StructuredDeclarationResult(
            unit_sale_price=CommonFieldDeclaration(
                status=STATUS_CONFIRMED,
                value="₹ 50 / kg",
                raw_text="₹ 50 / kg",
                provenance=make_prov(),
            )
        )
        # CHK19 effective_from is 2022-12-01. If inspection date is 2020-01-01:
        ctx = CheckContext(
            net_quantity_value=5.0,
            net_quantity_unit="kg",
            rules_as_at=date(2020, 1, 1),
        )
        f = evaluate_unit_sale_price(llm_res, ctx)
        assert f.verdict == "not_assessed"
        assert "not effective on inspection date 2020-01-01" in f.reason


# ---------------------------------------------------------------------------
# 10. End-to-End Engine Integration Tests
# ---------------------------------------------------------------------------

class TestEngineIntegration:
    def test_run_checks_consumes_structured_declarations(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(
            llm_result=llm_res,
            panels_captured={"front", "back"},
            ocr_available=True,
            is_imported=False,
            is_perishable=False,
            rules_as_at=date(2026, 7, 1),
        )
        findings = run_checks(ctx)
        assert len(findings) == 19

        # Check CHK01, CHK04, CHK05, CHK12, CHK13
        chk01 = next(f for f in findings if f.check_id == "CHK01")
        assert chk01.verdict == "pass"
        assert chk01.rule_id == "RULE_LMPC_01_MANDATORY_DECLARATIONS"
        assert chk01.rule_pack_version == "2026.07.v1"
        assert chk01.evidence_provenance is not None

        chk04 = next(f for f in findings if f.check_id == "CHK04")
        assert chk04.verdict == "pass"
        assert chk04.rule_id == "RULE_LMPC_04_MRP_FORM"

        chk05 = next(f for f in findings if f.check_id == "CHK05")
        assert chk05.verdict == "pass"
        assert chk05.rule_id == "RULE_LMPC_05_NET_QUANTITY_UNITS"

        chk12 = next(f for f in findings if f.check_id == "CHK12")
        assert chk12.verdict == "pass"
        assert chk12.rule_id == "RULE_LMPC_12_COUNTRY_OF_ORIGIN_IMPORT"

    def test_assess_returns_versioned_provenance(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(
            llm_result=llm_res,
            panels_captured={"front", "back"},
            ocr_available=True,
            is_imported=False,
            is_perishable=False,
            rules_as_at=date(2026, 7, 1),
        )
        findings, verdict, provenance = assess(ctx)
        assert provenance["rule_pack_version"] == "2026.07.v1"
        assert verdict.checks_total == 19
        assert isinstance(findings, list)


# ---------------------------------------------------------------------------
# 11. Performance Benchmark (Section 23)
# ---------------------------------------------------------------------------

class TestEvaluatorPerformance:
    def test_deterministic_evaluator_latency(self):
        llm_res = make_full_llm_result()
        ctx = CheckContext(
            panels_captured={"front", "back"},
            ocr_available=True,
            is_imported=True,
            is_perishable=True,
            net_quantity_value=5.0,
            net_quantity_unit="kg",
            rules_as_at=date(2026, 7, 1),
        )

        # Warm up
        for _ in range(20):
            evaluate_declaration_rules(llm_res, ctx)

        iterations = 500
        timings_ms: list[float] = []

        for _ in range(iterations):
            t0 = time.perf_counter()
            findings = evaluate_declaration_rules(llm_res, ctx)
            t1 = time.perf_counter()
            timings_ms.append((t1 - t0) * 1000.0)

        timings_ms.sort()
        mean_ms = sum(timings_ms) / len(timings_ms)
        p50_ms = timings_ms[len(timings_ms) // 2]
        p95_ms = timings_ms[int(len(timings_ms) * 0.95)]

        print(f"\n--- Phase 4B Evaluator Benchmark ({iterations} runs) ---")
        print(f"Rules evaluated per run: {len(findings)}")
        print(f"Mean: {mean_ms:.4f} ms")
        print(f"p50:  {p50_ms:.4f} ms")
        print(f"p95:  {p95_ms:.4f} ms")

        # Must execute strictly in milliseconds (target < 5.0 ms in-memory)
        assert p95_ms < 5.0, f"p95 latency {p95_ms:.2f} ms exceeded 5.0 ms target"
        assert len(findings) == 8
