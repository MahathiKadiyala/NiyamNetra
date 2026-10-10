"""Backend/ocr package — Cloud OCR provider abstraction for NiyamNetra."""
from ocr.azure_vision import AzureVisionProvider
from ocr.base import (
    BaseOCRProvider,
    OcrBlock,
    OcrLine,
    OcrResult,
    OcrWord,
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
)
from ocr.fallback import FallbackOCRProvider
from ocr.factory import get_candidate_providers, get_ocr_provider
from ocr.google_vision import GoogleVisionProvider
from ocr.ocr_space import OCRSpaceProvider
from ocr.security import sanitize_sensitive_text

__all__ = [
    "BaseOCRProvider",
    "GoogleVisionProvider",
    "OCRSpaceProvider",
    "AzureVisionProvider",
    "FallbackOCRProvider",
    "get_ocr_provider",
    "get_candidate_providers",
    "sanitize_sensitive_text",
    "OcrLine",
    "OcrResult",
    "OcrBlock",
    "OcrWord",
    "STATUS_SUCCESS",
    "STATUS_INPUT_UNAVAILABLE",
    "STATUS_TIMEOUT",
    "STATUS_RATE_LIMITED",
    "STATUS_PROVIDER_ERROR",
    "STATUS_AUTH_ERROR",
    "STATUS_INVALID_RESPONSE",
    "STATUS_NO_TEXT",
    "STATUS_UNAVAILABLE",
    "STATUS_SKIPPED",
]
