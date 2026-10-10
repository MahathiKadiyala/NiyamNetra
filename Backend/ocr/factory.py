"""Backend/ocr/factory.py — Provider factory and configuration selector.

Server-side deterministic selection of OCR Provider (Section 6 & 10).
Does not expose provider secrets to client frontends.
"""
from __future__ import annotations

from config import settings
from ocr.azure_vision import AzureVisionProvider
from ocr.base import BaseOCRProvider
from ocr.fallback import FallbackOCRProvider
from ocr.google_vision import GoogleVisionProvider
from ocr.ocr_space import OCRSpaceProvider


def get_candidate_providers(order_str: str | None = None) -> list[BaseOCRProvider]:
    """Parse configured provider chain order and instantiate providers."""
    raw_order = (
        order_str
        or getattr(settings, "OCR_PROVIDER_ORDER", None)
        or getattr(settings, "OCR_PROVIDER_CHAIN", None)
        or "google_vision,ocr_space,azure"
    )
    candidates: list[BaseOCRProvider] = []
    seen = set()
    for item in raw_order.split(","):
        prov_key = item.strip().lower()
        if not prov_key or prov_key in seen:
            continue
        seen.add(prov_key)
        if prov_key in ("google", "google_vision", "google-vision", "vision"):
            candidates.append(GoogleVisionProvider())
        elif prov_key in ("ocrspace", "ocr_space", "ocr-space"):
            candidates.append(OCRSpaceProvider())
        elif prov_key in ("azure", "azure_vision", "azure-vision"):
            candidates.append(AzureVisionProvider())
    return candidates


def get_ocr_provider(provider_name: str | None = None) -> BaseOCRProvider:
    """Return the configured cloud OCR provider instance.

    If 'auto' (the default), returns a FallbackOCRProvider that dynamically
    attempts configured providers in priority order and falls back upon runtime errors.
    If an explicit provider is requested, returns that specific provider.
    """
    name = (provider_name or getattr(settings, "OCR_PROVIDER", "auto") or "auto").strip().lower()

    if name in ("google", "google_vision", "google-vision", "vision"):
        return GoogleVisionProvider()
    if name in ("ocrspace", "ocr_space", "ocr-space"):
        return OCRSpaceProvider()
    if name in ("azure", "azure_vision", "azure-vision"):
        return AzureVisionProvider()
    if name in ("fallback", "composite"):
        return FallbackOCRProvider(candidates=get_candidate_providers())

    # "auto" -> Resilient Fallback Chain
    return FallbackOCRProvider(candidates=get_candidate_providers())
