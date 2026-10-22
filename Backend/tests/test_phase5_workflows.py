"""tests/test_phase5_workflows.py â€” Phase 5 regression: Review Queue,
Smart Recapture, and Enforcement-Support Workflows.

Tests the three Phase 5 workstreams:
- 5A: Officer Review Queue + Adjudication
- 5B: Smart Recapture Workflow
- 5C: Officer Reports + Enforcement-Support Workflow

These tests exercise the pure-logic helpers and data-flow contracts without
requiring a running server or cloud OCR.
"""
from __future__ import annotations

import json
import pytest
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

from fastapi import HTTPException


# =========================================================================
# PHASE 5A â€” Review Queue + Adjudication
# =========================================================================

class TestReviewQueueHelpers:
    """Test the review reason determination and item construction."""

    def _make_scan(self, overall_result="not_assessed", checks_total=18,
                   checks_assessed=10, **kwargs):
        scan = MagicMock()
        scan.id = kwargs.get("id", 1)
        scan.inspection_id = kwargs.get("inspection_id", 1)
        scan.overall_result = overall_result
        scan.checks_total = checks_total
        scan.checks_assessed = checks_assessed
        scan.commodity_generic = kwargs.get("commodity_generic", "Test Product")
        scan.brand_name = kwargs.get("brand_name", "TestBrand")
        scan.batch_number = kwargs.get("batch_number", "B001")
        scan.commodity_category = kwargs.get("commodity_category", "food")
        scan.created_at = datetime.now(timezone.utc)
        scan.rules_as_at = date(2026, 9, 21)
        scan.engine_version = "2.0"
        scan.rule_pack_version = "2026.09.v1"
        scan.duplicate_of = None
        scan.images = []
        scan.findings = []
        return scan

    def _make_finding(self, check_id="CHK01", verdict="pass", severity="major",
                      confidence=0.95, human_verdict=None, **kwargs):
        f = MagicMock()
        f.id = kwargs.get("id", 1)
        f.scan_id = kwargs.get("scan_id", 1)
        f.check_id = check_id
        f.title = kwargs.get("title", f"Check {check_id}")
        f.engine_verdict = verdict
        f.human_verdict = human_verdict
        f.effective_verdict = human_verdict or verdict
        f.severity = severity
        f.confidence = confidence
        f.reason = kwargs.get("reason", None)
        f.observed = kwargs.get("observed", None)
        f.required = kwargs.get("required", None)
        f.citation = kwargs.get("citation", None)
        f.ledger_ref = kwargs.get("ledger_ref", None)
        f.limb = kwargs.get("limb", None)
        f.override_reason = None
        f.overridden_at = None
        f.overridden_by = None
        return f

    def _make_inspection(self, **kwargs):
        insp = MagicMock()
        insp.id = kwargs.get("id", 1)
        insp.store_id = kwargs.get("store_id", 1)
        insp.user_id = kwargs.get("user_id", 1)
        insp.inspection_date = kwargs.get("inspection_date", date.today())
        insp.status = kwargs.get("status", "draft")
        insp.transaction_type = kwargs.get("transaction_type", "retail_sale")
        insp.in_scope = kwargs.get("in_scope", True)
        insp.edited_offline = kwargs.get("edited_offline", False)
        insp.clock_skew_seconds = kwargs.get("clock_skew_seconds", 0)
        insp.geofence_status = kwargs.get("geofence_status", "inside")
        insp.signature_status = kwargs.get("signature_status", None)
        insp.notes = kwargs.get("notes", None)
        insp.store = MagicMock()
        insp.store.name = "Test Store"
        insp.store.city = "Delhi"
        insp.store.district = "Central Delhi"
        insp.inspector = MagicMock()
        insp.inspector.full_name = "Inspector Test"
        return insp

    def test_review_reason_not_assessed(self):
        """Not-assessed scan generates review reason."""
        from routers.review import _review_reason
        scan = self._make_scan(overall_result="not_assessed")
        insp = self._make_inspection()
        findings = [
            self._make_finding("CHK01", "not_assessed", reason="No evidence"),
            self._make_finding("CHK02", "pass"),
        ]
        reasons = _review_reason(scan, findings, insp)
        assert any(r["type"] == "not_assessed" for r in reasons)

    def test_review_reason_low_confidence(self):
        """Low-confidence findings generate review reason."""
        from routers.review import _review_reason
        scan = self._make_scan(overall_result="compliant")
        insp = self._make_inspection()
        findings = [
            self._make_finding("CHK01", "pass", confidence=0.40),
        ]
        reasons = _review_reason(scan, findings, insp)
        assert any(r["type"] == "low_confidence" for r in reasons)

    def test_review_reason_null_confidence(self):
        """NULL confidence deterministic findings do NOT generate low_confidence review reason."""
        from routers.review import _review_reason
        scan = self._make_scan(overall_result="compliant")
        insp = self._make_inspection()
        findings = [
            self._make_finding("CHK01", "pass", confidence=None),
        ]
        reasons = _review_reason(scan, findings, insp)
        assert not any(r["type"] == "low_confidence" for r in reasons)

    def test_review_reason_offline_edit(self):
        """Offline-edited inspection generates review reason."""
        from routers.review import _review_reason
        scan = self._make_scan(overall_result="compliant")
        insp = self._make_inspection(edited_offline=True, clock_skew_seconds=-600)
        findings = [self._make_finding("CHK01", "pass")]
        reasons = _review_reason(scan, findings, insp)
        assert any(r["type"] == "offline_edit" for r in reasons)

    def test_review_reason_unconfirmed_violation(self):
        """Unconfirmed violation generates review reason."""
        from routers.review import _review_reason
        scan = self._make_scan(overall_result="violation")
        insp = self._make_inspection()
        findings = [
            self._make_finding("CHK05", "fail", severity="critical", human_verdict=None),
        ]
        reasons = _review_reason(scan, findings, insp)
        assert any(r["type"] == "violation" for r in reasons)

    def test_build_review_item_priority_high(self):
        """Critical severity â†’ high priority."""
        from routers.review import _build_review_item
        insp = self._make_inspection()
        scan = self._make_scan(overall_result="violation")
        findings = [
            self._make_finding("CHK05", "fail", severity="critical", human_verdict=None),
        ]
        item = _build_review_item(insp, scan, findings)
        assert item["priority"] == "high"
        assert item["review_status"] == "OPEN"
        assert item["inspection_id"] == insp.id
        assert len(item["review_reasons"]) > 0

    def test_build_review_item_priority_medium(self):
        """Major severity without critical â†’ medium priority."""
        from routers.review import _build_review_item
        insp = self._make_inspection()
        scan = self._make_scan(overall_result="not_assessed")
        findings = [
            self._make_finding("CHK01", "not_assessed", severity="major",
                               reason="No evidence found"),
        ]
        item = _build_review_item(insp, scan, findings)
        assert item["priority"] == "medium"

    def test_build_review_item_has_findings(self):
        """Review item includes finding summaries with needs_review flag."""
        from routers.review import _build_review_item
        insp = self._make_inspection()
        scan = self._make_scan(overall_result="not_assessed")
        findings = [
            self._make_finding("CHK01", "not_assessed", reason="missing"),
            self._make_finding("CHK02", "pass", confidence=0.95),
        ]
        item = _build_review_item(insp, scan, findings)
        assert len(item["findings"]) == 2
        f1 = next(f for f in item["findings"] if f["check_id"] == "CHK01")
        assert f1["needs_review"] is True

    def test_review_item_store_and_inspector(self):
        """Review item includes store and inspector metadata."""
        from routers.review import _build_review_item
        insp = self._make_inspection()
        scan = self._make_scan()
        item = _build_review_item(insp, scan, [])
        assert item["store_name"] == "Test Store"
        assert item["inspector_name"] == "Inspector Test"


# =========================================================================
# PHASE 5B â€” Smart Recapture Workflow
# =========================================================================

class TestSmartRecapture:
    """Test capture task generation and panel-to-check mapping."""

    def _make_scan(self, **kwargs):
        scan = MagicMock()
        scan.id = kwargs.get("id", 1)
        scan.inspection_id = kwargs.get("inspection_id", 1)
        scan.overall_result = kwargs.get("overall_result", "not_assessed")
        scan.checks_total = kwargs.get("checks_total", 18)
        scan.checks_assessed = kwargs.get("checks_assessed", 10)
        scan.commodity_generic = "Test Product"
        scan.brand_name = "TestBrand"
        scan.batch_number = "B001"
        scan.commodity_category = "food"
        scan.duplicate_of = None
        return scan

    def _make_finding(self, check_id, verdict, **kwargs):
        f = MagicMock()
        f.id = kwargs.get("id", 1)
        f.scan_id = 1
        f.check_id = check_id
        f.title = kwargs.get("title", f"Check {check_id}")
        f.engine_verdict = verdict
        f.human_verdict = kwargs.get("human_verdict", None)
        f.severity = kwargs.get("severity", "major")
        f.confidence = kwargs.get("confidence", None)
        f.reason = kwargs.get("reason", None)
        f.required = kwargs.get("required", None)
        return f

    def _make_image(self, panel, **kwargs):
        img = MagicMock()
        img.id = kwargs.get("id", 1)
        img.panel = panel
        img.sha256 = "abc123"
        img.blur_variance = kwargs.get("blur_variance", 500.0)
        img.residual_tilt_deg = kwargs.get("residual_tilt_deg", 1.0)
        img.rectified = True
        return img

    def test_missing_panel_generates_task(self):
        """Missing mandatory panel generates a capture task."""
        from routers.recapture import _generate_capture_tasks
        scan = self._make_scan()
        findings = []
        images = [self._make_image("front", id=1)]  # only front, back missing
        tasks = _generate_capture_tasks(1, scan, findings, images)
        assert any(t["target_panel"] == "back" and "missing" in t["request_id"]
                    for t in tasks)

    def test_both_panels_no_missing_task(self):
        """No missing panel task when both front and back are captured."""
        from routers.recapture import _generate_capture_tasks
        scan = self._make_scan()
        findings = []
        images = [
            self._make_image("front", id=1),
            self._make_image("back", id=2),
        ]
        tasks = _generate_capture_tasks(1, scan, findings, images)
        assert not any("missing" in t["request_id"] for t in tasks)

    def test_not_assessed_generates_quality_task(self):
        """Not-assessed finding on a captured panel generates quality recapture."""
        from routers.recapture import _generate_capture_tasks
        scan = self._make_scan()
        findings = [
            self._make_finding("CHK01", "not_assessed",
                               reason="Text not readable", id=1),
        ]
        images = [
            self._make_image("front", id=1),
            self._make_image("back", id=2),
        ]
        tasks = _generate_capture_tasks(1, scan, findings, images)
        quality_tasks = [t for t in tasks if "quality" in t["request_id"]]
        assert len(quality_tasks) >= 1

    def test_low_confidence_generates_recapture_task(self):
        """Very low confidence finding generates a recapture task."""
        from routers.recapture import _generate_capture_tasks
        scan = self._make_scan()
        findings = [
            self._make_finding("CHK02", "pass", confidence=0.30, id=1),
        ]
        images = [
            self._make_image("front", id=1),
            self._make_image("back", id=2),
        ]
        tasks = _generate_capture_tasks(1, scan, findings, images)
        lowconf = [t for t in tasks if "lowconf" in t["request_id"]]
        assert len(lowconf) >= 1

    def test_tasks_sorted_by_priority(self):
        """Tasks are sorted: high â†’ medium â†’ low."""
        from routers.recapture import _generate_capture_tasks
        scan = self._make_scan()
        findings = [
            self._make_finding("CHK01", "not_assessed",
                               severity="critical", reason="missing", id=1),
            self._make_finding("CHK09", "not_assessed",
                               severity="minor", reason="missing", id=2),
        ]
        images = [self._make_image("front", id=1)]  # back missing â†’ high priority
        tasks = _generate_capture_tasks(1, scan, findings, images)
        if len(tasks) >= 2:
            priorities = [t["priority"] for t in tasks]
            priority_order = {"high": 0, "medium": 1, "low": 2}
            assert all(
                priority_order.get(priorities[i], 3) <= priority_order.get(priorities[i + 1], 3)
                for i in range(len(priorities) - 1)
            )

    def test_tasks_deduplicated(self):
        """Duplicate request IDs are removed."""
        from routers.recapture import _generate_capture_tasks
        scan = self._make_scan()
        findings = []
        images = []  # both panels missing
        tasks = _generate_capture_tasks(1, scan, findings, images)
        ids = [t["request_id"] for t in tasks]
        assert len(ids) == len(set(ids))

    def test_check_to_panel_mapping(self):
        """Check-to-panel mapping returns expected panels."""
        from routers.recapture import _check_to_panel
        assert _check_to_panel("CHK01") == "front"
        assert _check_to_panel("CHK12") == "back"
        assert _check_to_panel("CHK11") == "mrp"
        assert _check_to_panel("CHK99") == "front"  # default


# =========================================================================
# PHASE 5C â€” Enforcement-Support Workflow
# =========================================================================

class TestEnforcementSupport:
    """Test enforcement guidance, Section 36 analysis, and dossier generation."""

    def _make_finding(self, check_id, verdict, **kwargs):
        f = MagicMock()
        f.id = kwargs.get("id", 1)
        f.check_id = check_id
        f.title = kwargs.get("title", f"Check {check_id}")
        f.engine_verdict = verdict
        f.human_verdict = kwargs.get("human_verdict", None)
        f.effective_verdict = kwargs.get("human_verdict", None) or verdict
        f.severity = kwargs.get("severity", "major")
        f.limb = kwargs.get("limb", None)
        f.reason = kwargs.get("reason", None)
        f.observed = kwargs.get("observed", None)
        f.required = kwargs.get("required", None)
        f.citation = kwargs.get("citation", None)
        f.ledger_ref = kwargs.get("ledger_ref", None)
        f.confidence = kwargs.get("confidence", 0.95)
        f.override_reason = None
        return f

    def test_s36_no_violations(self):
        """No violations â†’ Section 36 not applicable."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK01", "pass"),
            self._make_finding("CHK02", "pass"),
        ]
        guidance = _section_36_guidance(findings)
        assert guidance["applicable"] is False
        assert guidance["recommended_action"] is None

    def test_s36_critical_violation(self):
        """Critical violation â†’ prosecute_and_seize recommendation."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK05", "fail", severity="critical",
                               limb="36(1)"),
        ]
        guidance = _section_36_guidance(findings)
        assert guidance["applicable"] is True
        assert guidance["recommended_action"] == "prosecute_and_seize"
        assert guidance["total_violations"] == 1
        assert any(l["limb"] == "36(1)" for l in guidance["limbs"])

    def test_s36_major_violation(self):
        """Major violation â†’ compound_or_prosecute recommendation."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK04", "fail", severity="major",
                               limb="36(1)"),
        ]
        guidance = _section_36_guidance(findings)
        assert guidance["applicable"] is True
        assert guidance["recommended_action"] == "compound_or_prosecute"

    def test_s36_minor_violation(self):
        """Minor violation â†’ compound recommendation."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK09", "fail", severity="minor",
                               limb="36(1)"),
        ]
        guidance = _section_36_guidance(findings)
        assert guidance["applicable"] is True
        assert guidance["recommended_action"] == "compound"

    def test_s36_both_limbs(self):
        """Violations engaging both limbs are reported."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK01", "fail", severity="major",
                               limb="36(1)", id=1),
            self._make_finding("CHK02", "fail", severity="critical",
                               limb="36(2)", id=2),
        ]
        guidance = _section_36_guidance(findings)
        assert guidance["applicable"] is True
        limb_ids = {l["limb"] for l in guidance["limbs"]}
        assert "36(1)" in limb_ids
        assert "36(2)" in limb_ids
        assert guidance["total_violations"] == 2

    def test_s36_has_legal_disclaimer(self):
        """Section 36 guidance includes legal disclaimer."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK05", "fail", severity="critical",
                               limb="36(1)"),
        ]
        guidance = _section_36_guidance(findings)
        assert "legal_disclaimer" in guidance
        assert "advisory" in guidance["legal_disclaimer"].lower()

    def test_s36_unassigned_limb(self):
        """Violations without limb assigned are reported under 'unassigned'."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK09", "fail", severity="minor",
                               limb=None),
        ]
        guidance = _section_36_guidance(findings)
        assert guidance["applicable"] is True
        assert any(l["limb"] == "unassigned" for l in guidance["limbs"])

    def test_s36_human_verdict_pass_not_violation(self):
        """Finding with engine_verdict=fail but human_verdict=pass is not a violation."""
        from routers.enforcement import _section_36_guidance
        findings = [
            self._make_finding("CHK05", "fail", severity="critical",
                               human_verdict="pass", limb="36(1)"),
        ]
        guidance = _section_36_guidance(findings)
        # human_verdict overrides â†’ effective is pass â†’ no violation
        assert guidance["applicable"] is False

    def test_violation_dossier_data(self):
        """Violation dossier data includes all required fields."""
        from routers.enforcement import _violation_dossier_data
        insp = MagicMock()
        insp.id = 1
        scan = MagicMock()
        scan.id = 10
        scan.commodity_generic = "Rice"
        scan.brand_name = "SuperRice"
        scan.batch_number = "B001"
        scan.commodity_category = "food"
        scan.overall_result = "violation"
        scan.rules_as_at = date(2026, 9, 21)
        scan.engine_version = "2.0"
        scan.rule_pack_version = "2026.09.v1"
        findings = [
            self._make_finding("CHK02", "fail", severity="critical",
                               limb="36(2)", observed="450g",
                               required="500g", citation="Rule 6(1)(b)"),
        ]
        dossier = _violation_dossier_data(insp, scan, findings)
        assert dossier["scan_id"] == 10
        assert dossier["total_violations"] == 1
        assert len(dossier["violation_items"]) == 1
        item = dossier["violation_items"][0]
        assert item["check_id"] == "CHK02"
        assert item["citation"] == "Rule 6(1)(b)"

    def test_legal_reference_lookup(self):
        """Legal reference returns correct data for known check IDs."""
        from routers.enforcement import get_legal_reference
        # Test with a mock user
        mock_user = MagicMock()
        ref = get_legal_reference("CHK01", user=mock_user)
        assert ref["check_id"] == "CHK01"
        assert ref["rule"] == "Rule 6(1)(a)"
        assert "legal_disclaimer" in ref

    def test_legal_reference_unknown_check(self):
        """Unknown check ID raises 404."""
        from routers.enforcement import get_legal_reference
        mock_user = MagicMock()
        with pytest.raises(HTTPException) as exc_info:
            get_legal_reference("CHK_UNKNOWN", user=mock_user)
        assert exc_info.value.status_code == 404


# =========================================================================
# CROSS-PHASE INTEGRATION TESTS
# =========================================================================

class TestPhase5Integration:
    """Integration tests verifying Phase 5 components work together."""

    def test_review_to_enforcement_flow(self):
        """A review item with violations produces valid enforcement guidance."""
        from routers.enforcement import _section_36_guidance
        from routers.review import _review_reason

        scan = MagicMock()
        scan.overall_result = "violation"
        scan.checks_total = 18
        scan.checks_assessed = 18

        insp = MagicMock()
        insp.edited_offline = False

        f1 = MagicMock()
        f1.id = 1
        f1.check_id = "CHK02"
        f1.engine_verdict = "fail"
        f1.human_verdict = None
        f1.effective_verdict = "fail"
        f1.severity = "critical"
        f1.confidence = 0.95
        f1.limb = "36(2)"

        # Review layer detects the unconfirmed violation
        reasons = _review_reason(scan, [f1], insp)
        assert any(r["type"] == "violation" for r in reasons)

        # Enforcement layer generates guidance
        guidance = _section_36_guidance([f1])
        assert guidance["applicable"] is True
        assert guidance["total_violations"] == 1

    def test_recapture_to_review_flow(self):
        """A capture task for missing panel is generated correctly."""
        from routers.recapture import _generate_capture_tasks

        scan = MagicMock()
        scan.id = 1
        scan.inspection_id = 1
        scan.overall_result = "not_assessed"
        scan.checks_total = 18
        scan.checks_assessed = 8

        findings = [
            MagicMock(
                id=1, check_id="CHK08", engine_verdict="not_assessed",
                human_verdict=None, severity="major",
                confidence=None, reason="Back panel not captured",
                title="Best Before Date", required="Best before date visible",
            ),
        ]
        images = [MagicMock(id=1, panel="front")]

        tasks = _generate_capture_tasks(1, scan, findings, images)
        # Should have task for missing back panel + quality recapture for CHK08
        panel_tasks = [t for t in tasks if t["target_panel"] == "back"]
        assert len(panel_tasks) >= 1

    def test_all_modules_importable(self):
        """All Phase 5 modules import without error."""
        import routers.review
        import routers.recapture
        import routers.enforcement

        assert hasattr(routers.review, "router")
        assert hasattr(routers.recapture, "router")
        assert hasattr(routers.enforcement, "router")

    def test_review_status_model(self):
        """Review status model has exactly three states."""
        from routers.review import REVIEW_STATUSES
        assert REVIEW_STATUSES == ("OPEN", "IN_REVIEW", "RESOLVED")

    def test_enforcement_disclaimer_present(self):
        """Enforcement endpoints include legal disclaimers."""
        from routers.enforcement import _section_36_guidance
        # Even with violations, disclaimer is present
        f = MagicMock()
        f.engine_verdict = "fail"
        f.human_verdict = None
        f.effective_verdict = "fail"
        f.severity = "critical"
        f.limb = "36(1)"
        guidance = _section_36_guidance([f])
        assert "advisory" in guidance["legal_disclaimer"].lower()

    def test_capture_task_has_all_fields(self):
        """Generated capture task has all required fields."""
        from routers.recapture import _generate_capture_tasks
        scan = MagicMock()
        scan.id = 1
        scan.inspection_id = 1
        tasks = _generate_capture_tasks(1, scan, [], [])
        if tasks:
            required_fields = {
                "request_id", "inspection_id", "scan_id",
                "target_panel", "reason", "expected_evidence",
                "priority", "suggested_action", "status",
            }
            assert required_fields.issubset(set(tasks[0].keys()))

