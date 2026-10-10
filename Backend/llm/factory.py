"""Backend/llm/factory.py — LLM Provider Factory, Orchestrator, & Deterministic Cache (Phase 3).

Features:
- Deterministic provider resolution (Section 27 & 28).
- Primary -> fallback provider cascade.
- Idempotent deterministic fingerprint caching (Section 29).
- Single inspection request orchestrator (Section 4).
- Offline / missing key safe fallback (Section 31).
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

from config import settings
from llm.base import BaseLLMProvider
from llm.gemini_provider import GeminiLLMProvider
from llm.groq_provider import GroqLLMProvider
from llm.prompts import PROMPT_VERSION, SCHEMA_VERSION, package_ocr_for_llm
from llm.schema import (
    LLM_STATUS_FAILED,
    LLM_STATUS_PENDING,
    LLM_STATUS_SUCCESS,
    LLM_STATUS_UNAVAILABLE,
    StructuredDeclarationResult,
)
from ocr.base import OcrResult

logger = logging.getLogger("niyamnetra.llm.factory")

# Process-level LRU memory cache for idempotent runs
_LLM_MEMORY_CACHE: dict[str, StructuredDeclarationResult] = {}
_MAX_CACHE_ITEMS = 256


def get_llm_provider(
    provider_name: str | None = None,
    model_name: str | None = None,
) -> BaseLLMProvider:
    """Return the configured Cloud LLM provider instance (Section 27)."""
    name = (provider_name or getattr(settings, "LLM_PROVIDER", "groq") or "groq").strip().lower()

    if name in ("groq", "groq_cloud", "groq-llm"):
        return GroqLLMProvider(model=model_name)
    if name in ("gemini", "google_gemini", "gemini-flash"):
        return GeminiLLMProvider(model=model_name)

    # In "auto" mode: select primary configured key deterministically
    if getattr(settings, "GROQ_API_KEY", None):
        return GroqLLMProvider(model=model_name)
    if getattr(settings, "GEMINI_API_KEY", None):
        return GeminiLLMProvider(model=model_name)

    # Default to Groq instance (will return status=UNAVAILABLE gracefully if unconfigured)
    return GroqLLMProvider(model=model_name)


def compute_llm_cache_hash(packaged_ocr: str, provider: str, model: str) -> str:
    """Compute deterministic fingerprint of OCR evidence + prompt/schema/model versions (Section 29)."""
    key_str = f"{packaged_ocr}|{PROMPT_VERSION}|{SCHEMA_VERSION}|{provider}|{model}"
    return hashlib.sha256(key_str.encode("utf-8")).hexdigest()


def structure_inspection_ocr(
    ocr_input: list[OcrResult] | OcrResult,
    evidence_meta: dict[str, Any] | None = None,
    primary_provider_name: str | None = None,
    fallback_provider_name: str | None = None,
    use_cache: bool = True,
) -> StructuredDeclarationResult:
    """Orchestrate one-call structured declaration extraction across all inspection panels.

    Follows Section 4 (One request per inspection), Section 26 (Retry), and Section 27 (Fallback).
    """
    # 1. Package multi-panel OCR text cleanly
    packaged_ocr = package_ocr_for_llm(ocr_input)

    # 2. Check if all panels yielded no text
    results_list = [ocr_input] if isinstance(ocr_input, OcrResult) else ocr_input
    has_any_text = any(len(res.lines) > 0 for res in results_list)
    if not has_any_text:
        _errs = [getattr(r, "failure_reason", None) for r in results_list if getattr(r, "failure_reason", None)]
        _statuses = [getattr(r, "status", None) for r in results_list if getattr(r, "status", None)]
        _is_fail = any(s in ("provider_error", "input_unavailable", "timeout") for s in _statuses) or bool(_errs)
        _status = LLM_STATUS_FAILED if _is_fail else LLM_STATUS_SUCCESS
        _evidence_st = "ocr_failed" if _is_fail else "no_readable_text"
        _note = f"OCR provider failed: {_errs[0]}" if _errs else ("No text detected across any panel; all declarations not_observed.")

        return StructuredDeclarationResult(
            metadata={
                "status": _status,
                "evidence_status": _evidence_st,
                "error": _errs[0] if _errs else None,
                "note": _note,
                "llm_prompt_version": PROMPT_VERSION,
                "llm_schema_version": SCHEMA_VERSION,
                "duration_ms": 0.0,
            },
            raw_packaged_ocr=packaged_ocr,
        )

    # 3. Resolve primary provider & model
    primary = get_llm_provider(primary_provider_name)
    cache_hash = compute_llm_cache_hash(packaged_ocr, primary.name, primary.model_name)

    # 4. Check cache if enabled
    cache_enabled = use_cache and getattr(settings, "LLM_CACHE_ENABLED", True)
    if cache_enabled and cache_hash in _LLM_MEMORY_CACHE:
        logger.debug("[LLM Cache] In-memory cache hit for hash %s", cache_hash[:12])
        hit = _LLM_MEMORY_CACHE[cache_hash]
        # Return a copy with fresh metadata flag
        res = StructuredDeclarationResult.from_dict(hit.to_dict())
        res.metadata["cache_hit"] = True
        return res

    # 5. Execute primary provider call
    logger.info("[LLM Structuring] Calling primary provider: %s (model: %s)", primary.name, primary.model_name)
    result = primary.extract_declarations(packaged_ocr, evidence_meta)

    # 6. Check if primary failed and fallback is available
    if result.metadata.get("status") in (LLM_STATUS_FAILED, LLM_STATUS_UNAVAILABLE):
        fb_name = fallback_provider_name or getattr(settings, "LLM_FALLBACK_PROVIDER", "gemini")
        if fb_name and fb_name != primary.name:
            logger.warning(
                "[LLM Fallback] Primary provider %s failed (%s). Triggering fallback provider %s.",
                primary.name, result.metadata.get("error"), fb_name,
            )
            fallback = get_llm_provider(fb_name)
            if fallback.api_key:
                result = fallback.extract_declarations(packaged_ocr, evidence_meta)
                result.metadata["fallback_used"] = True
                result.metadata["primary_error"] = result.metadata.get("error")

    # 7. Record evidence fingerprint and stage in cache if successful
    result.metadata["evidence_fingerprint"] = cache_hash
    if cache_enabled and result.metadata.get("status") == LLM_STATUS_SUCCESS:
        if len(_LLM_MEMORY_CACHE) >= _MAX_CACHE_ITEMS:
            _LLM_MEMORY_CACHE.pop(next(iter(_LLM_MEMORY_CACHE)))
        _LLM_MEMORY_CACHE[cache_hash] = result

    return result
