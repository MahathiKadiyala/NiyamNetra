"""Backend/ocr/diagnostics.py — Operational diagnostics and health telemetry for OCR providers.

Guarantees:
- Differentiates between configuration presence, live verification, and runtime failure.
- Never returns or serializes API keys, secrets, tokens, or credentials.
- Accurately reports provider readiness without falsely claiming unverified providers are functional.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone
from typing import Any

from config import settings
from ocr.base import (
    STATUS_AUTH_ERROR,
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from ocr.security import sanitize_diagnostic_data, sanitize_sensitive_text

_lock = threading.Lock()

_diagnostic_state: dict[str, Any] = {
    "last_live_success": None,
    "last_failure": None,
    "last_attempted_providers": [],
    "fallback_occurred": False,
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def record_provider_success(
    provider: str,
    duration_ms: float = 0.0,
    line_count: int = 0,
    is_test: bool = False,
) -> None:
    """Record a successful provider request."""
    with _lock:
        _diagnostic_state["last_live_success"] = {
            "provider": provider,
            "duration_ms": duration_ms,
            "line_count": line_count,
            "timestamp": _now_iso(),
            "source": "test_double" if is_test else "live_request",
        }
        if provider not in _diagnostic_state["last_attempted_providers"]:
            _diagnostic_state["last_attempted_providers"].append(provider)


def record_provider_failure(
    provider: str,
    status: str,
    category: str,
    failure_reason: str | None = None,
    duration_ms: float = 0.0,
) -> None:
    """Record a failed provider request."""
    clean_reason = sanitize_sensitive_text(failure_reason)
    with _lock:
        _diagnostic_state["last_failure"] = {
            "provider": provider,
            "status": status,
            "category": category,
            "failure_reason": clean_reason,
            "duration_ms": duration_ms,
            "timestamp": _now_iso(),
        }
        if provider not in _diagnostic_state["last_attempted_providers"]:
            _diagnostic_state["last_attempted_providers"].append(provider)


def record_provider_fallback(
    primary: str,
    fallback: str,
    duration_ms: float = 0.0,
) -> None:
    """Record that a fallback transition occurred."""
    with _lock:
        _diagnostic_state["fallback_occurred"] = True
        for p in (primary, fallback):
            if p not in _diagnostic_state["last_attempted_providers"]:
                _diagnostic_state["last_attempted_providers"].append(p)


def reset_diagnostic_records() -> None:
    """Reset recorded operational state (useful for test isolation)."""
    with _lock:
        _diagnostic_state["last_live_success"] = None
        _diagnostic_state["last_failure"] = None
        _diagnostic_state["last_attempted_providers"] = []
        _diagnostic_state["fallback_occurred"] = False


def get_ocr_health_diagnostics() -> dict[str, Any]:
    """Compile safe, telemetry-rich OCR diagnostics for the /health endpoint and admin dashboards.

    Strictly ensures ZERO secrets are exposed.
    """
    from ocr.factory import get_candidate_providers, get_ocr_provider

    prov_cfg = (getattr(settings, "OCR_PROVIDER", "auto") or "auto").strip().lower()
    selection_mode = "auto" if prov_cfg in ("auto", "default", "fallback", "composite") else "explicit"

    invalid_cfg = False
    try:
        active_provider = get_ocr_provider(prov_cfg)
    except ValueError:
        active_provider = None
        invalid_cfg = True

    if invalid_cfg:
        candidates = []
        any_configured = False
    elif selection_mode == "explicit" and active_provider is not None:
        candidates = [active_provider]
        any_configured = active_provider.is_configured()
    else:
        candidates = get_candidate_providers()
        any_configured = any(c.is_configured() for c in candidates)

    with _lock:
        last_succ = dict(_diagnostic_state["last_live_success"]) if _diagnostic_state["last_live_success"] else None
        last_fail = dict(_diagnostic_state["last_failure"]) if _diagnostic_state["last_failure"] else None
        attempted = list(_diagnostic_state["last_attempted_providers"])
        fallback_occurred = _diagnostic_state["fallback_occurred"]

    has_live_evidence = last_succ is not None
    is_configured = any_configured or has_live_evidence

    # Readiness determination distinguishing all operational states
    if invalid_cfg:
        readiness = "invalid_configuration"
        verification_status = "unsupported_provider"
    elif last_succ is not None:
        if fallback_occurred:
            readiness = "degraded"
            verification_status = "fallback_succeeded"
        else:
            readiness = "live_verified"
            verification_status = "live_verified"
    elif not is_configured:
        readiness = "unconfigured"
        verification_status = "missing_configuration"
    elif last_fail is not None:
        fail_cat = last_fail.get("category", "provider_error")
        if fail_cat == "auth_error":
            readiness = "unauthorized"
            verification_status = "auth_error"
        elif fail_cat == "billing_error":
            readiness = "billing_error"
            verification_status = "billing_error"
        elif fail_cat == "rate_limited":
            readiness = "rate_limited"
            verification_status = "rate_limited"
        elif fail_cat == "timeout":
            readiness = "timeout"
            verification_status = "timeout"
        elif fail_cat == "network_error":
            readiness = "network_error"
            verification_status = "network_error"
        elif fail_cat == "all_failed":
            readiness = "all_failed"
            verification_status = "all_providers_failed"
        else:
            readiness = "error"
            verification_status = fail_cat
    else:
        readiness = "ready_unverified"
        verification_status = "configured_not_live_verified"

    # Provider status list
    providers_info = []
    for idx, c in enumerate(candidates, start=1):
        c_status = "unverified"
        if last_succ and last_succ.get("provider") == c.name:
            c_status = STATUS_SUCCESS
        elif last_fail and last_fail.get("provider") == c.name:
            c_status = last_fail.get("status", STATUS_PROVIDER_ERROR)

        providers_info.append({
            "name": c.name,
            "configured": c.is_configured(),
            "priority": idx,
            "last_status": c_status,
        })

    last_error_cat = last_fail.get("category") if last_fail else None
    verification_source = last_succ.get("source", "none") if last_succ else "none"

    raw_diag = {
        "provider_selection_mode": selection_mode,
        "selected_provider": prov_cfg,
        "configured": is_configured,
        "live_verified": (last_succ is not None),
        "readiness": readiness,
        "verification_status": verification_status,
        "verification_source": verification_source,
        "fallback_occurred": fallback_occurred,
        "last_error_category": last_error_cat,
        "last_attempted_providers": attempted,
        "providers": providers_info,
        "coverage_note": "OCR configuration does not guarantee live cloud service reachability",
    }

    return sanitize_diagnostic_data(raw_diag)
