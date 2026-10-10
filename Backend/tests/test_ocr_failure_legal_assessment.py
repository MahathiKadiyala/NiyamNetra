"""Backend/tests/test_ocr_failure_legal_assessment.py — Safe Legal Assessment when OCR Fails (Module 2).

Tests:
1. All OCR providers fail (HTTP 403 / provider error) -> No false MRP or Net Quantity violations, CHK18 no penalty.
2. OCR succeeds and evidence supports confirmed missing declaration -> applicable rule fails correctly, CHK18 triggers.
3. Multi-panel partial failure -> unaffected rule (front panel PDP) evaluates cleanly.
4. Multi-panel partial failure with genuine breach -> confirmed breach on unaffected panel is preserved.
5. OCR returns success with zero/unusable text -> absence is not automatically established.
6. Provenance tracing retains source_type.
7. Verdict counts and findings breakdown remain internally consistent.
8. LLM structuring prevents hallucinations on failed OCR panels.
"""
from datetime import date
import pytest

from ocr.base import (
    OcrLine,
    OcrResult,
    STATUS_PROVIDER_ERROR,
    STATUS_SUCCESS,
    STATUS_NO_TEXT,
    STATUS_TIMEOUT,
)
from llm.schema import (
    CandidateItem,
    CommonFieldDeclaration,
    FieldProvenance,
    MrpDeclaration,
    NetQuantityDeclaration,
    STATUS_CONFIRMED,
    STATUS_NOT_OBSERVED,
    StructuredDeclarationResult,
    LLM_STATUS_FAILED,
)
from llm.factory import structure_inspection_ocr
from rules_engine import (
    CheckContext,
    FindingResult,
    ScanVerdict,
    assess,
    derive_result,
)
from rules.visual_evaluator import (
    evaluate_conspicuous_contrast,
    evaluate_character_height,
)
from rules.evaluator import (
    evaluate_mandatory_declarations,
    evaluate_mrp_expression,
    evaluate_net_quantity_expression,
    evaluate_country_of_origin_declaration,
    evaluate_perishable_expiry_declaration,
    evaluate_unit_sale_price,
    evaluate_dimensions,
    evaluate_declaration_rules,
    format_evidence_provenance,
    is_coverage_sufficient,
)
from rules.aggregation import aggregate_inspection_assessment


def test_full_ocr_provider_failure_no_false_violations():
    """When all OCR providers fail (e.g. Google Vision 403), do not treat null fields as violations."""
    ctx = CheckContext(
        panels_captured={"front", "back", "top", "bottom"},
        ocr_available=False,
        ocr_failure_reason="Google Vision HTTP 403: billing disabled",
        panels_failed_ocr={
            "front": "Google Vision HTTP 403: billing disabled",
            "back": "Google Vision HTTP 403: billing disabled",
            "top": "Google Vision HTTP 403: billing disabled",
            "bottom": "Google Vision HTTP 403: billing disabled",
        },
        rules_as_at=date(2026, 7, 1),
    )
    llm_res = StructuredDeclarationResult(
        metadata={"status": LLM_STATUS_FAILED, "evidence_status": "ocr_failed"}
    )
    ctx.llm_result = llm_res

    # 1. Direct declaration evaluation
    findings_decl = evaluate_declaration_rules(llm_res, ctx)

    chk01 = next(f for f in findings_decl if f.check_id == "CHK01")
    assert chk01.verdict == "not_assessed"
    assert chk01.limb is None
    assert "403" in chk01.reason

    chk04 = next(f for f in findings_decl if f.check_id == "CHK04")
    assert chk04.verdict == "not_assessed"
    assert chk04.limb is None
    assert "403" in chk04.reason

    chk05 = next(f for f in findings_decl if f.check_id == "CHK05")
    assert chk05.verdict == "not_assessed"
    assert chk05.limb is None
    assert "403" in chk05.reason

    # 2. Complete engine assessment with CHK18 penalty
    findings, verdict, prov = assess(ctx)
    assert verdict.overall_result == "not_assessed"
    assert verdict.failed == 0
    assert verdict.recommended_action == "recapture_required"

    chk18 = next(f for f in findings if f.check_id == "CHK18")
    assert chk18.verdict == "not_assessed"
    assert "Evidence processing failed" in (chk18.reason or "") or "could not be assessed" in (chk18.reason or "")


def test_ocr_succeeds_and_confirms_missing_declaration():
    """When OCR succeeds with readable evidence and MRP is genuinely absent, CHK04 fails and CHK18 triggers."""
    ctx = CheckContext(
        panels_captured={"front", "back"},
        ocr_available=True,
        ocr_failure_reason=None,
        panels_with_ocr={"front", "back"},
        is_imported=False,
        is_perishable=False,
        rules_as_at=date(2026, 7, 1),
    )
    llm_res = StructuredDeclarationResult(
        commodity_name=CommonFieldDeclaration(
            status=STATUS_CONFIRMED,
            value="Soap",
            raw_text="Bath Soap",
            provenance=FieldProvenance(source_panel="front", source_text="Bath Soap"),
        ),
        net_quantity=NetQuantityDeclaration(
            status=STATUS_CONFIRMED,
            value=100.0,
            unit="g",
            raw_text="Net Wt: 100 g",
            provenance=FieldProvenance(source_panel="front", source_text="Net Wt: 100 g"),
        ),
        mrp=MrpDeclaration(status=STATUS_NOT_OBSERVED, value=None),
    )
    ctx.llm_result = llm_res

    f_mrp = evaluate_mrp_expression(llm_res, ctx)
    assert f_mrp.verdict == "fail"
    assert f_mrp.limb == "36(1)"

    findings, verdict, prov = assess(ctx)
    assert verdict.overall_result == "violation"
    assert verdict.failed >= 1

    chk18 = next(f for f in findings if f.check_id == "CHK18")
    assert chk18.verdict == "fail"
    assert "36(1)" in (chk18.observed or "")


def test_partial_panel_failure_unaffected_rules_assessable():
    """If back panel fails OCR but front panel succeeds with net quantity, CHK05 passes while CHK04 is safe."""
    ctx = CheckContext(
        panels_captured={"front", "back"},
        ocr_available=True,
        panels_with_ocr={"front"},
        panels_failed_ocr={"back": "Provider timeout on back panel"},
        is_imported=False,
        is_perishable=False,
        rules_as_at=date(2026, 7, 1),
    )
    llm_res = StructuredDeclarationResult(
        commodity_name=CommonFieldDeclaration(
            status=STATUS_CONFIRMED,
            value="Wheat Flour",
            raw_text="Wheat Flour",
            provenance=FieldProvenance(source_panel="front", source_text="Wheat Flour"),
        ),
        net_quantity=NetQuantityDeclaration(
            status=STATUS_CONFIRMED,
            value=5.0,
            unit="kg",
            raw_text="Net Qty: 5 kg",
            provenance=FieldProvenance(source_panel="front", source_text="Net Qty: 5 kg"),
        ),
        mrp=MrpDeclaration(status=STATUS_NOT_OBSERVED, value=None),
    )
    ctx.llm_result = llm_res

    f_nq = evaluate_net_quantity_expression(llm_res, ctx)
    assert f_nq.verdict == "pass"

    f_mrp = evaluate_mrp_expression(llm_res, ctx)
    assert f_mrp.verdict == "not_assessed"
    assert "OCR failure on panel 'back'" in f_mrp.reason

    findings, verdict, prov = assess(ctx)
    assert verdict.overall_result == "not_assessed"
    assert verdict.failed == 0
    assert verdict.recommended_action == "human_review"


def test_partial_panel_failure_preserves_genuine_violation():
    """If front panel succeeds and has a prohibited qualifier, CHK05 fails under 36(2) even if back panel failed."""
    ctx = CheckContext(
        panels_captured={"front", "back"},
        ocr_available=True,
        panels_with_ocr={"front"},
        panels_failed_ocr={"back": "Provider HTTP 500"},
        is_imported=False,
        is_perishable=False,
        rules_as_at=date(2026, 7, 1),
    )
    llm_res = StructuredDeclarationResult(
        commodity_name=CommonFieldDeclaration(
            status=STATUS_CONFIRMED,
            value="Rice",
            raw_text="Basmati Rice",
            provenance=FieldProvenance(source_panel="front", source_text="Basmati Rice"),
        ),
        net_quantity=NetQuantityDeclaration(
            status=STATUS_CONFIRMED,
            value=1.0,
            unit="kg",
            raw_text="approx 1 kg",
            provenance=FieldProvenance(source_panel="front", source_text="approx 1 kg"),
        ),
        mrp=MrpDeclaration(status=STATUS_NOT_OBSERVED, value=None),
    )
    ctx.llm_result = llm_res

    f_nq = evaluate_net_quantity_expression(llm_res, ctx)
    assert f_nq.verdict == "fail"
    assert f_nq.limb == "36(2)"
    assert "prohibited qualifier" in f_nq.observed

    findings, verdict, prov = assess(ctx)
    assert verdict.overall_result == "violation"

    chk18 = next(f for f in findings if f.check_id == "CHK18")
    assert chk18.verdict == "fail"
    assert "36(2)" in (chk18.observed or "")


def test_ocr_success_with_zero_text_does_not_assert_absence():
    """When OCR returns SUCCESS but 0 lines were detected, absence is not established."""
    ctx = CheckContext(
        panels_captured={"front", "back"},
        ocr_available=False,
        panels_with_ocr=set(),
        panels_empty_ocr={"front", "back"},
        rules_as_at=date(2026, 7, 1),
    )
    llm_res = StructuredDeclarationResult(
        mrp=MrpDeclaration(status=STATUS_NOT_OBSERVED, value=None),
        net_quantity=NetQuantityDeclaration(status=STATUS_NOT_OBSERVED, value=None),
    )

    f_mrp = evaluate_mrp_expression(llm_res, ctx)
    assert f_mrp.verdict == "not_assessed"
    assert "No readable text detected" in f_mrp.reason

    f_nq = evaluate_net_quantity_expression(llm_res, ctx)
    assert f_nq.verdict == "not_assessed"
    assert "No readable text detected" in f_nq.reason


def test_provenance_tracing_source_type():
    """Provenance payloads must include source_type indicating origin."""
    prov_ocr = FieldProvenance(
        source_panel="front",
        source_text="MRP Rs 50.00",
        source_type="ocr",
    )
    payload_ocr = format_evidence_provenance(prov_ocr)
    assert payload_ocr["source_type"] == "ocr"
    assert payload_ocr["source_panel"] == "front"

    prov_inferred = FieldProvenance(
        source_panel=None,
        source_text=None,
        source_type="inspector_confirmation",
    )
    payload_inferred = format_evidence_provenance(prov_inferred)
    assert payload_inferred["source_type"] == "inspector_confirmation"


def test_findings_breakdown_and_verdict_consistency():
    """ScanVerdict counts must strictly match findings list breakdown."""
    ctx = CheckContext(
        panels_captured={"front", "back"},
        ocr_available=True,
        rules_as_at=date(2026, 7, 1),
    )
    llm_res = StructuredDeclarationResult()
    ctx.llm_result = llm_res
    findings, verdict, prov = assess(ctx)

    total_findings = len(findings)
    assert total_findings == 19

    assessable = [f for f in findings if f.check_id != "CHK18"]
    p_cnt = sum(1 for f in assessable if f.verdict == "pass")
    f_cnt = sum(1 for f in assessable if f.verdict == "fail")
    na_cnt = sum(1 for f in assessable if f.verdict == "not_assessed")

    assert verdict.checks_total == 18
    assert verdict.passed == p_cnt
    assert verdict.failed == f_cnt
    assert verdict.not_assessed == na_cnt
    assert p_cnt + f_cnt + na_cnt == 18
    assert verdict.checks_assessed == p_cnt + f_cnt


def test_llm_factory_failed_ocr_handles_empty_cleanly():
    """When OCR input panels have provider failures, structure_inspection_ocr fails without hallucination."""
    failed_panel = OcrResult(
        lines=[],
        engine="google_vision",
        status=STATUS_PROVIDER_ERROR,
        failure_reason="Google Vision 403 Forbidden",
        panel="front",
    )
    res = structure_inspection_ocr([failed_panel])
    assert res.metadata["status"] == LLM_STATUS_FAILED
    assert res.metadata.get("evidence_status") == "ocr_failed"
    assert res.commodity_name.status == STATUS_NOT_OBSERVED
    assert res.mrp.status == STATUS_NOT_OBSERVED
    assert res.net_quantity.status == STATUS_NOT_OBSERVED
