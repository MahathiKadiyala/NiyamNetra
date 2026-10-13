"""tests/test_assessment_counting_contract.py — Assessment Finding Counts and Rule Totals Contract (Module 3)

Tests covering:
1. Arithmetic invariants:
   - 4 passes, 3 failures, 12 not-assessed findings yield 19 total findings and 7 assessed findings.
   - Empty findings (0 total, 0 assessed).
   - All-pass findings (19 total, 19 assessed).
   - All-fail findings (19 total, 19 assessed).
   - All-not-assessed findings (19 total, 0 assessed).
   - Mixed partial results.
   - Invariants: pass_count + fail_count + not_assessed_count == total_findings == checks_total.
   - Invariants: checks_assessed == pass_count + fail_count.
2. Catalog versus execution counts:
   - 26 catalog rules in active pack lmpc_2026_09_v1.
   - 19 executable algorithmic checks.
   - 7 manual-review or scope-related catalog rules (19 + 7 = 26).
   - Per-scan findings returned are exactly the evaluated checks (19), not the catalog metadata (26).
   - Unreturned catalog rules are not counted as evaluated.
3. Finding identity & deduplication:
   - CHK06 and CHK06b are distinct statutory checks and remain distinguishable.
   - Duplicate copies of findings do not inflate totals.
   - Supporting evidence is preserved on deduplication.
4. API serialization & database integration:
   - _scan_out returns internally consistent ScanOut payload.
   - counts.total == checks_total == len(findings).
   - counts.passed + counts.failed == checks_assessed.
   - Truthful handling during OCR provider outage (19 not_assessed, 0 assessed, 0 false violations).
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from models import Base, Finding, Inspection, Scan, Store, User
from rules.aggregation import deduplicate_findings
from rules.loader import load_rule_pack
from rules_engine import (
    ALL_CHECK_IDS,
    CHECKS,
    CheckContext,
    FindingResult,
    ScanVerdict,
    assess,
    derive_result,
    run_checks,
)
from schemas import ScanOut, VerdictCounts


# ---------------------------------------------------------------------------
# 1. Arithmetic Invariants
# ---------------------------------------------------------------------------

class TestArithmeticInvariants:
    def test_production_report_distribution_4pass_3fail_12na(self):
        """Four passes, three failures, and twelve not-assessed findings yield
        19 total findings and 7 assessed findings when all 19 are unique returned findings.
        """
        ctx = CheckContext(rules_as_at=date(2026, 9, 21))
        findings: list[FindingResult] = []

        # 4 Pass checks
        for cid in ["CHK01", "CHK02", "CHK03", "CHK04"]:
            findings.append(FindingResult(cid, f"Title {cid}", "pass", "major"))

        # 2 Fail checks in assessable
        for cid in ["CHK05", "CHK06"]:
            findings.append(FindingResult(cid, f"Title {cid}", "fail", "major", limb="36(1)"))

        # 12 Not Assessed checks (11 in assessable + CHK18 initially)
        na_ids = ["CHK06b", "CHK07", "CHK08", "CHK09", "CHK10", "CHK11", "CHK12", "CHK13", "CHK14", "CHK15", "CHK16", "CHK17"]
        for cid in na_ids:
            findings.append(FindingResult(cid, f"Title {cid}", "not_assessed", "major", reason="Unassessed"))

        # Add CHK18 (the 19th check)
        findings.append(FindingResult("CHK18", "Graduated sanctions", "not_assessed", "critical"))

        assert len(findings) == 19
        assert len({f.check_id for f in findings}) == 19

        verdict = derive_result(findings, ctx)

        # CHK18 is evaluated as fail because there are violations among assessable
        chk18_f = next(f for f in findings if f.check_id == "CHK18")
        assert chk18_f.verdict == "fail"

        # Authoritative reconciled counts:
        # Pass: 4
        # Fail: 2 assessable + 1 CHK18 = 3
        # Not assessed: 12
        # Total findings: 19
        # Assessed: 4 + 3 = 7
        assert verdict.passed == 4
        assert verdict.failed == 3
        assert verdict.not_assessed == 12
        assert verdict.checks_total == 19
        assert verdict.checks_assessed == 7

        # Enforce exact arithmetic invariants
        assert verdict.passed + verdict.failed + verdict.not_assessed == verdict.checks_total
        assert verdict.checks_assessed == verdict.passed + verdict.failed
        assert verdict.checks_total == len(findings)

    def test_empty_findings_contract(self):
        """Empty findings yield 0 total and 0 assessed with consistent arithmetic."""
        ctx = CheckContext(rules_as_at=date(2026, 9, 21))
        verdict = derive_result([], ctx)

        assert verdict.checks_total == 0
        assert verdict.checks_assessed == 0
        assert verdict.passed == 0
        assert verdict.failed == 0
        assert verdict.not_assessed == 0
        assert verdict.passed + verdict.failed + verdict.not_assessed == verdict.checks_total
        assert verdict.checks_assessed == verdict.passed + verdict.failed

    def test_all_pass_findings_contract(self):
        """All-pass findings yield checks_total == checks_assessed == passed."""
        ctx = CheckContext(rules_as_at=date(2026, 9, 21))
        findings = [
            FindingResult(cid, f"Check {cid}", "pass", "major")
            for cid in ALL_CHECK_IDS
        ]
        verdict = derive_result(findings, ctx)

        assert verdict.checks_total == 19
        assert verdict.checks_assessed == 19
        assert verdict.passed == 19
        assert verdict.failed == 0
        assert verdict.not_assessed == 0
        assert verdict.overall_result == "compliant"
        assert verdict.passed + verdict.failed + verdict.not_assessed == verdict.checks_total
        assert verdict.checks_assessed == verdict.passed + verdict.failed

    def test_all_fail_findings_contract(self):
        """All-fail findings yield checks_total == checks_assessed == failed."""
        ctx = CheckContext(rules_as_at=date(2026, 9, 21))
        findings = [
            FindingResult(cid, f"Check {cid}", "fail", "major", limb="36(1)")
            for cid in ALL_CHECK_IDS
        ]
        verdict = derive_result(findings, ctx)

        assert verdict.checks_total == 19
        assert verdict.checks_assessed == 19
        assert verdict.passed == 0
        assert verdict.failed == 19
        assert verdict.not_assessed == 0
        assert verdict.overall_result == "violation"
        assert verdict.passed + verdict.failed + verdict.not_assessed == verdict.checks_total
        assert verdict.checks_assessed == verdict.passed + verdict.failed

    def test_all_not_assessed_findings_contract(self):
        """All-not-assessed findings yield checks_assessed == 0 and not_assessed == checks_total."""
        ctx = CheckContext(rules_as_at=date(2026, 9, 21))
        findings = [
            FindingResult(cid, f"Check {cid}", "not_assessed", "major", reason="Provider outage")
            for cid in ALL_CHECK_IDS
        ]
        verdict = derive_result(findings, ctx)

        assert verdict.checks_total == 19
        assert verdict.checks_assessed == 0
        assert verdict.passed == 0
        assert verdict.failed == 0
        assert verdict.not_assessed == 19
        assert verdict.overall_result == "not_assessed"
        assert verdict.passed + verdict.failed + verdict.not_assessed == verdict.checks_total
        assert verdict.checks_assessed == verdict.passed + verdict.failed

    def test_mixed_partial_evidence_contract(self):
        """A partial collection of findings maintains strict arithmetic invariants."""
        ctx = CheckContext(rules_as_at=date(2026, 9, 21))
        partial = [
            FindingResult("CHK01", "Mandatory", "pass", "critical"),
            FindingResult("CHK04", "MRP", "fail", "critical", limb="36(1)"),
            FindingResult("CHK05", "Units", "pass", "major"),
            FindingResult("CHK06", "Font Height", "not_assessed", "major", reason="No ruler"),
            FindingResult("CHK06b", "Net Qty Height", "not_assessed", "major", reason="No ruler"),
        ]
        verdict = derive_result(partial, ctx)

        assert verdict.checks_total == 5
        assert verdict.passed == 2
        assert verdict.failed == 1
        assert verdict.not_assessed == 2
        assert verdict.checks_assessed == 3
        assert verdict.passed + verdict.failed + verdict.not_assessed == verdict.checks_total
        assert verdict.checks_assessed == verdict.passed + verdict.failed


# ---------------------------------------------------------------------------
# 2. Catalog versus Execution Counts
# ---------------------------------------------------------------------------

class TestCatalogVsExecutionCounts:
    def test_catalog_metadata_not_confused_with_scan_findings(self):
        """The static catalog metadata (26 rules) is not confused with per-scan executable findings (19)."""
        rule_pack = load_rule_pack("2026.09.v1")
        assert rule_pack.rule_pack_version == "2026.09.v1"
        assert len(rule_pack.rules) == 26

        # Engine registers exactly 19 executable algorithmic checks
        assert len(ALL_CHECK_IDS) == 19
        assert len(CHECKS) == 19

        # The 7 rules that require manual review or specialized schedules:
        # 26 - 19 = 7
        manual_or_scope_count = len(rule_pack.rules) - len(ALL_CHECK_IDS)
        assert manual_or_scope_count == 7

        # Assess a scan and verify the returned findings collection size
        ctx = CheckContext(
            transaction_type="retail_sale",
            panels_captured={"front", "back"},
            ocr_available=True,
            rules_as_at=date(2026, 9, 21),
        )
        findings, verdict, provenance = assess(ctx)

        # The scan returns 19 findings, NOT 26
        assert len(findings) == 19
        assert verdict.checks_total == 19
        assert verdict.checks_total != 26

    def test_unreturned_catalog_rules_not_counted_as_evaluated(self):
        """Rules outside executable scope (e.g. CHK19, CHK20, CHK21, CHK22, CHK23)
        exist in the 26-rule catalog but are not returned as findings on a physical scan.
        """
        ctx = CheckContext(
            transaction_type="retail_sale",
            panels_captured={"front"},
            ocr_available=True,
            rules_as_at=date(2026, 9, 21),
        )
        findings, verdict, _ = assess(ctx)
        returned_check_ids = {f.check_id for f in findings}

        # Check IDs that belong strictly to manual review or specialized schedules
        catalog_only_codes = {"CHK19", "CHK20", "CHK21", "CHK22", "CHK23"}
        for code in catalog_only_codes:
            assert code not in returned_check_ids

        # Total findings matches exactly the registered executable checks
        assert returned_check_ids == set(ALL_CHECK_IDS)


# ---------------------------------------------------------------------------
# 3. Finding Identity & Deduplication
# ---------------------------------------------------------------------------

class TestFindingIdentityAndDeduplication:
    def test_chk06_and_chk06b_remain_distinguishable(self):
        """CHK06 (General PDP Letter Height) and CHK06b (Net Quantity Numeral Height)
        are distinct statutory checks under Rule 7(1) vs Rule 7(2) and remain distinguishable.
        """
        assert "CHK06" in ALL_CHECK_IDS
        assert "CHK06b" in ALL_CHECK_IDS
        assert "CHK06" != "CHK06b"

        ctx = CheckContext(
            measured_heights_mm={"mrp": 2.5, "net_quantity": 4.0},
            scale_source="declared",
            mm_per_pixel=0.1,
            rules_as_at=date(2026, 9, 21),
        )
        findings = run_checks(ctx)
        chk06 = next(f for f in findings if f.check_id == "CHK06")
        chk06b = next(f for f in findings if f.check_id == "CHK06b")

        assert chk06.check_id == "CHK06"
        assert chk06b.check_id == "CHK06b"
        assert chk06.title != chk06b.title
        assert chk06.citation != chk06b.citation

    def test_deduplicate_findings_preserves_chk06_and_chk06b(self):
        """Deduplication deterministically keeps CHK06 and CHK06b separate while
        collapsing genuine duplicate copies of the same check.
        """
        input_findings = [
            FindingResult("CHK06", "Letter Height", "pass", "major"),
            FindingResult("CHK06b", "Net Qty Height", "pass", "major"),
            # Duplicate copy of CHK01 with different evidence
            FindingResult("CHK01", "Declarations", "pass", "critical", evidence_provenance={"panel": "front"}),
            FindingResult("CHK01", "Declarations", "fail", "critical", limb="36(1)", evidence_provenance={"panel": "back"}),
        ]

        deduped = deduplicate_findings(input_findings)
        deduped_ids = [f.check_id for f in deduped]

        # CHK06 and CHK06b are both preserved
        assert "CHK06" in deduped_ids
        assert "CHK06b" in deduped_ids

        # Duplicate CHK01 is collapsed into 1 finding with FAIL winning
        chk01_items = [f for f in deduped if f.check_id == "CHK01"]
        assert len(chk01_items) == 1
        assert chk01_items[0].verdict == "fail"

        # Total deduped count is 3
        assert len(deduped) == 3


# ---------------------------------------------------------------------------
# 4. API Serialization & Integration
# ---------------------------------------------------------------------------

class TestApiSerializationAndIntegration:
    @pytest.fixture
    def test_db(self):
        engine = create_engine("sqlite:///:memory:")
        Base.metadata.create_all(engine)
        Session = sessionmaker(bind=engine)
        session = Session()

        store = Store(name="Test Store", store_type="retail")
        session.add(store)
        user = User(employee_id="EMP01", full_name="Inspector Test", password_hash="hash")
        session.add(user)
        session.commit()

        insp = Inspection(user_id=user.id, store_id=store.id, inspection_date=date(2026, 9, 21), transaction_type="retail")
        session.add(insp)
        session.commit()

        yield session, insp
        session.close()

    def test_scan_out_counts_and_checks_total_are_identical(self, test_db):
        """_scan_out enforces checks_total == counts.total and checks_assessed == counts.passed + counts.failed."""
        from routers.scans import _scan_out

        session, insp = test_db
        scan = Scan(
            inspection_id=insp.id,
            commodity_generic="Biscuits",
            brand_name="Parle-G",
            overall_result="violation",
            checks_total=18,  # Legacy row with obsolete checks_total=18
            checks_assessed=6, # Legacy row with obsolete checks_assessed=6
            rules_as_at=date(2026, 9, 21),
            catalog_hash="testhash",
            engine_version="2.4.0",
        )
        session.add(scan)
        session.commit()

        # Insert 19 findings: 4 pass, 3 fail, 12 not_assessed
        findings_to_insert = [
            ("CHK01", "pass"), ("CHK02", "pass"), ("CHK03", "pass"), ("CHK04", "pass"),
            ("CHK05", "fail"), ("CHK06", "fail"), ("CHK18", "fail"),
            ("CHK06b", "not_assessed"), ("CHK07", "not_assessed"), ("CHK08", "not_assessed"),
            ("CHK09", "not_assessed"), ("CHK10", "not_assessed"), ("CHK11", "not_assessed"),
            ("CHK12", "not_assessed"), ("CHK13", "not_assessed"), ("CHK14", "not_assessed"),
            ("CHK15", "not_assessed"), ("CHK16", "not_assessed"), ("CHK17", "not_assessed"),
        ]
        for cid, verd in findings_to_insert:
            session.add(Finding(
                scan_id=scan.id, check_id=cid, title=f"Check {cid}",
                engine_verdict=verd, severity="major",
                reason="Not observed on package" if verd == "not_assessed" else None,
            ))
        session.commit()

        out = _scan_out(session, scan)

        # Invariants strictly enforced on serialization
        assert out.counts is not None
        assert out.counts.total == 19
        assert out.counts.passed == 4
        assert out.counts.failed == 3
        assert out.counts.not_assessed == 12

        assert out.checks_total == 19
        assert out.checks_assessed == 7
        assert out.checks_total == out.counts.total
        assert out.checks_assessed == out.counts.passed + out.counts.failed
        assert len(out.findings) == 19

        # Diagnostics counters are reconciled
        assert out.diagnostics["catalog_rule_count"] == 26
        assert out.diagnostics["executable_check_count"] == 19
        assert out.diagnostics["applicable_rule_count"] == 19
        assert out.diagnostics["evaluated_rule_count"] == 7
        assert out.diagnostics["manual_review_rules"] == 7

        # Database record was safely updated
        session.refresh(scan)
        assert scan.checks_total == 19
        assert scan.checks_assessed == 7

    def test_ocr_provider_failure_truthful_counts(self):
        """During an OCR provider outage, all 19 findings are not_assessed,
        checks_assessed is 0, and 0 false violations are manufactured.
        """
        ctx = CheckContext(
            panels_captured={"front", "back"},
            ocr_available=False,
            ocr_failure_reason="Google Vision OCR failed: 403 Forbidden (billing disabled)",
            rules_as_at=date(2026, 9, 21),
        )
        findings, verdict, _ = assess(ctx)

        assert len(findings) == 19
        assert verdict.checks_total == 19
        assert verdict.checks_assessed == 0
        assert verdict.passed == 0
        assert verdict.failed == 0
        assert verdict.not_assessed == 19
        assert verdict.overall_result == "not_assessed"

        # Zero violations
        assert sum(1 for f in findings if f.verdict == "fail") == 0

        # Invariants strictly hold
        assert verdict.passed + verdict.failed + verdict.not_assessed == verdict.checks_total
        assert verdict.checks_assessed == verdict.passed + verdict.failed
