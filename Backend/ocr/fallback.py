"""Backend/ocr/fallback.py — Resilient runtime OCR fallback chain provider.

Features:
- Dynamically iterates over configured, supported OCR providers.
- Bounded retries and deliberate handling of rate limits and transient errors.
- Automatically falls back on HTTP 403 (e.g. billing disabled), 401, 429, 5xx,
  timeouts, and connection errors.
- Skips unconfigured providers safely without attempting API calls.
- Returns real extracted text, provider identity, duration, and status from
  the succeeding fallback provider.
- If all providers fail, returns an explicit structured failure (never masks
  as a silent success).
- Collects a safe diagnostic trace with zero credential leakage.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Sequence

from ocr.base import (
    BaseOCRProvider,
    OcrResult,
    STATUS_AUTH_ERROR,
    STATUS_INVALID_RESPONSE,
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from ocr.security import sanitize_sensitive_text

logger = logging.getLogger("niyamnetra.ocr.fallback")

# Statuses that trigger runtime fallback to next configured provider
FALLBACK_TRIGGER_STATUSES = {
    STATUS_AUTH_ERROR,        # HTTP 401, 403, billing disabled
    STATUS_TIMEOUT,           # Network / HTTP timeout
    STATUS_RATE_LIMITED,      # HTTP 429
    STATUS_PROVIDER_ERROR,    # HTTP 5xx, connection drop, server crash
    STATUS_INVALID_RESPONSE,  # Corrupted or unexpected API response
    STATUS_UNAVAILABLE,       # Provider unreachable or unconfigured
}


def _status_to_category(status: str) -> str:
    categories = {
        STATUS_SUCCESS: "success",
        STATUS_AUTH_ERROR: "auth_error",
        STATUS_TIMEOUT: "timeout",
        STATUS_RATE_LIMITED: "rate_limited",
        STATUS_PROVIDER_ERROR: "provider_error",
        STATUS_INVALID_RESPONSE: "invalid_response",
        STATUS_NO_TEXT: "no_text",
        STATUS_UNAVAILABLE: "unavailable",
        STATUS_SKIPPED: "skipped_unconfigured",
    }
    return categories.get(status, "unknown")


class FallbackOCRProvider(BaseOCRProvider):
    """Orchestrates an ordered fallback chain across configured Cloud OCR providers."""

    name: str = "auto"

    def __init__(
        self,
        candidates: Sequence[BaseOCRProvider] | None = None,
        provider_order: str | None = None,
    ):
        self._explicit_candidates = list(candidates) if candidates is not None else None
        self._provider_order = provider_order

    def get_candidates(self) -> list[BaseOCRProvider]:
        if self._explicit_candidates is not None:
            return self._explicit_candidates
        from ocr.factory import get_candidate_providers
        return get_candidate_providers(self._provider_order)

    def is_configured(self) -> bool:
        return any(c.is_configured() for c in self.get_candidates())

    def recognize(
        self,
        image_bytes: bytes,
        image_width: int | None = None,
        image_height: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> OcrResult:
        t0_total = time.perf_counter()
        candidates = self.get_candidates()
        attempts: list[dict[str, Any]] = []

        if not candidates:
            return OcrResult(
                engine="none",
                provider="none",
                status=STATUS_UNAVAILABLE,
                failure_reason="No OCR providers configured or available in candidate chain.",
                duration_ms=round((time.perf_counter() - t0_total) * 1000, 2),
                attempts=attempts,
            )

        for candidate in candidates:
            # 1. Skip if unconfigured or unsupported
            if not candidate.is_configured():
                attempts.append({
                    "provider": candidate.name,
                    "status": STATUS_SKIPPED,
                    "category": "skipped_unconfigured",
                    "duration_ms": 0.0,
                    "failure_reason": f"Provider '{candidate.name}' skipped: required credentials not configured.",
                })
                logger.info("[OCR] Provider '%s' skipped: credentials not configured.", candidate.name)
                continue

            # 2. Attempt recognition
            t0_cand = time.perf_counter()
            try:
                result = candidate.recognize(
                    image_bytes=image_bytes,
                    image_width=image_width,
                    image_height=image_height,
                    options=options,
                )
            except Exception as exc:
                cand_dur = round((time.perf_counter() - t0_cand) * 1000, 2)
                safe_exc = sanitize_sensitive_text(str(exc))
                result = OcrResult(
                    engine=candidate.name,
                    provider=candidate.name,
                    status=STATUS_PROVIDER_ERROR,
                    failure_reason=f"Provider '{candidate.name}' unhandled exception: {type(exc).__name__}: {safe_exc}",
                    duration_ms=cand_dur,
                )

            # Ensure failure_reason is sanitized
            if result.failure_reason:
                result.failure_reason = sanitize_sensitive_text(result.failure_reason)

            attempt_entry = {
                "provider": candidate.name,
                "status": result.status,
                "category": _status_to_category(result.status),
                "duration_ms": result.duration_ms,
                "failure_reason": result.failure_reason,
            }
            attempts.append(attempt_entry)

            # 3. Success check
            if result.status == STATUS_SUCCESS:
                result.attempts = attempts
                result.duration_ms = round((time.perf_counter() - t0_total) * 1000, 2)
                logger.info(
                    "[OCR] Fallback chain succeeded with provider '%s' (line_count=%d, duration=%.2fms, total_attempts=%d)",
                    candidate.name,
                    len(result.lines),
                    result.duration_ms,
                    len(attempts),
                )
                return result

            # 4. Valid no-text check
            if result.status == STATUS_NO_TEXT:
                result.attempts = attempts
                result.duration_ms = round((time.perf_counter() - t0_total) * 1000, 2)
                logger.info(
                    "[OCR] Provider '%s' completed with NO_TEXT (duration=%.2fms)",
                    candidate.name,
                    result.duration_ms,
                )
                return result

            # 5. Runtime failure eligible for fallback
            if result.status in FALLBACK_TRIGGER_STATUSES:
                logger.warning(
                    "[OCR] Provider '%s' failed (status=%s, reason=%s). Falling back to next candidate...",
                    candidate.name,
                    result.status,
                    result.failure_reason,
                )
                continue

            # Any other status: fallback
            continue

        # All providers exhausted without success
        total_dur = round((time.perf_counter() - t0_total) * 1000, 2)
        invoked_attempts = [a for a in attempts if a["category"] != "skipped_unconfigured"]

        if not invoked_attempts:
            overall_status = STATUS_UNAVAILABLE
            failure_summary = "All OCR providers skipped: no configured credentials found."
        else:
            summary_parts = [
                f"{a['provider']} ({a['status']}: {a.get('failure_reason') or 'failed'})"
                for a in invoked_attempts
            ]
            failure_summary = f"All OCR providers failed: {'; '.join(summary_parts)}"
            if any(a["status"] == STATUS_AUTH_ERROR for a in invoked_attempts):
                overall_status = STATUS_AUTH_ERROR
            elif any(a["status"] == STATUS_TIMEOUT for a in invoked_attempts):
                overall_status = STATUS_TIMEOUT
            elif any(a["status"] == STATUS_RATE_LIMITED for a in invoked_attempts):
                overall_status = STATUS_RATE_LIMITED
            else:
                overall_status = STATUS_PROVIDER_ERROR

        logger.error("[OCR] %s (duration=%.2fms)", failure_summary, total_dur)
        return OcrResult(
            lines=[],
            engine="none",
            provider="none",
            status=overall_status,
            failure_reason=failure_summary,
            duration_ms=total_dur,
            attempts=attempts,
        )
