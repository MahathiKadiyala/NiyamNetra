"""Backend/scripts/smoke_ocr_provider.py — Safe Real-Provider OCR Smoke Test.

Module 7.4 (SIH26034 release gate):
- Verifies that configured cloud OCR providers can read a packaged-product label.
- Reuses existing OCR provider factory (get_ocr_provider) and fallback chain (FallbackOCRProvider).
- Requires explicit opt-in via '--live-provider-call' before making external requests.
- Performs ZERO database writes and creates NO inspection, scan, or finding records.
- Strictly scrubs API keys, bearer tokens, and avoids printing raw OCR text.
- Returns nonzero exit code when all providers fail or no usable text is extracted.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

# Ensure Backend directory is on sys.path when executed directly as a script
_CURRENT_DIR = Path(__file__).resolve().parent
_BACKEND_DIR = _CURRENT_DIR.parent
if str(_BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(_BACKEND_DIR))

from config import settings
from ocr.base import (
    STATUS_AUTH_ERROR,
    STATUS_INPUT_UNAVAILABLE,
    STATUS_INVALID_RESPONSE,
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    OcrResult,
)
import ocr.factory
from ocr.security import sanitize_sensitive_text


def run_smoke_ocr(
    image_path: str | Path,
    live_provider_call: bool = False,
    provider_name: str | None = None,
    options: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    """Execute the OCR smoke test against an image file.

    Parameters:
    -----------
    image_path: Path to the target packaging image file.
    live_provider_call: Must be True to permit external cloud API requests.
    provider_name: Optional override ('auto', 'google_vision', 'ocr_space', 'azure').
    options: Optional provider options dictionary.

    Returns:
    --------
    tuple(exit_code: int, summary: dict[str, Any])
    Exit codes:
      0: Success (valid text extracted by configured provider)
      1: Failure (provider error, all providers failed, or zero usable text)
      2: Blocked (explicit '--live-provider-call' flag missing)
    """
    path = Path(image_path).resolve()

    # 1. Image path validation
    if not path.exists():
        err_msg = f"Target image file does not exist: {path}"
        return 1, {
            "status": STATUS_INPUT_UNAVAILABLE,
            "success": False,
            "error": err_msg,
            "file_path": str(path),
        }

    if not path.is_file():
        err_msg = f"Target image path is not a file: {path}"
        return 1, {
            "status": STATUS_INPUT_UNAVAILABLE,
            "success": False,
            "error": err_msg,
            "file_path": str(path),
        }

    # 2. Image integrity check
    img_bytes = path.read_bytes()
    w_px: int | None = None
    h_px: int | None = None
    try:
        from PIL import Image
        with Image.open(path) as im:
            w_px, h_px = im.width, im.height
            im_format = im.format
    except Exception as exc:
        return 1, {
            "status": STATUS_INPUT_UNAVAILABLE,
            "success": False,
            "error": f"Invalid or unreadable image format: {type(exc).__name__}",
            "file_path": str(path),
        }

    # 3. Explicit opt-in safety check
    if not live_provider_call:
        return 2, {
            "status": "BLOCKED_OPT_IN_REQUIRED",
            "success": False,
            "error": (
                "Live cloud OCR request was not permitted. "
                "The '--live-provider-call' flag is strictly required to make external network calls."
            ),
            "file_path": str(path),
            "file_size_bytes": len(img_bytes),
            "dimensions": f"{w_px}x{h_px}" if w_px and h_px else "unknown",
        }

    # 4. Resolve provider instance
    target_provider = ocr.factory.get_ocr_provider(provider_name)
    if not target_provider.is_configured():
        return 1, {
            "status": STATUS_UNAVAILABLE,
            "success": False,
            "error": (
                f"OCR provider '{target_provider.name}' has no active credentials configured "
                "in environment (check settings.OCR_PROVIDER_CHAIN)."
            ),
            "provider": target_provider.name,
            "file_path": str(path),
        }

    # 5. Execute OCR recognition
    t0 = time.perf_counter()
    result: OcrResult = target_provider.recognize(
        image_bytes=img_bytes,
        image_width=w_px,
        image_height=h_px,
        options=options,
    )
    duration_ms = round((time.perf_counter() - t0) * 1000, 2)

    # 6. Analyze fallback and sanitize diagnostics
    attempts_trace: list[dict[str, Any]] = []
    fallback_occurred = False
    raw_attempts = getattr(result, "attempts", []) or []

    for idx, att in enumerate(raw_attempts):
        sanitized_entry = {
            "provider": att.get("provider"),
            "status": att.get("status"),
            "category": att.get("category"),
            "duration_ms": att.get("duration_ms"),
            "failure_reason": sanitize_sensitive_text(att.get("failure_reason")),
        }
        attempts_trace.append(sanitized_entry)

    if len(raw_attempts) > 1:
        prior_attempts = raw_attempts[:-1]
        if any(a.get("status") != STATUS_SUCCESS for a in prior_attempts):
            fallback_occurred = True

    resolved_provider = getattr(result, "provider", None) or getattr(result, "engine", None) or "unknown"
    line_count = len(result.lines) if result.lines else 0
    confidence = round(result.mean_confidence, 4) if result.mean_confidence is not None else None
    sanitized_failure = sanitize_sensitive_text(result.failure_reason)

    summary: dict[str, Any] = {
        "status": result.status,
        "success": (result.status == STATUS_SUCCESS and line_count > 0),
        "provider": resolved_provider,
        "fallback_occurred": fallback_occurred,
        "line_count": line_count,
        "mean_confidence": confidence,
        "duration_ms": duration_ms,
        "attempts": attempts_trace,
        "file_path": str(path),
        "file_size_bytes": len(img_bytes),
        "dimensions": f"{w_px}x{h_px}" if w_px and h_px else "unknown",
        "failure_reason": sanitized_failure,
    }

    # 7. Determine exit code
    if summary["success"]:
        return 0, summary
    return 1, summary


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="NiyamNetra Safe Real-Provider OCR Smoke Test (Module 7.4)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "image_path",
        nargs="?",
        default=None,
        help="Path to real packaged product label image file",
    )
    parser.add_argument(
        "--image-path",
        dest="opt_image_path",
        default=None,
        help="Optional explicit flag for image file path",
    )
    parser.add_argument(
        "--live-provider-call",
        action="store_true",
        default=False,
        help="Explicit opt-in required to invoke live external cloud OCR providers",
    )
    parser.add_argument(
        "--provider",
        default=None,
        help="Override OCR provider selection ('auto', 'google_vision', 'ocr_space', 'azure')",
    )
    parser.add_argument(
        "--json",
        dest="json_output",
        action="store_true",
        default=False,
        help="Output results in JSON format",
    )

    args = parser.parse_args(argv)
    target_path = args.opt_image_path or args.image_path

    if not target_path:
        print("[ERROR] Missing target image path. Specify an image file path as an argument.", file=sys.stderr)
        parser.print_help(sys.stderr)
        return 1

    exit_code, summary = run_smoke_ocr(
        image_path=target_path,
        live_provider_call=args.live_provider_call,
        provider_name=args.provider,
    )

    if args.json_output:
        print(json.dumps(summary, indent=2))
        return exit_code

    # Human-readable formatted summary (strictly redacting raw text and credentials)
    print("=" * 70)
    print("NIYAMNETRA CLOUD OCR PROVIDER SMOKE TEST")
    print("=" * 70)
    print(f"Target Image:      {Path(target_path).name} ({summary.get('dimensions', 'unknown')}, {summary.get('file_size_bytes', 0)} bytes)")
    print(f"Live Opt-in Flag:  {'YES' if args.live_provider_call else 'NO (calls blocked)'}")
    print(f"OCR Status:        {summary.get('status')}")
    print(f"Active Provider:   {summary.get('provider', 'none')}")
    print(f"Fallback Occurred: {'YES' if summary.get('fallback_occurred') else 'NO'}")
    print(f"Extracted Lines:   {summary.get('line_count', 0)}")
    conf_str = f"{summary.get('mean_confidence'):.4f}" if summary.get("mean_confidence") is not None else "N/A"
    print(f"Mean Confidence:   {conf_str}")
    print(f"Elapsed Time:      {summary.get('duration_ms', 0.0):.2f} ms")
    print("Database Writes:   0 (no database records created)")

    if summary.get("attempts"):
        print("\nCandidate Execution Trace:")
        for idx, att in enumerate(summary["attempts"], 1):
            stat = att.get("status", "unknown")
            dur = f"{att.get('duration_ms', 0):.1f}ms"
            reason = f" - {att['failure_reason']}" if att.get("failure_reason") else ""
            print(f"  {idx}. {att.get('provider')}: {stat} ({dur}){reason}")

    if summary.get("failure_reason"):
        print(f"\nFailure Reason:    {summary['failure_reason']}")

    print("=" * 70)
    if exit_code == 0:
        print("RESULT: PASS - Real provider successfully extracted text.")
    elif exit_code == 2:
        print("RESULT: BLOCKED - Opt-in flag '--live-provider-call' required to contact cloud providers.")
    else:
        print("RESULT: FAIL - OCR provider chain did not extract usable text.")
    print("=" * 70)

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
