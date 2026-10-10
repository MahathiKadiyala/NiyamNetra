"""Backend/ocr/base.py — Normalized OCR contract and base provider abstraction.

Maintains 100% backward compatibility with downstream code (rules_engine,
routers/scans, reports) while adding rich geometry, confidence, and provenance.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


# Standardized OCR Status Constants
STATUS_SUCCESS = "OCR_SUCCESS"
STATUS_INPUT_UNAVAILABLE = "OCR_INPUT_UNAVAILABLE"
STATUS_TIMEOUT = "OCR_TIMEOUT"
STATUS_RATE_LIMITED = "OCR_RATE_LIMITED"
STATUS_PROVIDER_ERROR = "OCR_PROVIDER_ERROR"
STATUS_AUTH_ERROR = "OCR_AUTH_ERROR"
STATUS_INVALID_RESPONSE = "OCR_INVALID_RESPONSE"
STATUS_NO_TEXT = "OCR_NO_TEXT"
STATUS_UNAVAILABLE = "OCR_UNAVAILABLE"
STATUS_SKIPPED = "OCR_SKIPPED"


@dataclass(slots=True)
class OcrWord:
    text: str
    confidence: float | None = None
    box: list[list[float]] | None = None  # [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]


@dataclass(slots=True)
class OcrLine:
    text: str
    confidence: float | None = None
    box: list[list[float]] | None = None  # Polygon quad [[x1, y1], [x2, y2], [x3, y3], [x4, y4]]
    height_px: float = 0.0
    words: list[OcrWord] = field(default_factory=list)


@dataclass(slots=True)
class OcrBlock:
    text: str
    confidence: float | None = None
    box: list[list[float]] | None = None
    lines: list[OcrLine] = field(default_factory=list)


@dataclass(slots=True)
class OcrResult:
    lines: list[OcrLine] = field(default_factory=list)
    engine: str = "none"
    mean_confidence: float | None = None
    failure_reason: str | None = None

    # Enhanced normalized fields (Section 6 & 12)
    status: str = STATUS_SUCCESS
    provider: str = ""
    provider_request_id: str | None = None
    provider_version: str | None = None
    source_width: int | None = None
    source_height: int | None = None
    orientation: float | None = None
    duration_ms: float = 0.0
    blocks: list[OcrBlock] = field(default_factory=list)

    # Provenance fields (Section 11)
    inspection_id: int | None = None
    scan_id: int | None = None
    image_id: int | None = None
    panel: str | None = None
    input_artifact_type: str = ""  # "rectified" or "analysis"

    # Runtime fallback & diagnostics (Module 1)
    attempts: list[dict[str, Any]] = field(default_factory=list)

    @property
    def diagnostic_trace(self) -> list[dict[str, Any]]:
        return self.attempts

    @property
    def full_text(self) -> str:
        return "\n".join(l.text for l in self.lines)


class BaseOCRProvider(ABC):
    """Abstract base class for all cloud OCR providers."""

    name: str = "base"

    def is_configured(self) -> bool:
        """Return True if required API credentials / endpoints are configured."""
        return True

    @abstractmethod
    def recognize(
        self,
        image_bytes: bytes,
        image_width: int | None = None,
        image_height: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> OcrResult:
        """Process image bytes and return a normalized OcrResult.

        Must NEVER raise unhandled exceptions to callers; return an OcrResult with
        an appropriate error status and failure_reason.
        """
        pass

    def _execute_with_retry(
        self,
        func,
        max_retries: int = 2,
        initial_backoff_s: float = 0.5,
    ) -> Any:
        """Bounded retry policy for transient network/rate errors (Section 16)."""
        retries = 0
        backoff = initial_backoff_s
        while True:
            try:
                return func()
            except Exception as e:
                # Check if transient error eligible for retry
                is_transient = False
                err_str = str(e).lower()
                status_code = getattr(getattr(e, "response", None), "status_code", None)

                if status_code in (429, 502, 503, 504):
                    is_transient = True
                elif "timeout" in err_str or "connection" in err_str:
                    is_transient = True

                if is_transient and retries < max_retries:
                    retries += 1
                    time.sleep(backoff)
                    backoff *= 2.0
                    continue
                raise e
