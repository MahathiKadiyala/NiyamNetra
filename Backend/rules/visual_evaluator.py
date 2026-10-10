"""Backend/rules/visual_evaluator.py — Deterministic Visual and Geometry Compliance Evaluator (Phase 4C).

Core Invariants (Sections 1, 2, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20):
- OCR READS -> LLM STRUCTURES -> RULES DECIDE.
- Zero external AI/LLM/OCR calls. Operates on derived artifacts and validated evidence.
- Physical mm measurements require a REAL validated optical calibration source.
  Never infer scale from fake 120x80 mm dimensions, smartphone DPI, or image dimensions.
  Missing/unvalidated scale returns NOT_ASSESSED.
- Rule 7: Numerals and letters must satisfy Table-I / Table-II minimum height based on PDP area.
  Character width must be >= 1/3 height under Rule 7(3) (exempting narrow glyphs 1, i, I, l).
- Rule 8: Mandatory declarations must be placed on the PDP and net quantity must have clear surrounding space.
  Anti-false-compliance safeguard: absence of interfering print/graphics in clear space must be verified.
- Rule 9: Conspicuous contrast requirement with blown/moulded/embossed exception.
  Measures engineering contrast signal; never invents an arbitrary legal threshold (e.g. WCAG 4.5:1).
- Grounded in evidence provenance with coordinate system validation and immutability of original evidence.
"""
from __future__ import annotations

import logging
import math
import os
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from citations import cite
from config import settings
from rules.loader import load_rule_pack
from rules_engine import (
    CheckContext,
    FindingResult,
    Verdict,
    compare_with_uncertainty,
    min_height_mm,
    pdp_area_cm2,
)

logger = logging.getLogger("niyamnetra.rules.visual_evaluator")

# Validated physical calibration sources (Sections 4 & 5)
VALIDATED_CALIBRATION_SOURCES = {
    "validated_reference",
    "id1_card",
    "coin_5inr",
    "optical_target",
    "calibrated_ruler",
}

# Narrow glyphs exempt from Rule 7(3) 1/3 width requirement
NARROW_GLYPHS = set("1iIl")
WIDTH_RATIO = 1.0 / 3.0


@dataclass(slots=True)
class CalibrationSpec:
    """Audit model for physical scale calibration (Section 5)."""
    scale_source: str                  # validated_reference | id1_card | coin_5inr | optical_target | calibrated_ruler | declared | none | invalid
    reference_type: str | None = None  # id1_card | coin_5inr | optical_target | calibrated_ruler | none
    mm_per_pixel: float | None = None
    mm_per_pixel_uncertainty: float | None = None
    reference_width_mm: float | None = None
    reference_height_mm: float | None = None
    reference_pixel_width: float | None = None
    reference_pixel_height: float | None = None
    calibration_error: float | None = None
    validated_at: str | None = None


def validate_calibration(ctx: Any) -> tuple[bool, str | None]:
    """Verify that physical measurement is backed by a validated physical reference (Sections 4 & 5).

    Prohibits fake physical sizes (e.g. 120x80 mm default), smartphone DPI assumptions,
    or bare unverified operator numeric inputs.
    """
    source = getattr(ctx, "scale_source", "none") or "none"
    mmpp = getattr(ctx, "mm_per_pixel", None)

    if source not in VALIDATED_CALIBRATION_SOURCES or mmpp is None or mmpp <= 0:
        return False, (
            "Physical measurement cannot be established because no validated "
            "scale/calibration reference is available."
        )

    # Check uncertainty bounds: relative uncertainty > 25% cannot produce legal findings
    unc = getattr(ctx, "mm_per_pixel_uncertainty", None)
    if unc is not None and (unc / mmpp) > 0.25:
        return False, (
            f"Scale calibration uncertainty (+/-{unc:.4f} mm/px on {mmpp:.4f} mm/px) "
            f"exceeds acceptable tolerance for legal compliance verification."
        )

    return True, None


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _format_visual_provenance(
    ctx: CheckContext,
    target_field: str | None = None,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    prov: dict[str, Any] = {
        "scale_source": getattr(ctx, "scale_source", "none"),
        "mm_per_pixel": getattr(ctx, "mm_per_pixel", None),
        "mm_per_pixel_uncertainty": getattr(ctx, "mm_per_pixel_uncertainty", None),
        "panels_captured": list(getattr(ctx, "panels_captured", set())),
        "panel_shape": getattr(ctx, "panel_shape", None),
    }
    if target_field:
        prov["target_field"] = target_field
        if hasattr(ctx, "measured_heights_mm") and target_field in ctx.measured_heights_mm:
            prov["measured_height_mm"] = ctx.measured_heights_mm[target_field]
    if extra:
        prov.update(extra)
    return prov


# ---------------------------------------------------------------------------
# Coordinate System Reconciliation (Section 19)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class CoordinateReconciliation:
    target_bbox: tuple[int, int, int, int] | None  # (x1, y1, x2, y2)
    status: str                                    # valid | coordinate_space_mismatch | out_of_bounds | target_not_localized
    reason: str | None = None
    source_space: str = "unknown"
    target_space: str = "rectified_panel"


def reconcile_coordinates(
    bbox: tuple[float, float, float, float] | list[float] | None,
    source_artifact: str,
    target_shape: tuple[int, int],  # (height, width)
    source_shape: tuple[int, int] | None = None,
    homography: np.ndarray | None = None,
) -> CoordinateReconciliation:
    """Reconciles bounding box coordinates into target image space with boundary checks (Section 19)."""
    if bbox is None or len(bbox) != 4:
        return CoordinateReconciliation(
            None,
            "target_not_localized",
            "Target declaration bounding box was not localized.",
            source_space=source_artifact,
        )

    h_tgt, w_tgt = target_shape
    if h_tgt <= 0 or w_tgt <= 0:
        return CoordinateReconciliation(
            None,
            "coordinate_space_mismatch",
            "Target artifact dimensions are invalid.",
            source_space=source_artifact,
        )

    x1, y1, x2, y2 = [float(v) for v in bbox]

    # Handle normalized coordinates [0.0, 1.0]
    if 0.0 <= x1 <= 1.0 and 0.0 <= y1 <= 1.0 and 0.0 <= x2 <= 1.0 and 0.0 <= y2 <= 1.0:
        ix1 = int(round(x1 * w_tgt))
        iy1 = int(round(y1 * h_tgt))
        ix2 = int(round(x2 * w_tgt))
        iy2 = int(round(y2 * h_tgt))
        if ix2 <= ix1 or iy2 <= iy1:
            return CoordinateReconciliation(
                None, "coordinate_space_mismatch", "Degenerate normalized bounding box dimensions."
            )
        return CoordinateReconciliation((ix1, iy1, ix2, iy2), "valid", source_space="normalized")

    # If coordinates are in analysis_image space and target is rectified_panel
    if source_artifact == "analysis_image" and homography is not None:
        try:
            pts = np.array([[[x1, y1]], [[x2, y1]], [[x2, y2]], [[x1, y2]]], dtype=np.float32)
            warped = cv2.perspectiveTransform(pts, homography)
            wx1 = int(round(np.min(warped[:, 0, 0])))
            wy1 = int(round(np.min(warped[:, 0, 1])))
            wx2 = int(round(np.max(warped[:, 0, 0])))
            wy2 = int(round(np.max(warped[:, 0, 1])))
            # Clip bounds
            wx1 = max(0, min(wx1, w_tgt - 1))
            wy1 = max(0, min(wy1, h_tgt - 1))
            wx2 = max(wx1 + 1, min(wx2, w_tgt))
            wy2 = max(wy1 + 1, min(wy2, h_tgt))
            return CoordinateReconciliation(
                (wx1, wy1, wx2, wy2), "valid", source_space="analysis_image"
            )
        except Exception as e:
            return CoordinateReconciliation(
                None,
                "coordinate_space_mismatch",
                f"Perspective coordinate mapping failed: {e}",
                source_space=source_artifact,
            )

    # Same space direct coordinates
    if source_artifact in ("rectified_panel", "target"):
        ix1, iy1, ix2, iy2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
        # Severe out of bounds check
        if ix1 < -w_tgt * 0.1 or iy1 < -h_tgt * 0.1 or ix2 > w_tgt * 1.1 or iy2 > h_tgt * 1.1:
            return CoordinateReconciliation(
                None,
                "out_of_bounds",
                "Bounding box coordinates lie outside the target panel boundaries.",
                source_space=source_artifact,
            )
        # Moderate clipping to image frame
        cx1 = max(0, min(ix1, w_tgt - 1))
        cy1 = max(0, min(iy1, h_tgt - 1))
        cx2 = max(cx1 + 1, min(ix2, w_tgt))
        cy2 = max(cy1 + 1, min(iy2, h_tgt))
        if cx2 - cx1 < 4 or cy2 - cy1 < 4:
            return CoordinateReconciliation(
                None, "coordinate_space_mismatch", "Bounding box clipped to degenerate area."
            )
        return CoordinateReconciliation((cx1, cy1, cx2, cy2), "valid", source_space=source_artifact)

    # Mismatch when shapes differ and no homography available
    if source_shape and source_shape != target_shape and homography is None:
        return CoordinateReconciliation(
            None,
            "coordinate_space_mismatch",
            "Evidence coordinate system could not be reconciled with the evaluated artifact.",
            source_space=source_artifact,
        )

    ix1, iy1, ix2, iy2 = int(round(x1)), int(round(y1)), int(round(x2)), int(round(y2))
    return CoordinateReconciliation((ix1, iy1, ix2, iy2), "valid", source_space=source_artifact)


# ---------------------------------------------------------------------------
# Deterministic Glyph Character Measurement (Sections 8, 9, 10)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class GlyphMeasurementResult:
    status: str                       # measured | insufficient_resolution | insufficient_sharpness | severe_glare | segmentation_unreliable | character_height_uncertain
    glyph_height_px: float | None = None
    glyph_height_mm: float | None = None
    glyph_width_px: float | None = None
    glyph_width_mm: float | None = None
    uncertainty_mm: float | None = None
    component_count: int = 0
    reason: str | None = None
    details: dict[str, Any] = field(default_factory=dict)


def measure_glyph_height_mm(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
    mm_per_pixel: float,
    mm_per_pixel_uncertainty: float | None = None,
) -> GlyphMeasurementResult:
    """Conservative, deterministic glyph character measurement on image patch (Sections 8 & 9).

    Uses connected components analysis on local Otsu segmented patch without ML or OCR re-run.
    """
    if image is None or getattr(image, "size", 0) == 0:
        return GlyphMeasurementResult("segmentation_unreliable", reason="Image artifact is empty.")

    h_img, w_img = image.shape[:2]
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(x1, w_img - 1))
    y1 = max(0, min(y1, h_img - 1))
    x2 = max(x1 + 1, min(x2, w_img))
    y2 = max(y1 + 1, min(y2, h_img))

    crop = image[y1:y2, x1:x2]
    crop_h, crop_w = crop.shape[:2]

    # Quality Gate 1: Resolution
    if crop_h < 8 or crop_w < 8:
        return GlyphMeasurementResult(
            "insufficient_resolution",
            reason=f"Target crop ({crop_w}x{crop_h} px) is below minimum resolution threshold (8x8 px).",
        )

    # Grayscale conversion
    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
    else:
        gray = crop

    # Quality Gate 2: Specular Glare (checked before sharpness, as glare washes out edges)
    saturated = float(np.mean(gray > 248))
    if saturated > 0.35:
        return GlyphMeasurementResult(
            "severe_glare",
            reason=f"Severe specular glare ({saturated*100:.1f}% saturated pixels) obscures character glyphs.",
            details={"glare_ratio": saturated},
        )

    # Quality Gate 3: Sharpness (Laplacian variance)
    lap_var = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    if lap_var < 5.0:
        return GlyphMeasurementResult(
            "insufficient_sharpness",
            reason=f"Target crop sharpness ({lap_var:.1f}) is insufficient for legal typography measurement.",
            details={"sharpness_laplacian_var": lap_var},
        )

    # Deterministic Segmentation: Otsu thresholding
    _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    # Check polarity: printed text foreground is typically <= 65% of patch area
    fg_fraction = float(np.mean(binary > 0))
    if fg_fraction > 0.65:
        binary = cv2.bitwise_not(binary)

    # Filter connected components
    num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(binary, connectivity=8)
    valid_heights = []
    valid_widths = []

    for i in range(1, num_labels):
        comp_w = stats[i, cv2.CC_STAT_WIDTH]
        comp_h = stats[i, cv2.CC_STAT_HEIGHT]
        comp_area = stats[i, cv2.CC_STAT_AREA]

        # Ignore tiny speckle noise and full-box border artifacts
        if comp_area < 4 or comp_h < 4:
            continue
        if comp_h > crop_h * 1.02 or comp_w > crop_w * 0.95:
            continue
        # Character glyphs typically occupy 25% to 100% of line bounding height
        if comp_h < crop_h * 0.25:
            continue

        valid_heights.append(comp_h)
        valid_widths.append(comp_w)

    if not valid_heights:
        return GlyphMeasurementResult(
            "segmentation_unreliable",
            reason="No coherent foreground character glyph components could be segmented.",
            details={"sharpness": lap_var, "components_found": num_labels - 1},
        )

    # Quality Gate 4: Plausible character evidence & consistency
    h_arr = np.array(valid_heights, dtype=np.float64)
    w_arr = np.array(valid_widths, dtype=np.float64)

    # Upper 75th percentile represents capital/numeral glyph height in mixed-case strings
    measured_h_px = float(np.percentile(h_arr, 75))
    measured_w_px = float(np.median(w_arr))

    # Reject if variance is excessive and evidence is sparse
    if len(h_arr) >= 3 and (float(np.std(h_arr)) / (float(np.mean(h_arr)) + 1e-6)) > 0.6:
        return GlyphMeasurementResult(
            "character_height_uncertain",
            reason="Character height variance within declaration bounding box exceeds acceptable legal limits.",
            details={"std_dev": float(np.std(h_arr)), "mean": float(np.mean(h_arr))},
        )

    # Convert to physical millimetres with combined uncertainty
    measured_h_mm = measured_h_px * mm_per_pixel
    measured_w_mm = measured_w_px * mm_per_pixel

    scale_unc = mm_per_pixel_uncertainty or (mm_per_pixel * 0.05)
    # Combined uncertainty: optical scale error + 1.0 px discretization boundary error
    u_mm = math.sqrt((measured_h_px * scale_unc) ** 2 + (1.0 * mm_per_pixel) ** 2)

    return GlyphMeasurementResult(
        "measured",
        glyph_height_px=measured_h_px,
        glyph_height_mm=measured_h_mm,
        glyph_width_px=measured_w_px,
        glyph_width_mm=measured_w_mm,
        uncertainty_mm=u_mm,
        component_count=len(valid_heights),
        details={
            "crop_dimensions": [crop_w, crop_h],
            "laplacian_variance": lap_var,
            "components_analyzed": len(valid_heights),
        },
    )


# ---------------------------------------------------------------------------
# Clear Space Image Verification (Section 13 & 14)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class ClearSpaceImageVerification:
    verified_clean: bool | None       # True: clean | False: interfering print/graphics | None: unassessable
    edge_density: float | None = None
    variance: float | None = None
    reason: str | None = None


def verify_clear_space_image(
    image: np.ndarray | None,
    target_bbox: tuple[int, int, int, int] | None,
    numeral_height_px: float | None,
    v_mult: float = 1.0,
    h_mult: float = 2.0,
) -> ClearSpaceImageVerification:
    """Anti-false-compliance safeguard for Rule 8 clear space (Section 14).

    Inspects derived image margin rings to ensure absence of decorative printing,
    logos, and graphics in the clear space around the quantity declaration.
    """
    if image is None or getattr(image, "size", 0) == 0 or target_bbox is None or numeral_height_px is None:
        return ClearSpaceImageVerification(
            None,
            reason="Image evidence is unavailable to verify absence of decorative graphics in clear space.",
        )

    h_img, w_img = image.shape[:2]
    x1, y1, x2, y2 = target_bbox
    v_clear = int(round(numeral_height_px * v_mult))
    h_clear = int(round(numeral_height_px * h_mult))

    # Define margin bounds
    top_y1 = max(0, y1 - v_clear)
    bot_y2 = min(h_img, y2 + v_clear)
    left_x1 = max(0, x1 - h_clear)
    right_x2 = min(w_img, x2 + h_clear)

    if (top_y1 >= y1 and bot_y2 <= y2) or (left_x1 >= x1 and right_x2 <= x2):
        return ClearSpaceImageVerification(
            False, reason="Target declaration reaches image boundary; clear space is clipped."
        )

    if len(image.shape) == 3:
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    else:
        gray = image

    # Extract 4 surrounding margin patches
    margins = []
    if y1 > top_y1:
        margins.append(gray[top_y1:y1, left_x1:right_x2])  # Above
    if bot_y2 > y2:
        margins.append(gray[y2:bot_y2, left_x1:right_x2])  # Below
    if x1 > left_x1:
        margins.append(gray[y1:y2, left_x1:x1])            # Left
    if right_x2 > x2:
        margins.append(gray[y1:y2, x2:right_x2])           # Right

    if not margins:
        return ClearSpaceImageVerification(
            None, reason="Margin regions could not be established."
        )

    max_edge_density = 0.0
    max_variance = 0.0

    for m in margins:
        if m.size < 16:
            continue
        edges = cv2.Canny(m, 50, 150)
        ed = float(np.count_nonzero(edges)) / float(m.size)
        var = float(np.var(m))
        if ed > max_edge_density:
            max_edge_density = ed
        if var > max_variance:
            max_variance = var

    # Low edge density and smooth gradient indicate empty, clean background
    if max_edge_density > 0.06 or max_variance > 750.0:
        return ClearSpaceImageVerification(
            False,
            edge_density=max_edge_density,
            variance=max_variance,
            reason="Interfering decorative graphics, patterns, or artwork detected in surrounding clear space.",
        )

    return ClearSpaceImageVerification(
        True,
        edge_density=max_edge_density,
        variance=max_variance,
        reason="Surrounding clear space verified free of interfering graphics or text.",
    )


# ---------------------------------------------------------------------------
# Conspicuous Contrast Engineering Measurement (Sections 15 & 16)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class EngineeringContrastSignal:
    contrast_ratio: float
    luminance_separation: float
    foreground_luminance: float
    background_luminance: float
    color_distance_delta_e: float
    bimodal_confidence: float
    details: dict[str, Any] = field(default_factory=dict)


def measure_conspicuous_contrast_signal(
    image: np.ndarray,
    bbox: tuple[int, int, int, int],
) -> tuple[EngineeringContrastSignal | None, str | None]:
    """Measures physical pixel luminance contrast signal on derived image artifact (Section 15).

    Does NOT invent an arbitrary legal threshold (e.g. WCAG 4.5:1).
    Returns the objective engineering contrast signal.
    """
    if image is None or getattr(image, "size", 0) == 0:
        return None, "Image artifact is empty or unavailable."

    h_img, w_img = image.shape[:2]
    x1, y1, x2, y2 = bbox
    x1 = max(0, min(x1, w_img - 1))
    y1 = max(0, min(y1, h_img - 1))
    x2 = max(x1 + 1, min(x2, w_img))
    y2 = max(y1 + 1, min(y2, h_img))

    crop = image[y1:y2, x1:x2]
    if crop.shape[0] < 6 or crop.shape[1] < 6:
        return None, "Declaration crop is too small for contrast measurement."

    if len(crop.shape) == 3:
        gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)
        lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB)
    else:
        gray = crop
        lab = cv2.cvtColor(cv2.cvtColor(crop, cv2.COLOR_GRAY2BGR), cv2.COLOR_BGR2LAB)

    # Otsu bimodal segmentation
    thresh_val, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)

    # Character polarity
    fg_mask = binary > 0
    if np.mean(fg_mask) > 0.65:
        fg_mask = ~fg_mask
    bg_mask = ~fg_mask

    if np.count_nonzero(fg_mask) < 4 or np.count_nonzero(bg_mask) < 4:
        return None, "Text and background luminance could not be separated reliably."

    fg_lum = float(np.mean(gray[fg_mask]))
    bg_lum = float(np.mean(gray[bg_mask]))
    lum_sep = abs(fg_lum - bg_lum)

    high_l = max(fg_lum, bg_lum)
    low_l = min(fg_lum, bg_lum)
    contrast_ratio = (high_l + 0.05) / (low_l + 0.05)

    # CIELAB color distance delta E (76)
    fg_lab = np.mean(lab[fg_mask], axis=0)
    bg_lab = np.mean(lab[bg_mask], axis=0)
    delta_e = float(np.linalg.norm(fg_lab - bg_lab))

    # Otsu bimodal separation confidence (inter-class variance / total variance)
    total_var = float(np.var(gray))
    p1 = np.mean(fg_mask)
    p2 = 1.0 - p1
    inter_var = p1 * p2 * ((fg_lum - bg_lum) ** 2)
    bimodal_conf = float(inter_var / (total_var + 1e-6)) if total_var > 0 else 0.0

    signal = EngineeringContrastSignal(
        contrast_ratio=round(contrast_ratio, 2),
        luminance_separation=round(lum_sep, 2),
        foreground_luminance=round(fg_lum, 1),
        background_luminance=round(bg_lum, 1),
        color_distance_delta_e=round(delta_e, 2),
        bimodal_confidence=round(min(1.0, bimodal_conf), 3),
        details={"otsu_threshold": thresh_val, "total_pixels": crop.shape[0] * crop.shape[1]},
    )
    return signal, None


# ---------------------------------------------------------------------------
# 1. Rule 7: Character / Numeral Height (CHK06 / RULE_LMPC_06)
# ---------------------------------------------------------------------------

def evaluate_character_height(ctx: CheckContext) -> FindingResult:
    """Deterministic evaluation of character height under Rule 7(2) Table-I (CHK06)."""
    pack = load_rule_pack()
    as_at = getattr(ctx, "rules_as_at", None)
    rule = pack.get_rule_by_id("CHK06", as_at=as_at)
    rule_id = rule.rule_id if rule else "RULE_LMPC_06_CHARACTER_HEIGHT"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK06",
        "Character height meets Table-I for the panel area",
        "pass",
        "major",
        citation=cite("R7-2", "Rule 7(2) read with Table-I as substituted w.e.f 01.01.2018"),
        ledger_ref="L-11",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    if rule is None:
        t.verdict = "not_assessed"
        t.reason = f"No applicable rule found for CHK06 on inspection date {as_at.isoformat() if as_at else 'current'}."
        return t

    if as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK06 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()}."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    front_failed = getattr(ctx, "panels_failed_ocr", {}).get("front") or getattr(ctx, "panels_failed_ocr", {}).get("principal")
    if front_failed:
        t.verdict = "not_assessed"
        t.reason = f"Principal display panel OCR failed ({front_failed}); character height cannot be evaluated."
        return t

    if getattr(ctx, "phase3_halted", False) or getattr(ctx, "is_medical_device", False):
        t.verdict = "not_assessed"
        t.reason = (
            "Not assessed: medical device, labelled under the Medical Devices "
            "Rules 2017 rather than by the typography requirements of these Rules."
        )
        return t

    # Calibration gate (Sections 4 & 5)
    cal_valid, cal_reason = validate_calibration(ctx)
    if not cal_valid:
        t.verdict = "not_assessed"
        t.reason = cal_reason
        t.evidence_provenance = _format_visual_provenance(ctx)
        return t

    # PDP area gate (Section 7)
    area, area_reason = pdp_area_cm2(ctx)
    if area is None:
        t.verdict = "not_assessed"
        t.reason = area_reason or "Principal display panel could not be established from available geometry."
        t.evidence_provenance = _format_visual_provenance(ctx)
        return t

    # Optional image-level measurement integration if rectified_image and bboxes are supplied
    measured_heights = dict(getattr(ctx, "measured_heights_mm", {}) or {})
    rect_img = getattr(ctx, "rectified_image", None)
    bboxes = getattr(ctx, "declaration_bboxes", {})

    if rect_img is not None and bboxes:
        mmpp = ctx.mm_per_pixel or 0.05
        unc = ctx.mm_per_pixel_uncertainty or 0.002
        for fld, box in bboxes.items():
            if fld not in measured_heights and box:
                res = measure_glyph_height_mm(rect_img, box, mmpp, unc)
                if res.status == "measured" and res.glyph_height_mm is not None:
                    measured_heights[fld] = res.glyph_height_mm

    if not measured_heights:
        t.verdict = "not_assessed"
        t.reason = (
            "No character heights could be measured on the rectified panel, so "
            "Table-I compliance cannot be determined."
        )
        t.evidence_provenance = _format_visual_provenance(ctx)
        return t

    required = min_height_mm(area, ctx.is_blown_moulded)
    smallest_field = min(measured_heights, key=measured_heights.get)
    smallest = measured_heights[smallest_field]
    u = (ctx.mm_per_pixel_uncertainty or 0.0) * 2.0

    verdict, note = compare_with_uncertainty(smallest, required, u)
    t.verdict = verdict
    t.required = (
        f"Minimum {required:.1f} mm for a principal display panel of "
        f"{area:.0f} cm2"
        + (" (blown-moulded container)" if ctx.is_blown_moulded else "")
        + " under Rule 7 Table-I."
    )
    t.evidence_provenance = _format_visual_provenance(
        ctx,
        target_field=smallest_field,
        extra={
            "pdp_area_cm2": area,
            "measured_value_mm": smallest,
            "required_min_mm": required,
            "uncertainty_mm": u,
        },
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
        t.remediation = rule.remediation if rule else f"Increase character font height to at least {required:.1f} mm on principal display panel."
    return t


# ---------------------------------------------------------------------------
# 2. Rule 7: Net Quantity Numeral Height (CHK06b / RULE_LMPC_06B)
# ---------------------------------------------------------------------------

def evaluate_net_quantity_height(ctx: CheckContext) -> FindingResult:
    """Deterministic evaluation of net quantity numeral height under Rule 7(2) Table-I (post-2018)
    or historical Table-II (pre-2018) (CHK06b).

    G.S.R. 629(E), w.e.f 01.01.2018, substituted Rule 7(2) with:
    'The height of any numeral and letter in the declaration required under these rules shall be as per Table-I.'
    The same amendment omitted Table-II.
    """
    pack = load_rule_pack()
    as_at = getattr(ctx, "rules_as_at", None)
    rule = pack.get_rule_by_id("CHK06b", as_at=as_at)
    rule_id = rule.rule_id if rule else "RULE_LMPC_06B_NET_QUANTITY_NUMERAL_HEIGHT"
    pack_ver = pack.rule_pack_version

    citation_desc = (
        "Rule 7(2) read with Table-I as substituted by GSR 629(E) w.e.f 01.01.2018"
        if (as_at is None or as_at >= date(2018, 1, 1))
        else "Rule 7 read with Table-II (pre-2018 historical rule)"
    )

    t = FindingResult(
        "CHK06b",
        "Net-quantity numeral meets minimum height under Rule 7",
        "pass",
        "major",
        citation=cite("R7-nq", citation_desc),
        ledger_ref="L-04",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    if rule is None:
        t.verdict = "not_assessed"
        t.reason = f"No applicable rule found for CHK06b on inspection date {as_at.isoformat() if as_at else 'current'}."
        return t

    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK06b ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()}."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    front_failed = getattr(ctx, "panels_failed_ocr", {}).get("front") or getattr(ctx, "panels_failed_ocr", {}).get("principal")
    if front_failed:
        t.verdict = "not_assessed"
        t.reason = f"Principal display panel OCR failed ({front_failed}); net quantity height cannot be evaluated."
        return t

    if getattr(ctx, "phase3_halted", False) or getattr(ctx, "is_medical_device", False):
        t.verdict = "not_assessed"
        t.reason = "Not assessed: medical device, labelled under Medical Devices Rules 2017."
        return t

    # Calibration gate (Sections 4 & 5)
    cal_valid, cal_reason = validate_calibration(ctx)
    if not cal_valid:
        t.verdict = "not_assessed"
        t.reason = cal_reason
        t.evidence_provenance = _format_visual_provenance(ctx, target_field="net_quantity")
        return t

    area, area_reason = pdp_area_cm2(ctx)
    if area is None:
        t.verdict = "not_assessed"
        t.reason = area_reason or "Principal display panel area could not be determined to select Rule 7 numeral height."
        t.evidence_provenance = _format_visual_provenance(ctx, target_field="net_quantity")
        return t

    if ctx.net_quantity_value is None:
        t.verdict = "not_assessed"
        t.reason = "Net quantity was not read, so its specific height requirement cannot be selected."
        t.evidence_provenance = _format_visual_provenance(ctx, target_field="net_quantity")
        return t

    measured = ctx.measured_heights_mm.get("net_quantity")
    if measured is None:
        t.verdict = "not_assessed"
        t.reason = "The net-quantity declaration was not located or measured on the rectified panel."
        t.evidence_provenance = _format_visual_provenance(ctx, target_field="net_quantity")
        return t

    is_blown = ctx.is_blown_moulded
    params = rule.evaluation_method.parameters if rule and rule.evaluation_method else {}
    table_ref = params.get("table_reference", "table_i")

    # Select statutory threshold from versioned rule pack parameters
    if table_ref == "table_ii" and as_at and as_at < date(2018, 1, 1):
        # Pre-2018 historical rule: Table-II for count/length/area; Table-I for mass/volume
        unit = (ctx.net_quantity_unit or "").lower()
        if unit in {"pcs", "pc", "u", "n", "m", "cm", "mm"}:
            table_ii = params.get("table_ii_count_length", [
                [100.0, 1.0, 2.0], [500.0, 2.0, 4.0], [2500.0, 4.0, 6.0], [999999.0, 6.0, 6.0]
            ])
            required = 6.0
            for upper, normal, blown_h in table_ii:
                if area <= upper:
                    required = blown_h if is_blown else normal
                    break
            rule_source_desc = "pre-2018 Rule 7 Table-II"
            table_used = "table_ii"
        else:
            table_i = params.get("table_i", [
                [50.0, 1.0, 1.5], [100.0, 1.5, 3.0], [500.0, 2.5, 4.0], [2500.0, 4.0, 6.0], [999999.0, 6.0, 6.0]
            ])
            required = 6.0
            for upper, normal, blown_h in table_i:
                if area <= upper:
                    required = blown_h if is_blown else normal
                    break
            rule_source_desc = "pre-2018 Rule 7 Table-I"
            table_used = "table_i"
    else:
        # Post-2018: Rule 7(2) substituted w.e.f 01.01.2018 (GSR 629(E)). Table-II is omitted.
        # Table-I governs ALL numerals and letters in declarations, including length/area/number.
        table_i = params.get("table_i", [
            [50.0, 1.0, 1.5], [100.0, 1.5, 3.0], [500.0, 2.5, 4.0], [2500.0, 4.0, 6.0], [999999.0, 6.0, 6.0]
        ])
        required = 6.0
        for upper, normal, blown_h in table_i:
            if area <= upper:
                required = blown_h if is_blown else normal
                break
        rule_source_desc = "Rule 7(2) Table-I (GSR 629(E))"
        table_used = "table_i"

    u = (ctx.mm_per_pixel_uncertainty or 0.0) * 2.0
    verdict, note = compare_with_uncertainty(measured, required, u)
    t.verdict = verdict
    t.required = (
        f"Minimum {required:.1f} mm for the net-quantity numeral on a PDP of "
        f"{area:.0f} cm2"
        + (" (blown-moulded container)" if is_blown else "")
        + f" under {rule_source_desc}."
    )
    t.evidence_provenance = _format_visual_provenance(
        ctx,
        target_field="net_quantity",
        extra={
            "pdp_area_cm2": area,
            "measured_value_mm": measured,
            "required_min_mm": required,
            "uncertainty_mm": u,
            "rule_id": rule_id,
            "rule_pack_version": pack_ver,
            "table_selected": table_used,
        },
    )

    if verdict == "not_assessed":
        t.reason = note
        return t

    from image_processor import format_measurement
    t.observed = (
        "Net-quantity numeral height is "
        + format_measurement(measured, u, required)
        + f" (calibration: {ctx.scale_source})."
    )
    if verdict == "fail":
        t.limb = "36(2)"
        t.remediation = rule.remediation if rule else f"Increase net quantity numeral height to at least {required:.1f} mm."
    return t


# ---------------------------------------------------------------------------
# 3. Rule 7(3): Character Width (CHK07 / RULE_LMPC_07)
# ---------------------------------------------------------------------------

def evaluate_character_width(ctx: CheckContext) -> FindingResult:
    """Deterministic evaluation of character width ratio under Rule 7(3) (CHK07)."""
    pack = load_rule_pack()
    as_at = getattr(ctx, "rules_as_at", None)
    rule = pack.get_rule_by_id("CHK07", as_at=as_at)
    rule_id = rule.rule_id if rule else "RULE_LMPC_07_CHARACTER_WIDTH"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK07",
        "Character width at least one-third of height",
        "pass",
        "minor",
        citation=cite("R7-3", "Rule 7(3), character width"),
        ledger_ref="L-07",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    if rule is None:
        t.verdict = "not_assessed"
        t.reason = f"No applicable rule found for CHK07 on inspection date {as_at.isoformat() if as_at else 'current'}."
        return t

    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK07 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()}."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    if getattr(ctx, "phase3_halted", False) or getattr(ctx, "is_medical_device", False):
        t.verdict = "not_assessed"
        t.reason = "Not assessed: medical device, labelled under Medical Devices Rules 2017."
        return t

    if not ctx.measured_widths_mm:
        t.verdict = "not_assessed"
        t.reason = "Character widths could not be measured on the rectified panel."
        t.evidence_provenance = _format_visual_provenance(ctx)
        return t

    offenders = []
    for label, width in ctx.measured_widths_mm.items():
        if label and all(c in NARROW_GLYPHS for c in label.strip()):
            continue  # narrow glyphs 1, i, I, l are exempt
        height = ctx.measured_heights_mm.get(label)
        if height and width > 0.05 and width < (height * WIDTH_RATIO):
            offenders.append(f"'{label}' ({width:.2f} mm wide vs {height:.2f} mm high, ratio {width/height:.2f})")

    t.evidence_provenance = _format_visual_provenance(
        ctx,
        extra={"measured_widths_mm": ctx.measured_widths_mm},
    )

    if offenders:
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = "Non-compliant narrow characters: " + "; ".join(offenders) + "."
        t.required = (
            "Character width must be at least one-third (1/3) of character height "
            "under Rule 7(3), excluding narrow glyphs (1, i, I, l)."
        )
        t.remediation = rule.remediation if rule else "Ensure printed declaration fonts have width >= 1/3 of letter height."
        return t

    t.verdict = "pass"
    t.observed = "All measured characters meet the one-third width ratio under Rule 7(3)."
    return t


# ---------------------------------------------------------------------------
# 4. Rule 8: Principal Display Panel Placement (CHK22 / RULE_LMPC_22)
# ---------------------------------------------------------------------------

def evaluate_pdp_placement(ctx: CheckContext) -> FindingResult:
    """Deterministic evaluation of declaration placement on PDP under Rule 8 (CHK22).

    Audit safeguard: Does NOT equate panel == 'front' with PDP.
    Requires affirmative evidence/geometry establishing the PDP surface.
    """
    pack = load_rule_pack()
    as_at = getattr(ctx, "rules_as_at", None)
    rule = pack.get_rule_by_id("CHK22", as_at=as_at)
    rule_id = rule.rule_id if rule else "RULE_LMPC_22_PDP_PLACEMENT"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK22",
        "Placement of mandatory declarations on Principal Display Panel",
        "pass",
        "major",
        citation=cite("R8-pdp", "Rule 8, declarations on principal display panel"),
        ledger_ref="L-08",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    if rule is None:
        t.verdict = "not_assessed"
        t.reason = f"No applicable rule found for CHK22 on inspection date {as_at.isoformat() if as_at else 'current'}."
        return t

    if as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK22 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()}."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    # 1. Affirmative PDP determination from geometry/evidence
    # Do NOT equate panel == 'front' with principal_display_panel == true.
    # A declaration should pass placement only when the system has evidence establishing
    # that the relevant surface is the applicable principal display panel.
    # A front-camera capture may be evidence of the PDP, but the label string alone must not establish that fact.
    established_pdp = getattr(ctx, "pdp_panel_id", None) or getattr(ctx, "established_pdp_panel", None)
    pdp_verified = getattr(ctx, "pdp_surface_established", False) or getattr(ctx, "pdp_detected", False)
    panels = getattr(ctx, "panels_captured", set()) or set()

    # If PDP panel is not explicitly identified by name, verify if affirmative geometry/evidence established a unique surface
    if not established_pdp and pdp_verified:
        if "principal" in panels:
            established_pdp = "principal"
        elif len(panels) == 1 and getattr(ctx, "pdp_surface_established", False):
            established_pdp = next(iter(panels))

    # If PDP cannot be established from evidence and geometry: NOT_ASSESSED
    if not pdp_verified or not established_pdp:
        t.verdict = "not_assessed"
        t.reason = (
            "Principal display panel could not be established from available evidence and geometry. "
            "A panel label string alone (such as 'front') does not establish the Principal Display Panel."
        )
        t.evidence_provenance = _format_visual_provenance(
            ctx,
            extra={
                "pdp_verified": pdp_verified,
                "established_pdp": established_pdp,
                "panel_identity": list(panels) if panels else [],
                "pdp_determination": getattr(ctx, "pdp_determination_method", None),
                "coordinate_mapping": getattr(ctx, "coordinate_mapping_verified", None),
            },
        )
        return t

    # 2. Check placement of PDP-mandatory declarations (Net Quantity and MRP)
    llm_res = getattr(ctx, "llm_result", None)
    if not llm_res:
        t.verdict = "not_assessed"
        t.reason = "Declaration provenance is unavailable to verify panel placement coordinates."
        t.evidence_provenance = _format_visual_provenance(
            ctx,
            extra={
                "established_pdp": established_pdp,
                "pdp_verified": pdp_verified,
                "panel_identity": list(panels) if panels else [],
            },
        )
        return t

    nq_prov = getattr(llm_res.net_quantity, "provenance", None)
    mrp_prov = getattr(llm_res.mrp, "provenance", None)

    offenders = []
    # Net quantity must appear on the principal display panel under Rule 8 & Rule 6(1)
    if nq_prov and nq_prov.source_panel:
        if nq_prov.source_panel != established_pdp and nq_prov.source_panel not in ("principal", established_pdp):
            offenders.append(
                f"Net quantity was placed on '{nq_prov.source_panel}' panel instead of PDP (verified PDP is '{established_pdp}')"
            )

    # If coordinates are available, check placement inside panel bounds
    if getattr(ctx, "declaration_outside_pdp", False):
        offenders.append("Declaration coordinates lie outside the verified Principal Display Panel boundaries")

    t.evidence_provenance = _format_visual_provenance(
        ctx,
        extra={
            "established_pdp": established_pdp,
            "pdp_verified": pdp_verified,
            "panel_identity": list(panels) if panels else [],
            "pdp_determination": getattr(ctx, "pdp_determination_method", "geometric_surface_affirmation"),
            "coordinate_mapping": getattr(ctx, "coordinate_mapping_verified", True),
            "net_quantity_panel": nq_prov.source_panel if nq_prov else None,
            "mrp_panel": mrp_prov.source_panel if mrp_prov else None,
        },
    )

    if offenders:
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = "; ".join(offenders) + "."
        t.required = "Rule 8 requires mandatory declarations (specifically net quantity) to appear on the Principal Display Panel."
        t.remediation = rule.remediation if rule else "Affix net quantity declaration on the Principal Display Panel."
        return t

    t.verdict = "pass"
    t.observed = f"Mandatory declarations are correctly positioned on the established Principal Display Panel ('{established_pdp}') under Rule 8."
    return t


# ---------------------------------------------------------------------------
# 5. Rule 8: Quantity Clear Space (CHK09 / RULE_LMPC_09)
# ---------------------------------------------------------------------------

def evaluate_clear_space(ctx: CheckContext) -> FindingResult:
    """Deterministic evaluation of clear space around net quantity declaration under Rule 8 (CHK09)."""
    pack = load_rule_pack()
    as_at = getattr(ctx, "rules_as_at", None)
    rule = pack.get_rule_by_id("CHK09", as_at=as_at)
    rule_id = rule.rule_id if rule else "RULE_LMPC_09_CLEAR_SPACE"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK09",
        "Clear space around the net-quantity declaration",
        "pass",
        "minor",
        citation=cite("R8", "Rule 8, clear space around the net-quantity declaration"),
        ledger_ref="L-09",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    if rule is None:
        t.verdict = "not_assessed"
        t.reason = f"No applicable rule found for CHK09 on inspection date {as_at.isoformat() if as_at else 'current'}."
        return t

    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK09 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()}."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    if getattr(ctx, "phase3_halted", False) or getattr(ctx, "is_medical_device", False):
        t.verdict = "not_assessed"
        t.reason = "Not assessed: medical device, labelled under Medical Devices Rules 2017."
        return t

    height = ctx.measured_heights_mm.get("net_quantity")
    if height is None:
        t.verdict = "not_assessed"
        t.reason = "Net quantity character height was not measured, so Rule 8 clear space multiples cannot be calculated."
        t.evidence_provenance = _format_visual_provenance(ctx, target_field="net_quantity")
        return t

    if not ctx.clear_space_mm:
        t.verdict = "not_assessed"
        t.reason = (
            "The net-quantity declaration and the surrounding space could not both "
            "be measured, so Rule 8 cannot be applied."
        )
        t.evidence_provenance = _format_visual_provenance(ctx, target_field="net_quantity")
        return t

    # Statutory multiples: 1.0x height vertical, 2.0x height horizontal
    need = {
        "above": height * 1.0,
        "below": height * 1.0,
        "left": height * 2.0,
        "right": height * 2.0,
    }
    u = (ctx.mm_per_pixel_uncertainty or 0.0) * 2.0
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

    t.evidence_provenance = _format_visual_provenance(
        ctx,
        target_field="net_quantity",
        extra={
            "clear_space_mm": ctx.clear_space_mm,
            "required_multiples": need,
            "uncertainty_mm": u,
        },
    )

    if short:
        t.verdict = "fail"
        t.limb = "36(2)"
        t.observed = "; ".join(short) + "."
        t.required = (
            "Clear space surrounding net quantity must equal at least 1x character height "
            "above/below and 2x character height left/right under Rule 8."
        )
        t.remediation = rule.remediation if rule else "Maintain free clear space on all sides of net quantity declaration."
        return t

    if inconclusive:
        t.verdict = "not_assessed"
        t.reason = (
            "Clear space on the "
            + ", ".join(inconclusive)
            + " side(s) could not be determined within measurement uncertainty."
        )
        return t

    # Anti-False-Compliance Safeguard (Section 14)
    # OCR bounding boxes only represent text the OCR engine detected.
    # If image evidence cannot verify the absence of interfering decorative print, graphics, or logos:
    verified_clean = getattr(ctx, "clear_space_image_verified", None)
    if verified_clean is False:
        t.verdict = "not_assessed"
        t.reason = (
            "OCR detected no neighboring text, but image evidence cannot confirm "
            "the absence of interfering decorative printing, graphics, or artwork in the clear space."
        )
        return t

    if verified_clean is None and not getattr(ctx, "verify_clear_space_image", False):
        # When explicit image-level graphics verification was not performed,
        # preserve honesty about limitation (Section 14)
        t.verdict = "not_assessed"
        t.reason = (
            "OCR detected no neighboring text lines within required margins, but "
            "available image evidence is insufficient to verify absence of decorative graphics or artwork."
        )
        return t

    t.verdict = "pass"
    t.observed = "Clear space on all four sides meets Rule 8 multipliers with absence of interfering print verified."
    return t


# ---------------------------------------------------------------------------
# 6. Rule 9: Conspicuous Contrast (CHK08 / RULE_LMPC_08)
# ---------------------------------------------------------------------------

def evaluate_conspicuous_contrast(ctx: CheckContext) -> FindingResult:
    """Deterministic evaluation of conspicuous contrast under Rule 9(1) (CHK08).

    Audit safeguard: Does NOT return PASS merely because metadata flags container as moulded.
    PASS requires affirmative verification that declaration itself is blown/formed/moulded on glass/plastic surface.
    """
    pack = load_rule_pack()
    as_at = getattr(ctx, "rules_as_at", None)
    rule = pack.get_rule_by_id("CHK08", as_at=as_at)
    rule_id = rule.rule_id if rule else "RULE_LMPC_08_CONSPICUOUS_CONTRAST"
    pack_ver = pack.rule_pack_version

    t = FindingResult(
        "CHK08",
        "Declarations contrast conspicuously with the background",
        "pass",
        "minor",
        citation=cite("R9", "Rule 9, conspicuous contrast"),
        ledger_ref="L-10",
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    if rule is None:
        t.verdict = "not_assessed"
        t.reason = f"No applicable rule found for CHK08 on inspection date {as_at.isoformat() if as_at else 'current'}."
        return t

    if rule and as_at and not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK08 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()}."
        return t

    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    if getattr(ctx, "ocr_failure_reason", None):
        t.verdict = "not_assessed"
        t.reason = ctx.ocr_failure_reason
        return t

    if getattr(ctx, "phase3_halted", False) or getattr(ctx, "is_medical_device", False):
        t.verdict = "not_assessed"
        t.reason = "Not assessed: medical device, labelled under Medical Devices Rules 2017."
        return t

    # Proviso Exception Check (Section 17): Blown, formed, or moulded surface
    # DO NOT return PASS merely because container might be moulded.
    # PASS only when the applicable exception is sufficiently established by inspection evidence/context.
    is_blown_container = getattr(ctx, "is_blown_moulded", False)
    blown_verified = getattr(ctx, "blown_moulded_inscribed_verified", False)
    medium = getattr(ctx, "declaration_medium", None)
    mat = getattr(ctx, "surface_material", None)
    surface_exception_established = (
        blown_verified
        or (is_blown_container and medium in {"blown", "formed", "moulded", "embossed", "debossed"})
        or (mat in {"glass", "plastic"} and medium in {"blown", "formed", "moulded", "embossed", "debossed"})
    )

    if surface_exception_established:
        t.verdict = "pass"
        t.observed = (
            "Information is verified as blown, formed or moulded on container surface; "
            "colour contrast requirement is exempted under Rule 9(1) proviso."
        )
        t.evidence_provenance = _format_visual_provenance(
            ctx,
            extra={
                "surface_exception_established": True,
                "surface_material": mat,
                "declaration_medium": medium,
            },
        )
        return t

    # If container is moulded but declaration medium is unverified and contrast is not measurable
    contrast_ratio = getattr(ctx, "contrast_ratio", None)
    eng_signal = getattr(ctx, "engineering_contrast_signal", {}) or {}

    if is_blown_container and not surface_exception_established and contrast_ratio is None and not eng_signal:
        t.verdict = "not_assessed"
        t.reason = (
            "Container is recorded as blown/moulded, but inspection evidence does not establish "
            "whether the declaration itself is directly inscribed on the surface or printed on a label. "
            "Rule 9(1) proviso exemption cannot be certified without verified declaration medium."
        )
        t.evidence_provenance = _format_visual_provenance(ctx, extra={"is_blown_moulded": True})
        return t

    t.evidence_provenance = _format_visual_provenance(
        ctx,
        extra={
            "contrast_ratio": contrast_ratio,
            "engineering_signal": eng_signal,
        },
    )

    if contrast_ratio is None and not eng_signal:
        t.verdict = "not_assessed"
        t.reason = (
            "Declaration contrast could not be measured from available image pixels. "
            "Visual inspection is required to determine whether the declarations contrast conspicuously."
        )
        return t

    # Critical Legal Contrast Requirement (Section 16):
    # Rule 9 requires declarations to "contrast conspicuously" without prescribing a universal numeric ratio.
    statutory_threshold = None
    if rule and rule.evaluation_method and isinstance(rule.evaluation_method.parameters, dict):
        statutory_threshold = rule.evaluation_method.parameters.get("statutory_threshold")

    if statutory_threshold is not None and contrast_ratio is not None:
        if contrast_ratio >= float(statutory_threshold):
            t.verdict = "pass"
            t.observed = f"Measured contrast ratio {contrast_ratio:.2f}:1 meets the statutory threshold of {statutory_threshold}:1."
            return t
        else:
            t.verdict = "fail"
            t.limb = "36(1)"
            t.observed = f"Measured contrast ratio {contrast_ratio:.2f}:1 falls below required threshold of {statutory_threshold}:1."
            t.remediation = rule.remediation if rule else "Increase color contrast between declaration text and background."
            return t

    # Where no statutory numeric cutoff is defined in the gazetted law (Section 16):
    # Report the engineering measurement honestly as NOT_ASSESSED with engineering signal.
    # Never invent a fake legal standard.
    t.verdict = "not_assessed"
    t.reason = (
        f"Luminance contrast signal was measured ({contrast_ratio:.2f}:1 ratio), but no "
        f"legally defined numeric threshold is available in Rule 9 for automatic PASS/FAIL. "
        f"Visual inspection is required."
    )
    t.observed = f"Engineering luminance contrast ratio measured as {contrast_ratio:.2f}:1."
    return t


# ---------------------------------------------------------------------------
# 7. Rule 6(4A)(d) / Historical Rule 6(8): Origin Marking for Cosmetics & Toiletries (CHK23)
# ---------------------------------------------------------------------------

COSMETICS_TOILETRIES_CATEGORIES = {
    "soap", "soaps",
    "shampoo", "shampoos",
    "toothpaste", "toothpastes", "tooth_paste", "tooth_pastes",
    "cosmetic", "cosmetics",
    "toiletries", "toiletry",
    "personal_care",
    "skin_care",
    "hair_care",
    "body_wash",
    "face_wash",
    "lotion",
    "creams",
}

FOURTH_AMENDMENT_EFFECTIVE_DATE = date(2026, 9, 21)


def evaluate_origin_marking(ctx: CheckContext) -> FindingResult:
    """Deterministic evaluation of Origin Marking (green/red/brown dot) under Rule 6(4A)(d) / former Rule 6(8) (CHK23).

    G.S.R. 826(E) dated 21.09.2026:
    - Substituted/relocated former Rule 6(8) into Rule 6(4A)(d) w.e.f 21.09.2026.
    - Applies strictly to soap, shampoo, toothpaste, other cosmetics, and toiletries.
    - Vegetarian origin: green dot at top of PDP.
    - Non-vegetarian origin: red or brown dot at top of PDP.

    Critical Legal Interpretation (Section 3):
    Parent sub-rule 6(4A) introductory wording is permissive ('Nothing in these rules shall preclude...').
    The engine NEVER converts this statutory ambiguity into a false criminal Section 36(1) violation.
    Missing origin symbols on post-amendment inspections evaluate to not_assessed (MANUAL_REVIEW),
    requiring human Metrology Officer adjudication rather than automated prosecution.
    """
    as_at = getattr(ctx, "rules_as_at", None) or date(2026, 7, 1)
    pack = getattr(ctx, "rule_pack", None) or load_rule_pack("2026.09.v1")
    rule = pack.get_rule_by_id("CHK23", as_at=as_at)

    rule_id = rule.rule_id if rule else "RULE_LMPC_23_ORIGIN_MARKING_COSMETICS"
    pack_ver = pack.rule_pack_version
    source_rule = rule.source_rule if rule else "Rule 6(4A)(d)"

    t = FindingResult(
        "CHK23",
        "Origin marking (veg/non-veg dot) on cosmetics and toiletries",
        "pass",
        severity=rule.severity if rule else "advisory",
        citation=cite("R6-4A-d", f"{source_rule}, origin marking on cosmetics/toiletries"),
        rule_id=rule_id,
        rule_pack_version=pack_ver,
        evaluation_timestamp=_now_iso(),
    )

    if rule is None:
        t.verdict = "not_assessed"
        t.reason = f"No applicable rule found for CHK23 on inspection date {as_at.isoformat()}."
        return t

    # 1. Effective date check
    if not rule.is_effective_on(as_at):
        t.verdict = "not_assessed"
        t.reason = f"Rule CHK23 ({rule.rule_id}) is not effective on inspection date {as_at.isoformat()} (effective from {rule.effective_from} to {rule.effective_to or 'indefinite'})."
        return t

    # 2. Halt check
    if getattr(ctx, "halted", None):
        t.verdict = "not_assessed"
        t.reason = getattr(ctx, "halt_reason", "Assessment halted")
        return t

    # 3. Category Applicability (Section 8)
    raw_cat = (
        getattr(ctx, "commodity_category", None)
        or getattr(ctx, "product_category", None)
        or getattr(ctx, "commodity_generic", None)
    )
    if not raw_cat or not str(raw_cat).strip():
        t.verdict = "not_assessed"
        t.reason = "Product category could not be established reliably from evidence; cannot determine applicability of Rule 6(4A)(d)."
        return t

    cat_norm = str(raw_cat).strip().lower().replace("-", "_").replace(" ", "_")
    is_applicable_category = (
        cat_norm in COSMETICS_TOILETRIES_CATEGORIES
        or any(
            c in cat_norm
            for c in (
                "soap", "shampoo", "toothpaste", "cosmetic", "toiletry",
                "toiletries", "lotion", "cream", "skin_care", "hair_care",
                "body_wash", "face_wash", "personal_care",
            )
        )
    )

    if not is_applicable_category:
        t.verdict = "pass"
        t.observed = f"Commodity '{raw_cat}' is not soap, shampoo, toothpaste, cosmetic, or toiletry; origin marking under Rule 6(4A)(d) does not apply."
        return t

    # 4. Product Origin Verification (Section 9)
    # Origin must be grounded in structured context; do NOT guess from product name or visual color!
    raw_origin = getattr(ctx, "product_origin", None)
    if not raw_origin or str(raw_origin).strip().lower() in ("unknown", "unspecified", "none"):
        t.verdict = "not_assessed"
        t.reason = "Product origin (vegetarian vs non-vegetarian) is unknown in evidence; origin symbol cannot be evaluated without verified origin."
        return t

    origin_norm = str(raw_origin).strip().lower()
    is_veg = origin_norm in ("vegetarian", "veg", "green")
    is_non_veg = origin_norm in ("non_vegetarian", "non_veg", "non-vegetarian", "brown", "red")

    if not is_veg and not is_non_veg:
        t.verdict = "not_assessed"
        t.reason = f"Product origin '{raw_origin}' is unrecognized; expected 'vegetarian' or 'non_vegetarian'."
        return t

    # 5. PDP Affirmative Evidence Check (Section 12)
    pdp_established = getattr(ctx, "pdp_surface_established", False) or getattr(ctx, "pdp_detected", False)
    established_panel = getattr(ctx, "established_pdp_panel", None) or getattr(ctx, "pdp_panel_id", None)

    if not pdp_established:
        t.verdict = "not_assessed"
        t.reason = "Principal Display Panel (PDP) was not affirmatively established; cannot verify placement at top of PDP under Rule 6(4A)(d)."
        return t

    # 6. Visual Evidence & Placement Extraction (Section 10 & 11)
    symbol_detected = getattr(ctx, "origin_symbol_detected", None)
    symbol_panel = getattr(ctx, "origin_symbol_panel", None)
    symbol_image_id = getattr(ctx, "origin_symbol_image_id", None)
    symbol_bbox = getattr(ctx, "origin_symbol_bbox", None)
    symbol_colour = getattr(ctx, "origin_symbol_colour", None)
    symbol_placement = getattr(ctx, "origin_symbol_placement", None)
    eng_signal = getattr(ctx, "origin_symbol_engineering_signal", None)

    # Attach evidence provenance
    t.evidence_provenance = {
        "source_panel": symbol_panel,
        "source_image_id": symbol_image_id,
        "source_bbox": symbol_bbox,
        "observed_colour": symbol_colour,
        "placement": symbol_placement,
        "engineering_signal": eng_signal,
    }

    # Case A: Symbol confirmed detected with evidence
    if symbol_detected:
        # Check PDP panel
        if established_panel and symbol_panel and symbol_panel != established_panel:
            t.verdict = "fail"
            t.observed = f"Origin symbol detected on panel '{symbol_panel}', not on established PDP ('{established_panel}')."
            t.required = "Origin symbol must be placed on the Principal Display Panel under Rule 6(4A)(d)."
            t.remediation = "Display the origin symbol on the Principal Display Panel."
            return t

        # Check top of PDP placement
        if symbol_placement is not None:
            if symbol_placement not in ("top_pdp", "top_of_pdp", "top"):
                t.verdict = "fail"
                t.observed = f"Origin symbol is placed at '{symbol_placement}', not at the top of the Principal Display Panel."
                t.required = "Origin symbol must be displayed at the top of the Principal Display Panel under Rule 6(4A)(d)."
                t.remediation = "Relocate the origin symbol to the top of the Principal Display Panel."
                return t
        else:
            t.verdict = "not_assessed"
            t.reason = "Origin symbol was detected, but its placement relative to the top of the PDP could not be verified."
            return t

        # Check colour signal (Section 11)
        if not symbol_colour or str(symbol_colour).strip().lower() in ("unknown", "uncertain"):
            t.verdict = "not_assessed"
            t.reason = "Symbol colour could not be defensibly classified from engineering colour signal without arbitrary thresholding."
            return t

        col_norm = str(symbol_colour).strip().lower()
        if is_veg:
            if col_norm in ("green", "green_dot"):
                t.verdict = "pass"
                t.observed = "Green dot origin symbol verified at top of Principal Display Panel for vegetarian product."
                return t
            elif col_norm in ("red", "brown", "red_dot", "brown_dot"):
                t.verdict = "fail"
                t.observed = f"Vegetarian product displays {col_norm} dot instead of required green dot."
                t.required = "Products of vegetarian origin must display a green dot at the top of the PDP."
                t.remediation = "Replace symbol with green dot for vegetarian origin."
                return t
            else:
                t.verdict = "not_assessed"
                t.reason = f"Observed symbol colour '{symbol_colour}' cannot be defensibly evaluated under vegetarian green-dot requirement."
                return t
        else:  # is_non_veg
            if col_norm in ("red", "brown", "red_dot", "brown_dot"):
                t.verdict = "pass"
                t.observed = f"{col_norm.replace('_', ' ').capitalize()} origin symbol verified at top of Principal Display Panel for non-vegetarian product."
                return t
            elif col_norm in ("green", "green_dot"):
                t.verdict = "fail"
                t.observed = "Non-vegetarian product displays green dot instead of required red or brown dot."
                t.required = "Products of non-vegetarian origin must display a red or brown dot at the top of the PDP."
                t.remediation = "Replace symbol with red or brown dot for non-vegetarian origin."
                return t
            else:
                t.verdict = "not_assessed"
                t.reason = f"Observed symbol colour '{symbol_colour}' cannot be defensibly evaluated under non-vegetarian red/brown dot requirement."
                return t

    # Case B: Symbol is missing or not detected
    # Section 3 Critical Legal-Interpretation Requirement:
    # Under post-2026-09-21 Fourth Amendment (Rule 6(4A)(d)), the requirement is housed under a
    # permissive non-preclusion clause ("Nothing in these rules shall preclude...").
    # The statutory consequence of an omitted symbol is ambiguous; NEVER convert to automatic FAIL.
    if as_at >= FOURTH_AMENDMENT_EFFECTIVE_DATE:
        t.verdict = "not_assessed"
        t.limb = None  # No Section 36 offence limb manufactured
        t.reason = (
            "Origin symbol (green/red/brown dot) not observed on cosmetics/toiletries package. "
            "Rule 6(4A)(d) is housed under a permissive non-preclusion provision ('Nothing in these rules shall preclude...'). "
            "Statutory enforceability under Section 36(1) is legally ambiguous; flagged for MANUAL_REVIEW by Metrology Officer."
        )
        t.remediation = (
            "Review package under G.S.R. 826(E) and Department of Consumer Affairs guidelines. "
            "Determine whether origin declaration is required or advised for this commodity."
        )
        return t

    # For historical dates (< 2026-09-21) governed by former Rule 6(8):
    # Rule 6(8) had affirmative language ("Every package containing soap... shall bear...").
    panels = getattr(ctx, "panels_captured", set()) or set()
    if len(panels) >= 2 and pdp_established:
        t.verdict = "fail"
        t.limb = "36(1)"
        t.observed = f"No origin symbol (green dot / red or brown dot) observed on {raw_cat} package."
        t.required = "Under former Rule 6(8), packages containing soap, shampoo, toothpaste, cosmetics, or toiletries must bear a green dot (vegetarian) or red/brown dot (non-vegetarian) at the top of PDP."
        t.remediation = "Display green dot for vegetarian origin or red/brown dot for non-vegetarian origin at the top of the Principal Display Panel."
        return t
    else:
        t.verdict = "not_assessed"
        t.reason = "Origin symbol was not observed; inspection panel coverage is incomplete."
        t.remediation = "Capture all package panels to verify presence of origin symbol."
        return t


# ---------------------------------------------------------------------------
# Master Visual Evaluation Pipeline
# ---------------------------------------------------------------------------

def evaluate_visual_and_geometry_rules(
    ctx: CheckContext,
    include_origin_marking: bool = False,
) -> list[FindingResult]:
    """Execute all visual and geometry compliance evaluations deterministically (Phase 4C & GSR 826(E))."""
    findings = [
        evaluate_character_height(ctx),        # CHK06 (Rule 7 Table-I)
        evaluate_net_quantity_height(ctx),     # CHK06b (Rule 7 Table-II)
        evaluate_character_width(ctx),         # CHK07 (Rule 7(3))
        evaluate_conspicuous_contrast(ctx),    # CHK08 (Rule 9(1))
        evaluate_clear_space(ctx),             # CHK09 (Rule 8 clear space)
        evaluate_pdp_placement(ctx),           # CHK22 (Rule 8 PDP placement)
    ]
    if include_origin_marking:
        findings.append(evaluate_origin_marking(ctx))
    return findings


# ---------------------------------------------------------------------------
# Performance Benchmarking (Section 23)
# ---------------------------------------------------------------------------

def benchmark_phase4c_performance(iterations: int = 50) -> dict[str, Any]:
    """Measures actual latency separately for all Phase 4C visual/geometry components (Section 23).

    Zero OCR calls. Zero LLM calls. Measures:
    1. panel geometry retrieval
    2. coordinate transformation
    3. character measurement
    4. clear-space evaluation
    5. contrast measurement
    6. overall Phase 4C evaluation
    """
    rss_mb = 0.0
    try:
        import ctypes
        from ctypes import wintypes
        class PMC(ctypes.Structure):
            _fields_ = [
                ('cb', wintypes.DWORD), ('PageFaultCount', wintypes.DWORD),
                ('PeakWorkingSetSize', ctypes.c_size_t), ('WorkingSetSize', ctypes.c_size_t),
                ('QuotaPeakPagedPoolUsage', ctypes.c_size_t), ('QuotaPagedPoolUsage', ctypes.c_size_t),
                ('QuotaPeakNonPagedPoolUsage', ctypes.c_size_t), ('QuotaNonPagedPoolUsage', ctypes.c_size_t),
                ('PagefileUsage', ctypes.c_size_t), ('PeakPagefileUsage', ctypes.c_size_t),
            ]
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        psapi = ctypes.windll.psapi
        psapi.GetProcessMemoryInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        psapi.GetProcessMemoryInfo.restype = wintypes.BOOL
        if psapi.GetProcessMemoryInfo(ctypes.windll.kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            rss_mb = pmc.PeakWorkingSetSize / (1024 * 1024)
    except Exception:
        try:
            import resource
            rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
        except Exception:
            rss_mb = 0.0

    # Generate synthetic derived test artifact (1600x1200)
    img = np.full((1200, 1600, 3), 240, dtype=np.uint8)
    cv2.putText(img, "NET QTY: 500 g", (200, 400), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 2)
    cv2.putText(img, "MRP Rs. 150.00", (200, 500), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (20, 20, 20), 2)

    bbox_nq = (200, 360, 520, 410)
    homography = np.eye(3, dtype=np.float32)

    t_geom = []
    t_coord = []
    t_glyph = []
    t_clear = []
    t_contrast = []
    t_overall = []

    ctx = CheckContext(
        panel_shape="rectangular",
        panel_height_mm=120.0,
        panel_width_mm=100.0,
        mm_per_pixel=0.08,
        mm_per_pixel_uncertainty=0.002,
        scale_source="validated_reference",
        measured_heights_mm={"net_quantity": 3.2, "mrp": 3.2},
        measured_widths_mm={"net_quantity": 1.6, "mrp": 1.6},
        clear_space_mm={"above": 4.0, "below": 4.0, "left": 8.0, "right": 8.0},
        contrast_ratio=8.5,
        panels_captured={"front"},
        clear_space_image_verified=True,
    )

    for _ in range(iterations):
        # 1. Panel geometry retrieval
        t0 = time.perf_counter()
        _area, _ = pdp_area_cm2(ctx)
        t_geom.append((time.perf_counter() - t0) * 1000.0)

        # 2. Coordinate transformation
        t0 = time.perf_counter()
        reconcile_coordinates(bbox_nq, "analysis_image", (1200, 1600), homography=homography)
        t_coord.append((time.perf_counter() - t0) * 1000.0)

        # 3. Character measurement
        t0 = time.perf_counter()
        measure_glyph_height_mm(img, bbox_nq, 0.08, 0.002)
        t_glyph.append((time.perf_counter() - t0) * 1000.0)

        # 4. Clear-space evaluation
        t0 = time.perf_counter()
        verify_clear_space_image(img, bbox_nq, 40.0)
        t_clear.append((time.perf_counter() - t0) * 1000.0)

        # 5. Contrast measurement
        t0 = time.perf_counter()
        measure_conspicuous_contrast_signal(img, bbox_nq)
        t_contrast.append((time.perf_counter() - t0) * 1000.0)

        # 6. Overall Phase 4C evaluation
        t0 = time.perf_counter()
        evaluate_visual_and_geometry_rules(ctx)
        t_overall.append((time.perf_counter() - t0) * 1000.0)


    def stats(arr: list[float]) -> dict[str, float]:
        return {
            "mean_ms": round(float(np.mean(arr)), 3),
            "p50_ms": round(float(np.median(arr)), 3),
            "p95_ms": round(float(np.percentile(arr, 95)), 3),
        }

    return {
        "iterations": iterations,
        "panel_geometry_latency": stats(t_geom),
        "coordinate_transform_latency": stats(t_coord),
        "character_measurement_latency": stats(t_glyph),
        "clear_space_evaluation_latency": stats(t_clear),
        "contrast_measurement_latency": stats(t_contrast),
        "overall_phase4c_latency": stats(t_overall),
        "peak_rss_mb": round(rss_mb, 2),
        "rules_evaluated_count": 6,
    }
