"""schemas.py"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_PHONE_RE = re.compile(r"^[0-9]{10}$")


def _validate_email(v: str | None) -> str | None:
    if v is None:
        return None
    v = v.strip()
    if not v:
        return None
    if not _EMAIL_RE.match(v):
        raise ValueError("Invalid email address")
    return v


def _validate_phone(v: str | None) -> str | None:
    if v is None:
        return None
    p = v.replace(" ", "").replace("-", "")
    if p.startswith("+91"):
        p = p[3:]
    elif p.startswith("+"):
        # Only +91 country code is accepted; anything else is invalid.
        raise ValueError("Phone must be a 10-digit Indian mobile number")
    elif len(p) == 12 and p.startswith("91"):
        p = p[2:]
    elif len(p) == 11 and p.startswith("0"):
        p = p[1:]
    if not _PHONE_RE.match(p):
        raise ValueError("Phone must be a 10-digit number")
    return p


def _validate_password_strength(v: str) -> str:
    if not any(c.isalpha() for c in v) or not any(c.isdigit() for c in v):
        raise ValueError("Password must contain at least one letter and one digit")
    return v

Verdict = Literal["pass", "fail", "not_assessed"]
ScanResult = Literal["compliant", "violation", "not_assessed", "out_of_scope"]
Severity = Literal["critical", "major", "minor", "advisory"]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# ------------------------------------------------------------------ auth
class LoginRequest(BaseModel):
    employee_id: str = Field(min_length=3, max_length=32)
    password: str = Field(min_length=8, max_length=72)


class UserOut(ORMModel):
    id: int
    employee_id: str
    full_name: str
    role: str
    jurisdiction: str | None = None
    is_active: bool
    # Device binding (C7). Exposed so the admin roster can show which officer
    # device is bound (InspectorsScreen reads it); /auth/me returning it to the
    # officer is harmless — the login body already carries the same value.
    install_id: str | None = None


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int                     # seconds; the client refreshes before this
    user: UserOut
    install_id: str
    # The refresh token is NOT in this body. It is set as an httpOnly
    # SameSite=Strict cookie so that no JavaScript in the portal can read it.


# ------------------------------------------------------------ inspections
class CreateInspectionRequest(BaseModel):
    store_id: int
    transaction_type: Literal[
        "retail_sale", "wholesale", "institutional", "industrial",
        "packed_in_presence", "export", "other",
    ]
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    gps_accuracy_m: float | None = Field(default=None, ge=0)
    mock_location: bool | None = None
    local_created_at: datetime | None = None
    notes: str | None = Field(default=None, max_length=4000)


class SubmitInspectionRequest(BaseModel):
    signature_status: Literal["signed", "refused", "unavailable"]
    notes: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _refusal_needs_a_note(self) -> SubmitInspectionRequest:
        if self.signature_status in {"refused", "unavailable"} and not self.notes:
            raise ValueError(
                "A refused or unavailable signature must be explained in notes."
            )
        return self


class PanelGeometry(BaseModel):
    """Mandatory before the millimetre checks can run. C14.

    reference_pixel_size is the pixel length of the scale reference object
    (ID-1 card long edge / 5-INR coin diameter) when scale_source is not
    "declared". Threaded to image_processor.compute_scale by build_context.
    """
    panel_shape: Literal["rectangular", "cylindrical", "other"]
    panel_height_mm: float | None = Field(default=None, gt=0, le=2000)
    panel_width_mm: float | None = Field(default=None, gt=0, le=2000)
    panel_diameter_mm: float | None = Field(default=None, gt=0, le=2000)
    total_surface_area_cm2: float | None = Field(default=None, gt=0)
    is_blown_moulded: bool = False
    scale_source: Literal["declared", "id1_card", "coin_5inr", "none"] = "declared"
    reference_pixel_size: float | None = Field(default=None, gt=0, le=100000)

    @model_validator(mode="after")
    def _shape_needs_its_dimensions(self) -> PanelGeometry:
        if self.scale_source in ("none", "id1_card", "coin_5inr"):
            # When scale_source is 'none', dimensions are not declared and cannot be fabricated.
            # Millimetre typography rules will evaluate to 'not_assessed' (C14 / Rule 7).
            # When scale_source is a reference object (ID-1 / 5-INR coin), scale comes from the object.
            return self
        if self.panel_shape == "rectangular" and not (
            self.panel_height_mm and self.panel_width_mm
        ):
            raise ValueError("A rectangular panel needs height and width in mm.")
        if self.panel_shape == "cylindrical" and not (
            self.panel_height_mm and self.panel_diameter_mm
        ):
            raise ValueError("A cylindrical panel needs height and diameter in mm.")
        if self.panel_shape == "other" and not self.total_surface_area_cm2:
            raise ValueError(
                "An irregular package needs total surface area; Rule 7(4) takes 40% of it."
            )
        return self


# ------------------------------------------------------------------ scans
class CreateScanRequest(BaseModel):
    """One scan per package. Images are attached to it afterwards.

    v1.x created a Scan per uploaded image, so a four-panel package produced
    four independent verdicts on the same package and the report listed the
    same item four times with different results.
    """
    commodity_generic: str | None = Field(default=None, max_length=120)
    brand_name: str | None = Field(default=None, max_length=120)
    commodity_category: str | None = Field(default=None, max_length=60)
    batch_number: str | None = Field(default=None, max_length=60)
    geometry: PanelGeometry


class FindingOut(ORMModel):
    id: int
    check_id: str
    title: str
    engine_verdict: Verdict
    human_verdict: Verdict | None = None
    effective_verdict: Verdict
    severity: Severity
    reason: str | None = None
    observed: str | None = None
    required: str | None = None
    citation: str | None = None
    ledger_ref: str | None = None
    confidence: float | None = None


class ScanImageOut(ORMModel):
    id: int
    panel: str
    sha256: str
    width_px: int
    height_px: int
    rectified: bool
    residual_tilt_deg: float | None = None
    blur_variance: float | None = None
    similarity_status: str | None = None
    duplicate_of_image_id: int | None = None
    hamming_distance: int | None = None
    url: str | None = None
    thumbnail_url: str | None = None


class VerdictCounts(BaseModel):
    """Four counts and the denominator they are counted against. C4/C5."""
    total: int
    passed: int
    failed: int
    not_assessed: int

    @model_validator(mode="after")
    def _must_add_up(self) -> VerdictCounts:
        if self.passed + self.failed + self.not_assessed != self.total:
            raise ValueError("Verdict counts must sum to total.")
        return self


class ScanOut(ORMModel):
    id: int
    inspection_id: int
    commodity_generic: str | None = None
    brand_name: str | None = None
    batch_number: str | None = None
    net_quantity_value: float | None = None
    net_quantity_unit: str | None = None
    mrp: float | None = None
    overall_result: ScanResult
    violation_limb: str | None = None
    recommended_action: str | None = None
    checks_total: int
    checks_assessed: int
    mm_per_pixel: float | None = None
    scale_source: str | None = None
    rules_as_at: date
    catalog_hash: str
    engine_version: str
    rule_pack_version: str | None = None
    duplicate_of: int | None = None
    created_at: datetime
    # Set by the router after model_validate (the ORM row has no `counts`
    # attribute); optional here so validation of the ORM object does not fail.
    counts: VerdictCounts | None = None
    findings: list[FindingOut]
    images: list[ScanImageOut]
    diagnostics: dict[str, Any] | None = None


class ScanListItemOut(BaseModel):
    id: int
    inspection_id: int
    store_id: int
    store_name: str
    store_area: str | None = None
    inspector_id: int
    inspector_name: str
    commodity_generic: str | None = None
    brand_name: str | None = None
    commodity_category: str | None = None
    batch_number: str | None = None
    barcode: str | None = None
    net_quantity_value: float | None = None
    net_quantity_unit: str | None = None
    mrp: float | None = None
    overall_result: ScanResult
    checks_total: int
    checks_assessed: int
    image_count: int
    created_at: datetime


# ---------------------------------------------------------------- reports
class ResultCounts(BaseModel):
    """The five scan results plus refusals. Required in every report and dashboard payload.

    `refusals` counts inspections whose signature_status is 'refused' — it is
    an inspection-level count (not a scan count) and is additive with zero as
    a safe default so older clients ignore it.
    """
    total: int = 0
    compliant: int = 0
    violation: int = 0
    not_assessed: int = 0
    out_of_scope: int = 0
    refusals: int = 0


class StoreBreakdown(BaseModel):
    store_id: int
    store_name: str
    counts: ResultCounts


class TodaysReportResponse(BaseModel):
    report_date: date
    inspector: UserOut
    inspections: int
    counts: ResultCounts
    stores: list[StoreBreakdown]
    generated_at: datetime


class TrendPoint(BaseModel):
    day: date
    counts: ResultCounts


class CheckTally(BaseModel):
    check_id: str
    title: str
    count: int


class AdminDashboardResponse(BaseModel):
    period_start: date
    period_end: date
    inspections: int
    active_inspectors: int
    stores_visited: int = 0
    counts: ResultCounts
    review_queue: int
    top_failed_checks: list[CheckTally]
    trend: list[TrendPoint]
    violations_by_category: list[dict] | None = None
    violations_by_area: list[dict] | None = None


class AdminViolationItem(BaseModel):
    id: int
    date: str
    store_id: int
    store_name: str
    area: str
    commodity_generic: str
    product_name: str
    brand_name: str
    manufacturer: str
    category: str
    rule: str
    reason: str
    result: str
    inspector: str


class AdminViolationsResponse(BaseModel):
    total: int
    violations: list[AdminViolationItem]
    top_violations: list[dict] = []


class RepeatOffenderHistory(BaseModel):
    id: int
    scan_id: int | None = None
    check_id: str
    title: str | None = None
    citation: str | None = None
    severity: str | None = None
    brand: str | None = None
    region: str | None = None
    store_id: int
    shopName: str | None = None
    inspector_id: int | None = None
    inspector: str | None = None
    product: str | None = None
    date: str | None = None


class RepeatOffender(BaseModel):
    id: str | None = None
    name: str
    violations: int
    stores: int
    brands: list[str] = []
    regions: list[str] = []
    last_violation: str | None = None
    history: list[RepeatOffenderHistory] = []


class RepeatOffendersResponse(BaseModel):
    period_start: str
    period_end: str
    offenders: list[RepeatOffender] = []
    stores: list[dict] = []


class RuleVersionOut(BaseModel):
    id: str
    year: int
    name: str
    gazette_ref: str
    effective_from: str
    effective_to: str | None = None
    description: str
    status: str
    is_active: bool
    summary: str
    rules: list[dict] = []


class ToggleRuleVersionRequest(BaseModel):
    is_active: bool


# ------------------------------------------------------------------ admin
class CreateUserRequest(BaseModel):
    employee_id: str = Field(min_length=3, max_length=32)
    full_name: str = Field(min_length=2, max_length=120)
    password: str = Field(min_length=12, max_length=72)
    role: Literal["inspector", "admin"] = "inspector"
    jurisdiction: str | None = None
    email: str | None = None
    phone: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _email_ok(cls, v):
        return _validate_email(v)

    @field_validator("phone", mode="before")
    @classmethod
    def _phone_ok(cls, v):
        return _validate_phone(v)

    @field_validator("password", mode="after")
    @classmethod
    def _pw_strong(cls, v):
        return _validate_password_strength(v)


class UpdateUserRequest(BaseModel):
    full_name: str | None = Field(default=None, max_length=120)
    role: Literal["inspector", "admin"] | None = None
    jurisdiction: str | None = None
    is_active: bool | None = None
    email: str | None = None
    phone: str | None = None

    @field_validator("email", mode="before")
    @classmethod
    def _email_ok(cls, v):
        return _validate_email(v)

    @field_validator("phone", mode="before")
    @classmethod
    def _phone_ok(cls, v):
        return _validate_phone(v)


class ResetInstallRequest(BaseModel):
    reason: str = Field(min_length=10, max_length=500)


class OverrideFindingRequest(BaseModel):
    human_verdict: str | None = None
    override_reason: str = Field(min_length=1, max_length=1000)

    @field_validator("human_verdict", mode="before")
    @classmethod
    def _normalize_verdict(cls, v: str | None) -> str | None:
        if v is None:
            return None
        norm = str(v).strip().lower()
        if norm in ("pass", "compliant"):
            return "pass"
        if norm in ("fail", "violation", "non_compliant"):
            return "fail"
        if norm in ("not_assessed", "out_of_scope"):
            return "not_assessed"
        raise ValueError("Verdict must be pass/compliant, fail/violation, or not_assessed/out_of_scope")


class UpdateInspectionRequest(BaseModel):
    notes: str | None = Field(default=None, max_length=4000)
    signature_status: str | None = Field(default=None, max_length=24)


class AuditEntryOut(ORMModel):
    seq: int
    inspection_id: int | None = None
    scan_id: int | None = None
    user_id: int | None = None
    action: str
    old_value: str | None = None
    new_value: str | None = None
    reason: str | None = None
    timestamp: datetime
    hash_self: str


# ---------------------------------------------------------------- batch assess
class BatchAssessRequest(BaseModel):
    """Body for POST /scans/batch-assess.

    scan_ids: list of scan primary keys to (re-)assess. The caller is
    responsible for filtering to scans they own; the endpoint enforces
    ownership per item and reports per-item errors rather than batch-rejecting.
    """
    scan_ids: list[int] = Field(min_length=1, max_length=100)
    model_config = ConfigDict(extra="forbid")


class BatchAssessItem(BaseModel):
    scan_id: int
    overall_result: str | None = None
    ok: bool
    error: str | None = None


class BatchAssessResponse(BaseModel):
    total: int
    results: list[BatchAssessItem]
