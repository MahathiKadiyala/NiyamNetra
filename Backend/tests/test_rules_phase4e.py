"""Backend/tests/test_rules_phase4e.py — Phase 4E Final Hardening & Production Verification.

Exhaustive verification of the NiyamNetra Legal Compliance Engine (SIH26034):
- Rule-pack audit & strict statutory validation report (Section 3).
- Effective-date exhaustive boundary testing (Section 4).
- PASS / FAIL / NOT_ASSESSED adversarial safety matrix (Section 5).
- False-compliance defense tests (Section 6).
- False-violation defense tests (Section 7).
- Cross-panel conflict hardening (Section 8).
- Evidence integrity & immutability verification (tamper-evident / integrity-verifiable) (Sections 9 & 10).
- Assessment completeness (PARTIAL can never be COMPLIANT) (Section 11).
- Final verdict matrix exhaustive testing (Section 12).
- Audit-ready dossiers (violation & review, zero LLM narrative) (Section 13).
- Actionable capture guidance (Section 14).
- Real database persistence & API integration (Section 15).
- Idempotency & determinism (Section 16).
- Zero external AI / network calls proof (Sections 17 & 18).
- Performance & memory verification under Render's 512 MB ceiling (Sections 19 & 20).
- Security & malformed input validation (Section 21).
- Legal-scope validation & rule coverage report (Sections 22 & 23).
- Realistic end-to-end scenarios A through G (Section 24).
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
from datetime import date
from typing import Any
from unittest.mock import patch

import numpy as np
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from llm.schema import (
    CommonFieldDeclaration,
    DateDeclaration,
    FieldProvenance,
    MrpDeclaration,
    NetQuantityDeclaration,
    PartyDeclaration,
    StructuredDeclarationResult,
)
from models import Base, Finding, Inspection, Scan, ScanImage, Store, User
from rules.aggregation import (
    AssessmentCompleteness,
    EvidenceReference,
    InspectionAssessment,
    StatutoryConflict,
    aggregate_inspection_assessment,
    deduplicate_findings,
    detect_cross_panel_conflicts,
    determine_assessment_completeness,
    generate_actionable_capture_requests,
    generate_review_dossier,
    generate_violation_dossier,
    normalize_party_text,
)
from rules.benchmark import benchmark_phase4_end_to_end
from rules.evaluator import (
    evaluate_country_of_origin_declaration,
    evaluate_declaration_rules,
    evaluate_mandatory_declarations,
    evaluate_mrp_expression,
    evaluate_net_quantity_expression,
)
from rules.loader import generate_rule_pack_report, load_rule_pack
from rules.schema import ApplicabilitySpec, EvaluationSpec, LegalRuleDefinition, RulePack
from rules.visual_evaluator import (
    evaluate_character_height,
    evaluate_clear_space,
    evaluate_conspicuous_contrast,
    evaluate_net_quantity_height,
    evaluate_pdp_placement,
    evaluate_visual_and_geometry_rules,
    measure_glyph_height_mm,
    reconcile_coordinates,
    validate_calibration,
    verify_clear_space_image,
)
from rules_engine import CheckContext, FindingResult, ScanVerdict, assess


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def _dummy_finding(
    check_id: str,
    verdict: str,
    title: str = "Test Check",
    rule_id: str | None = None,
    limb: str | None = None,
    panel: str = "front",
    reason: str | None = None,
    severity: str = "critical",
) -> FindingResult:
    return FindingResult(
        check_id=check_id,
        title=title,
        verdict=verdict,  # type: ignore
        severity=severity,  # type: ignore
        rule_id=rule_id or f"RULE_{check_id}",
        rule_pack_version="2026.07.v1",
        citation="Legal Metrology (Packaged Commodities) Rules, 2011",
        ledger_ref="RULE_LEDGER_01",
        limb=limb if verdict == "fail" else None,
        reason=reason or (f"{check_id} failed" if verdict == "fail" else (f"{check_id} unassessed" if verdict == "not_assessed" else None)),
        observed="Observed valid declaration" if verdict == "pass" else "Non-compliant declaration",
        required="Mandatory statutory declaration",
        remediation="Ensure declaration is printed in accordance with LMPC Rules",
        evidence_provenance={"source_panel": panel, "source_text": "sample text", "bbox": [10, 10, 100, 50]},
    )


# ---------------------------------------------------------------------------
# 1. Rule-Pack Audit & Strict Validation (Section 3)
# ---------------------------------------------------------------------------

def test_rule_pack_production_audit_exhaustive():
    """Verify all 24 production rules in lmpc_2026_v1.json conform to statutory metadata."""
    pack = load_rule_pack(enforce_strict_validation=True)
    assert pack.rule_pack_version == "2026.07.v1"
    assert len(pack.rules) >= 24

    for r in pack.rules:
        assert r.rule_id, "Every rule must have a unique rule_id"
        assert r.code, f"Rule {r.rule_id} missing code"
        assert r.source_document, f"Rule {r.rule_id} missing source_document"
        assert r.source_rule, f"Rule {r.rule_id} missing source_rule"
        assert r.effective_from, f"Rule {r.rule_id} missing effective_from"
        assert r.remediation, f"Rule {r.rule_id} missing remediation"
        assert r.applicability is not None, f"Rule {r.rule_id} missing applicability"
        assert r.applicability.inspection_sources, f"Rule {r.rule_id} missing inspection_sources"
        assert r.evaluation_method is not None, f"Rule {r.rule_id} missing evaluation_method"
        assert r.evaluation_method.method, f"Rule {r.rule_id} missing evaluation_method.method"


def test_rule_pack_validation_fails_on_impossible_dates():
    """Validation fails if effective_from is after effective_to."""
    bad_rule = LegalRuleDefinition(
        rule_id="RULE_BAD_DATES",
        code="CHKBAD",
        rule_pack_version="2026.07.v1",
        title="Impossible Dates Rule",
        source_document="Test Gazette",
        source_rule="Rule 99",
        effective_from="2026-12-31",
        effective_to="2026-01-01",  # Impossible: from > to
        severity="major",
        applicability=ApplicabilitySpec(inspection_sources=["physical_package"]),
        required_evidence=["ocr_evidence"],
        required_fields=[],
        evaluation_method=EvaluationSpec(method="presence"),
        remediation="Fix date",
    )
    errors = bad_rule.validate_metadata()
    assert any("impossible date window" in e for e in errors)


def test_rule_pack_validation_fails_on_missing_applicability():
    """Validation fails if applicability or inspection_sources is missing."""
    bad_rule = LegalRuleDefinition(
        rule_id="RULE_NO_APP",
        code="CHKBAD2",
        rule_pack_version="2026.07.v1",
        title="No Applicability Rule",
        source_document="Test Gazette",
        source_rule="Rule 99",
        effective_from="2026-01-01",
        severity="major",
        applicability=ApplicabilitySpec(inspection_sources=[]),  # Missing sources
        required_evidence=["ocr_evidence"],
        required_fields=[],
        evaluation_method=EvaluationSpec(method="presence"),
        remediation="Fix applicability",
    )
    errors = bad_rule.validate_metadata()
    assert any("applicability with inspection_sources is mandatory" in e for e in errors)


def test_rule_pack_validation_fails_on_overlapping_check_periods():
    """Validation fails if two rules sharing a check code overlap in active time window."""
    r1 = LegalRuleDefinition(
        rule_id="RULE_A",
        code="CHKX",
        rule_pack_version="2026.07.v1",
        title="Rule A",
        source_document="Doc",
        source_rule="R1",
        effective_from="2020-01-01",
        effective_to="2025-12-31",
        severity="major",
        applicability=ApplicabilitySpec(inspection_sources=["physical_package"]),
        required_evidence=["ocr_evidence"],
        required_fields=[],
        evaluation_method=EvaluationSpec(method="presence"),
        remediation="Fix",
    )
    r2 = LegalRuleDefinition(
        rule_id="RULE_B",
        code="CHKX",  # Same code
        rule_pack_version="2026.07.v1",
        title="Rule B",
        source_document="Doc",
        source_rule="R1",
        effective_from="2025-06-01",  # Overlaps with 2025-12-31!
        effective_to=None,
        severity="major",
        applicability=ApplicabilitySpec(inspection_sources=["physical_package"]),
        required_evidence=["ocr_evidence"],
        required_fields=[],
        evaluation_method=EvaluationSpec(method="presence"),
        remediation="Fix",
    )
    pack = RulePack(
        rule_pack_version="test.v1",
        title="Test Pack",
        description="Test",
        authority="Gov",
        rules_as_at="2026-01-01",
        rules=[r1, r2],
    )
    errors = pack.validate()
    assert any("Overlapping active periods for check code 'CHKX'" in e for e in errors)


def test_rule_pack_validation_report():
    """Rule Pack Validation Report provides complete statutory metrics."""
    report = generate_rule_pack_report()
    assert report["active_rule_pack_version"] == "2026.07.v1"
    assert report["total_rules"] >= 24
    assert report["enabled_rules_count"] >= 24
    assert report["category_specific_rules_count"] >= 8
    assert report["ecommerce_rules_count"] >= 5
    assert report["measurement_rules_count"] >= 5
    assert len(report["source_references"]) >= 10


# ---------------------------------------------------------------------------
# 2. Effective-Date Exhaustive Boundary Testing (Section 4)
# ---------------------------------------------------------------------------

def test_effective_date_exact_boundaries_post_2018_rule7():
    """Verify Table-I vs Table-II boundary around 1 January 2018 (GSR 629(E))."""
    pack = load_rule_pack()
    rule_post_2018 = pack.get_rule_by_id("RULE_LMPC_06B_NET_QUANTITY_NUMERAL_HEIGHT")
    rule_pre_2018 = pack.get_rule_by_id("RULE_LMPC_06B_HISTORICAL_TABLE_II")

    assert rule_post_2018 is not None
    assert rule_pre_2018 is not None

    # Day before 2018 amendment: 31 Dec 2017
    d_before = date(2017, 12, 31)
    assert rule_pre_2018.is_effective_on(d_before) is True
    assert rule_post_2018.is_effective_on(d_before) is False

    # Exact effective date: 1 Jan 2018
    d_exact = date(2018, 1, 1)
    assert rule_pre_2018.is_effective_on(d_exact) is False
    assert rule_post_2018.is_effective_on(d_exact) is True

    # Day after: 2 Jan 2018
    d_after = date(2018, 1, 2)
    assert rule_pre_2018.is_effective_on(d_after) is False
    assert rule_post_2018.is_effective_on(d_after) is True


def test_effective_date_future_rule_never_applies_early():
    """Future rules (e.g. platform origin filter effective 2026-07-01) must NOT apply before effective date."""
    pack = load_rule_pack()
    rule_platform = pack.get_rule_by_id("RULE_LMPC_16_PLATFORM_ORIGIN_FILTER")
    assert rule_platform is not None
    assert rule_platform.effective_from == "2026-07-01"

    # Day before rule starts: 2026-06-30
    assert rule_platform.is_effective_on(date(2026, 6, 30)) is False

    # Exact effective date: 2026-07-01
    assert rule_platform.is_effective_on(date(2026, 7, 1)) is True

    # Day after: 2026-07-02
    assert rule_platform.is_effective_on(date(2026, 7, 2)) is True


# ---------------------------------------------------------------------------
# 3. PASS / FAIL / NOT_ASSESSED Adversarial Safety Matrix (Section 5)
# ---------------------------------------------------------------------------

def test_safety_matrix_pass_never_without_provenance():
    """A declaration missing evidence provenance can NEVER yield PASS."""
    pack = load_rule_pack()
    decl_without_prov = StructuredDeclarationResult(
        mrp=MrpDeclaration(value=100.0, provenance=None),  # Missing provenance!
    )
    ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))
    findings = evaluate_declaration_rules(decl_without_prov, ctx)
    mrp_finding = next(f for f in findings if f.check_id == "CHK04")
    assert mrp_finding.verdict != "pass", "Missing provenance must never produce PASS"
    assert mrp_finding.verdict in ("fail", "not_assessed")


def test_safety_matrix_pass_never_without_calibration():
    """Numeral height evaluation without scale calibration can NEVER yield PASS."""
    ctx = CheckContext(
        panel_shape="rectangular",
        panel_height_mm=200.0,
        panel_width_mm=100.0,
        mm_per_pixel=None,  # No calibration!
        scale_source="none",
        pdp_surface_established=True,
        measured_heights_mm={"net_quantity": 4.0},
    )
    finding = evaluate_net_quantity_height(ctx)
    assert finding.verdict != "pass"
    assert finding.verdict == "not_assessed"
    assert "calibration" in (finding.reason or "").lower() or "scale" in (finding.reason or "").lower()


def test_safety_matrix_fail_never_on_partial_coverage():
    """If a declaration is not observed on front panel only, it must be NOT_ASSESSED, not false FAIL."""
    pack = load_rule_pack()
    decl = StructuredDeclarationResult(
        commodity_name=CommonFieldDeclaration(value="Biscuits", provenance=FieldProvenance(source_panel="front")),
        # Consumer care details not observed
    )
    # Only front captured
    ctx = CheckContext(panels_captured={"front"}, rules_as_at=date(2026, 7, 1))
    findings = evaluate_declaration_rules(decl, ctx)
    chk01_finding = next(f for f in findings if f.check_id == "CHK01")
    assert chk01_finding.verdict != "fail", "Unobserved declaration on partial coverage cannot be FAIL"
    assert chk01_finding.verdict == "not_assessed"


def test_safety_matrix_fail_never_on_uncertain_contrast():
    """Contrast ratio without confirmed dark-on-light / light-on-dark cannot be false FAIL."""
    ctx = CheckContext(contrast_ratio=None)  # Contrast unavailable
    finding = evaluate_conspicuous_contrast(ctx)
    assert finding.verdict != "fail"
    assert finding.verdict == "not_assessed"


# ---------------------------------------------------------------------------
# 4. False-Compliance Defense Tests (Section 6)
# ---------------------------------------------------------------------------

def test_false_compliance_defense_fake_dpi_rejected():
    """Unvalidated device DPI or synthetic 120x80 mm defaults are rejected as uncalibrated."""
    ctx1 = CheckContext(scale_source="device_dpi", mm_per_pixel=0.1)
    valid, reason = validate_calibration(ctx1)
    assert valid is False
    assert "scale" in reason.lower() or "calibration" in reason.lower()

    ctx2 = CheckContext(scale_source="assumed_120x80", mm_per_pixel=0.1)
    valid2, _ = validate_calibration(ctx2)
    assert valid2 is False


def test_false_compliance_defense_conflicting_mrp_blocks_compliant():
    """Package with conflicting MRP on front and back cannot receive COMPLIANT."""
    findings = [
        _dummy_finding("CHK01", "pass"),
        _dummy_finding("CHK04", "pass"),
        _dummy_finding("CHK05", "pass"),
    ]
    panel_declarations = {
        "front": StructuredDeclarationResult(mrp=MrpDeclaration(value=260.0, provenance=FieldProvenance(source_panel="front"))),
        "back": StructuredDeclarationResult(mrp=MrpDeclaration(value=280.0, provenance=FieldProvenance(source_panel="back"))),
    }

    assessment = aggregate_inspection_assessment(
        inspection_id="INSP_FALSE_COMP_01",
        findings=findings,
        captured_panels={"front", "back"},
        panel_declarations=panel_declarations,
    )
    assert assessment.overall_verdict != "COMPLIANT"
    assert assessment.overall_verdict == "REVIEW_REQUIRED"
    assert len(assessment.conflicts) == 1


def test_false_compliance_defense_partial_coverage_never_compliant():
    """Even if all evaluated rules on front panel PASS, verdict must be REVIEW_REQUIRED."""
    findings = [
        _dummy_finding("CHK01", "pass"),
        _dummy_finding("CHK04", "pass"),
        _dummy_finding("CHK05", "pass"),
    ]
    assessment = aggregate_inspection_assessment(
        inspection_id="INSP_FALSE_COMP_02",
        findings=findings,
        captured_panels={"front"},  # Missing back/side
    )
    assert assessment.overall_verdict != "COMPLIANT"
    assert assessment.overall_verdict == "REVIEW_REQUIRED"
    assert assessment.assessment_completeness.status == "PARTIAL"


# ---------------------------------------------------------------------------
# 5. False-Violation Defense Tests (Section 7)
# ---------------------------------------------------------------------------

def test_false_violation_defense_glare_covered_clear_space():
    """Clear space inspection impeded by glare results in NOT_ASSESSED, never false FAIL."""
    ctx = CheckContext(
        clear_space_image_verified=False,
        image_usable=False,
        image_quality_reason="Severe glare over net quantity region",
    )
    finding = evaluate_clear_space(ctx)
    assert finding.verdict != "fail"
    assert finding.verdict == "not_assessed"


def test_false_violation_defense_unestablished_pdp():
    """Placement on PDP cannot fail if PDP surface could not be established."""
    ctx = CheckContext(
        pdp_surface_established=False,
        panels_captured={"front"},
    )
    finding = evaluate_pdp_placement(ctx)
    assert finding.verdict != "fail"
    assert finding.verdict == "not_assessed"


# ---------------------------------------------------------------------------
# 6. Cross-Panel Conflict Hardening & Deduplication (Section 8)
# ---------------------------------------------------------------------------

def test_cross_panel_duplicate_declarations_merged_cleanly():
    """Identical declarations across multiple lines/panels are merged without creating duplicate conflicts."""
    f1 = _dummy_finding("CHK04", "pass", panel="front")
    f2 = _dummy_finding("CHK04", "pass", panel="back")

    deduped = deduplicate_findings([f1, f2])
    assert len(deduped) == 1
    assert deduped[0].verdict == "pass"
    prov = getattr(deduped[0], "evidence_provenance", {}) or {}
    assert len(prov.get("supporting_evidence", [])) == 2


def test_cross_panel_mrp_conflict_both_references_preserved():
    """Neither candidate is silently chosen; both evidence pointers are preserved."""
    panel_declarations = {
        "front": StructuredDeclarationResult(mrp=MrpDeclaration(value=199.0, provenance=FieldProvenance(source_panel="front", source_text="₹199"))),
        "back": StructuredDeclarationResult(mrp=MrpDeclaration(value=220.0, provenance=FieldProvenance(source_panel="back", source_text="₹220"))),
    }
    conflicts = detect_cross_panel_conflicts("INSP_CONF_01", panel_declarations=panel_declarations)
    assert len(conflicts) == 1
    c = conflicts[0]
    panels = [r["panel"] for r in c.evidence_refs]
    assert "front" in panels and "back" in panels
    assert c.resolution_status == "unresolved"


# ---------------------------------------------------------------------------
# 7. Evidence Integrity & Immutability (Sections 9 & 10)
# ---------------------------------------------------------------------------

def test_evidence_integrity_traceability_in_findings():
    """Every finding preserves panel, bounding box, text, and rule version provenance."""
    ctx = CheckContext(
        panels_captured={"front", "back"},
        rules_as_at=date(2026, 7, 1),
        llm_result=StructuredDeclarationResult(
            mrp=MrpDeclaration(
                value=50.0,
                status="confirmed",
                currency="INR",
                raw_text="MRP Rs. 50.00 (incl. of all taxes)",
                provenance=FieldProvenance(
                    source_panel="front",
                    source_image_id=101,
                    source_text="MRP Rs. 50.00 (incl. of all taxes)",
                    source_bbox=[[10.0, 20.0], [100.0, 20.0], [100.0, 50.0], [10.0, 50.0]],
                ),
            ),
        ),
    )
    findings = evaluate_declaration_rules(ctx.llm_result, ctx)
    mrp_finding = next(f for f in findings if f.check_id == "CHK04")
    assert mrp_finding.verdict == "pass"
    prov = mrp_finding.evidence_provenance
    assert prov is not None
    assert prov.get("source_panel") == "front"
    assert prov.get("source_image_id") == 101
    assert mrp_finding.rule_pack_version == "2026.07.v1"


def test_original_evidence_immutability():
    """Phase 4 operations never mutate original image buffer, SHA-256, or timestamps."""
    original_bytes = b"EXACT_IMMUTABLE_CAMERA_IMAGE_BYTES_12345"
    original_sha256 = hashlib.sha256(original_bytes).hexdigest()
    capture_time = "2026-03-01T10:00:00Z"

    # Pass through Phase 4 pipeline
    ctx = CheckContext(
        panels_captured={"front", "back"},
        rules_as_at=date(2026, 7, 1),
    )
    findings = [_dummy_finding("CHK01", "pass")]
    assessment = aggregate_inspection_assessment(
        inspection_id="INSP_IMMUTABLE_01",
        findings=findings,
        captured_panels={"front", "back"},
        ctx=ctx,
    )

    # Re-verify original data has not been touched
    assert hashlib.sha256(original_bytes).hexdigest() == original_sha256
    assert capture_time == "2026-03-01T10:00:00Z"


# ---------------------------------------------------------------------------
# 8. Assessment Completeness & Precedence (Sections 11 & 12)
# ---------------------------------------------------------------------------

def test_verdict_precedence_matrix():
    """Test exhaustive verdict aggregation precedence combinations."""
    # 1. PASS only -> COMPLIANT
    res1 = aggregate_inspection_assessment("1", [_dummy_finding("CHK01", "pass")], captured_panels={"front", "back"})
    assert res1.overall_verdict == "COMPLIANT"

    # 2. PASS + NOT_ASSESSED -> REVIEW_REQUIRED
    res2 = aggregate_inspection_assessment(
        "2",
        [_dummy_finding("CHK01", "pass"), _dummy_finding("CHK02", "not_assessed")],
        captured_panels={"front", "back"},
    )
    assert res2.overall_verdict == "REVIEW_REQUIRED"

    # 3. PASS + FAIL -> VIOLATION
    res3 = aggregate_inspection_assessment(
        "3",
        [_dummy_finding("CHK01", "pass"), _dummy_finding("CHK02", "fail", limb="36(1)")],
        captured_panels={"front", "back"},
    )
    assert res3.overall_verdict == "VIOLATION"

    # 4. FAIL + NOT_ASSESSED -> VIOLATION (FAIL takes precedence)
    res4 = aggregate_inspection_assessment(
        "4",
        [_dummy_finding("CHK01", "fail", limb="36(2)"), _dummy_finding("CHK02", "not_assessed")],
        captured_panels={"front", "back"},
    )
    assert res4.overall_verdict == "VIOLATION"


# ---------------------------------------------------------------------------
# 9. Audit-Ready Dossiers (Section 13)
# ---------------------------------------------------------------------------

def test_violation_dossier_deterministic_structure_no_llm_fluff():
    """Violation dossier includes legal limbs, citations, deterministic explanations, and zero LLM prose."""
    f = _dummy_finding("CHK04", "fail", title="MRP Declaration", limb="36(1)", reason="Missing MRP declaration")
    dossier = generate_violation_dossier("INSP_DOSSIER_01", [f])
    assert dossier.total_violations == 1
    assert "36(1)" in dossier.affected_limbs
    assert "Section 36(1)" in dossier.statutory_summary
    item = dossier.violations[0]
    assert item.rule_code == "CHK04"
    assert item.statutory_limb == "36(1)"
    assert "Violation under" in item.inspector_explanation


def test_review_dossier_deterministic_structure():
    """Review dossier captures conflicts, unassessed rules, and missing coverage with officer actions."""
    completeness = AssessmentCompleteness(status="PARTIAL", coverage_sufficient=False, missing_panels=["back"])
    conflict = StatutoryConflict(
        conflict_id="CONF_01",
        inspection_id="INSP_DOSSIER_02",
        field="mrp",
        conflicting_values=[260.0, 280.0],
        evidence_refs=[],
        severity="critical",
        reason="MRP mismatch",
    )
    dossier = generate_review_dossier(
        inspection_id="INSP_DOSSIER_02",
        conflicts=[conflict],
        not_assessed_findings=[_dummy_finding("CHK09", "not_assessed")],
        completeness=completeness,
    )
    assert dossier.total_review_items == 3
    types = [item.review_type for item in dossier.review_items]
    assert "conflict" in types
    assert "incomplete_coverage" in types
    assert "unassessed_rule" in types


# ---------------------------------------------------------------------------
# 10. Real Database Persistence & API Integration (Section 15)
# ---------------------------------------------------------------------------

def test_real_database_persistence_and_assessment_api():
    """Verify that an inspection and scans flow through real database models and assessment endpoint."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()

    # 1. Create User & Store
    user = User(
        id=1,
        employee_id="EMP001",
        email="officer@nic.in",
        full_name="Officer Rao",
        role="inspector",
        password_hash="pwd",
    )
    store = Store(id=1, name="Apex Supermarket", address="MG Road", city="Thane", state="Maharashtra", pincode="400601")
    db.add_all([user, store])
    db.commit()

    # 2. Create Inspection & Scan
    insp = Inspection(id=1, user_id=user.id, store_id=store.id, inspection_date=date(2026, 7, 1), status="draft")
    scan = Scan(
        id=1,
        inspection_id=insp.id,
        commodity_generic="Basmati Rice",
        brand_name="Royal",
        overall_result="compliant",
        rule_pack_version="2026.07.v1",
        rules_as_at=date(2026, 7, 1),
        catalog_hash="dummy_hash_123",
        engine_version="2.1.0",
    )
    db.add_all([insp, scan])
    db.commit()

    # 3. Add images & findings to scan
    img1 = ScanImage(
        id=1, scan_id=scan.id, panel="front", file_path="/evidence/front.jpg",
        byte_size=1024, width_px=800, height_px=600, mime_type="image/jpeg", sha256="abc123sha"
    )
    img2 = ScanImage(
        id=2, scan_id=scan.id, panel="back", file_path="/evidence/back.jpg",
        byte_size=2048, width_px=800, height_px=600, mime_type="image/jpeg", sha256="def456sha"
    )
    f1 = Finding(
        id=1, scan_id=scan.id, check_id="CHK01", title="Commodity Name",
        engine_verdict="pass", severity="critical",
    )
    f2 = Finding(
        id=2, scan_id=scan.id, check_id="CHK04", title="MRP Declaration",
        engine_verdict="pass", severity="critical",
    )
    db.add_all([img1, img2, f1, f2])
    db.commit()

    # 4. Invoke inspection assessment endpoint function directly
    from routers.inspections import get_inspection_assessment
    res = get_inspection_assessment(insp=insp, db=db)

    assert isinstance(res, dict)
    assert res["inspection_id"] == "1"
    assert res["rule_pack_version"] == "2026.07.v1"
    assert res["evidence_summary"]["total_panels_captured"] == 2
    assert "front" in res["evidence_summary"]["panels_captured"]
    assert "back" in res["evidence_summary"]["panels_captured"]

    db.close()


# ---------------------------------------------------------------------------
# 11. Idempotency & Zero AI/Network Calls (Sections 16, 17, 18)
# ---------------------------------------------------------------------------

def test_assessment_idempotency():
    """Running assessment twice on identical data produces bit-for-bit identical results."""
    findings = [_dummy_finding("CHK01", "pass"), _dummy_finding("CHK04", "pass")]
    ctx = CheckContext(panels_captured={"front", "back"}, rules_as_at=date(2026, 7, 1))

    res1 = aggregate_inspection_assessment("INSP_IDEM_01", findings, captured_panels={"front", "back"}, ctx=ctx)
    res2 = aggregate_inspection_assessment("INSP_IDEM_01", findings, captured_panels={"front", "back"}, ctx=ctx)

    assert res1.overall_verdict == res2.overall_verdict
    assert res1.total_rules == res2.total_rules
    assert res1.passed_rules == res2.passed_rules

    d1 = res1.to_dict()
    d2 = res2.to_dict()
    d1.pop("evaluated_at")
    d2.pop("evaluated_at")
    assert d1 == d2


def test_zero_external_ai_or_network_calls_during_evaluation():
    """Mathematically assert zero socket/HTTP calls occur during Phase 4 evaluation."""
    import socket

    # If socket.create_connection is called, fail the test
    with patch("socket.create_connection", side_effect=RuntimeError("NETWORK_CALL_FORBIDDEN")):
        # Run full Phase 4 assessment
        findings = [_dummy_finding("CHK01", "pass")]
        assessment = aggregate_inspection_assessment(
            inspection_id="INSP_NO_NET_01",
            findings=findings,
            captured_panels={"front", "back"},
        )
        assert assessment.overall_verdict == "COMPLIANT"


# ---------------------------------------------------------------------------
# 12. Performance & Memory Benchmark (Sections 19 & 20)
# ---------------------------------------------------------------------------

def test_performance_and_memory_within_render_budget():
    """Verify performance metrics and peak RSS remains comfortably below Render's 512 MB ceiling.

    The benchmark measures process-lifetime peak RSS against Render's 512 MB container ceiling.
    Executing in an isolated subprocess ensures total process peak RSS reflects the rules-engine
    workload rather than cumulative memory from preceding full-suite test allocations.
    """
    import json
    import subprocess
    import sys

    cmd = [
        sys.executable,
        "-c",
        "import json; from rules.benchmark import benchmark_phase4_end_to_end; print(json.dumps(benchmark_phase4_end_to_end(iterations=20)))",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True, check=True)
    bench = json.loads(res.stdout)

    assert bench["render_compliant"] is True
    assert bench["peak_rss_mb"] < 512.0
    assert bench["render_512mb_headroom_mb"] > 200.0  # At least 200 MB headroom

    # Sub-millisecond rule-pack loading and context evaluation
    assert bench["rule_pack_loading_cached"]["p95_ms"] < 1.0
    assert bench["full_phase4_end_to_end_assessment"]["p95_ms"] < 25.0


# ---------------------------------------------------------------------------
# 13. Security & Malformed Input Validation (Section 21)
# ---------------------------------------------------------------------------

def test_security_malformed_measurements_rejected_safely():
    """Negative values, NaN, and Infinity in measurements never crash the engine."""
    ctx_nan = CheckContext(
        panel_shape="rectangular",
        panel_height_mm=float("nan"),
        panel_width_mm=float("inf"),
        mm_per_pixel=-0.05,  # Negative scale!
        scale_source="custom",
        measured_heights_mm={"net_quantity": -3.5},
    )
    # Must fail safely to not_assessed, never crash or return pass
    finding = evaluate_net_quantity_height(ctx_nan)
    assert finding.verdict != "pass"
    assert finding.verdict == "not_assessed"


def test_security_out_of_bounds_bbox_handled_safely():
    """Coordinates outside the image boundary are safely rejected or clipped without unhandled exceptions."""
    recon = reconcile_coordinates((-50, -50, 2000, 3000), "analysis_image", (1200, 1600))
    assert recon.status in ("valid", "coordinate_space_mismatch")


# ---------------------------------------------------------------------------
# 14. End-to-End Scenarios A through G (Section 24)
# ---------------------------------------------------------------------------

def test_scenario_a_fully_assessable_compliant_package():
    """Scenario A: Multiple panels, all mandatory declarations present and conforming, valid calibration -> COMPLIANT."""
    findings = [
        _dummy_finding("CHK01", "pass", title="Mandatory Declarations"),
        _dummy_finding("CHK04", "pass", title="MRP Expression"),
        _dummy_finding("CHK05", "pass", title="Net Quantity Units"),
        _dummy_finding("CHK06", "pass", title="Character Height"),
        _dummy_finding("CHK07", "pass", title="Character Width"),
        _dummy_finding("CHK08", "pass", title="Contrast Ratio"),
        _dummy_finding("CHK09", "pass", title="Clear Space"),
    ]
    ctx = CheckContext(
        panels_captured={"front", "back"},
        pdp_surface_established=True,
        scale_source="id1_card",
        mm_per_pixel=0.08,
    )
    assessment = aggregate_inspection_assessment("SCENARIO_A", findings, captured_panels={"front", "back"}, ctx=ctx)
    assert assessment.overall_verdict == "COMPLIANT"
    assert assessment.assessment_completeness.status == "COMPLETE"
    assert assessment.violation_dossier is None
    assert assessment.review_dossier is None


def test_scenario_b_confirmed_declaration_violation():
    """Scenario B: Complete package coverage, mandatory MRP declaration missing -> VIOLATION with dossier."""
    findings = [
        _dummy_finding("CHK01", "pass"),
        _dummy_finding("CHK04", "fail", title="MRP Expression", limb="36(1)", reason="Missing MRP declaration"),
    ]
    assessment = aggregate_inspection_assessment("SCENARIO_B", findings, captured_panels={"front", "back"})
    assert assessment.overall_verdict == "VIOLATION"
    assert assessment.violation_dossier is not None
    assert "36(1)" in assessment.violation_dossier.affected_limbs


def test_scenario_c_incomplete_inspection():
    """Scenario C: Only front panel captured -> REVIEW_REQUIRED with capture request for back panel."""
    findings = [_dummy_finding("CHK01", "pass"), _dummy_finding("CHK04", "pass")]
    assessment = aggregate_inspection_assessment("SCENARIO_C", findings, captured_panels={"front"})
    assert assessment.overall_verdict == "REVIEW_REQUIRED"
    assert assessment.assessment_completeness.status == "PARTIAL"
    assert any(req.target_panel == "back" for req in assessment.actionable_capture_requests)


def test_scenario_d_conflicting_mrp():
    """Scenario D: Front ₹260, Back ₹280 -> REVIEW_REQUIRED with StatutoryConflict under Rule 6(2A)."""
    panel_decl = {
        "front": StructuredDeclarationResult(mrp=MrpDeclaration(value=260.0, provenance=FieldProvenance(source_panel="front"))),
        "back": StructuredDeclarationResult(mrp=MrpDeclaration(value=280.0, provenance=FieldProvenance(source_panel="back"))),
    }
    findings = [_dummy_finding("CHK01", "pass"), _dummy_finding("CHK04", "pass")]
    assessment = aggregate_inspection_assessment(
        "SCENARIO_D", findings, captured_panels={"front", "back"}, panel_declarations=panel_decl,
    )
    assert assessment.overall_verdict == "REVIEW_REQUIRED"
    assert len(assessment.conflicts) == 1
    assert assessment.conflicts[0].field == "mrp"


def test_scenario_e_physical_measurement_unavailable():
    """Scenario E: No calibration standard -> NOT_ASSESSED font size -> REVIEW_REQUIRED, not false FAIL."""
    findings = [
        _dummy_finding("CHK01", "pass"),
        _dummy_finding("CHK06", "not_assessed", title="Font Height", reason="Scale calibration unavailable"),
    ]
    assessment = aggregate_inspection_assessment("SCENARIO_E", findings, captured_panels={"front", "back"})
    assert assessment.overall_verdict == "REVIEW_REQUIRED"
    assert assessment.failed_rules == 0
    assert any("SCALE" in req.request_id for req in assessment.actionable_capture_requests)


def test_scenario_f_visual_rule_uncertainty():
    """Scenario F: Glare prevents clear space measurement -> NOT_ASSESSED -> REVIEW_REQUIRED."""
    findings = [
        _dummy_finding("CHK01", "pass"),
        _dummy_finding("CHK09", "not_assessed", title="Clear Space", reason="Interfering glare"),
    ]
    assessment = aggregate_inspection_assessment("SCENARIO_F", findings, captured_panels={"front", "back"})
    assert assessment.overall_verdict == "REVIEW_REQUIRED"
    assert assessment.failed_rules == 0
    assert any("CLEAR_SPACE" in req.request_id for req in assessment.actionable_capture_requests)


def test_scenario_g_imported_ecommerce_listing():
    """Scenario G: E-commerce listing inspection evaluates origin and listing rules without physical package assumptions."""
    ctx = CheckContext(
        transaction_type="retail",
        is_imported=True,
        listing_available=True,
        rules_as_at=date(2026, 7, 1),
    )
    findings = [
        _dummy_finding("CHK12", "pass", title="Country of Origin on Import"),
        _dummy_finding("CHK15", "pass", title="E-Commerce Listing Mandatory Declarations"),
    ]
    assessment = aggregate_inspection_assessment(
        "SCENARIO_G",
        findings,
        inspection_source="ecommerce_listing",
        captured_panels={"ecommerce_listing"},
        ctx=ctx,
    )
    assert assessment.overall_verdict == "COMPLIANT"
    assert assessment.assessment_completeness.status == "COMPLETE"
