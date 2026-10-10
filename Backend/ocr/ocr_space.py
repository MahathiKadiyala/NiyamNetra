"""Backend/ocr/ocr_space.py — Direct REST adapter for OCR.space Cloud OCR.

Features:
- Requests isOverlayRequired=true for word and line level bounding boxes.
- detectOrientation=true for rotated packaging labels.
- Normalizes coordinates into OcrLine and OcrWord.
- Records confidence=None honestly (free tier reports no per-word confidence scores).
- Server-side credentials only (OCR_SPACE_API_KEY).
"""
from __future__ import annotations

import time
from typing import Any

from config import settings
from ocr.base import (
    BaseOCRProvider,
    OcrLine,
    OcrResult,
    OcrWord,
    STATUS_AUTH_ERROR,
    STATUS_INVALID_RESPONSE,
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from ocr.security import sanitize_sensitive_text


class OCRSpaceProvider(BaseOCRProvider):
    name: str = "ocr_space"

    def __init__(
        self,
        api_key: str | None = None,
        endpoint_url: str | None = None,
        timeout_s: float | None = None,
    ):
        self.api_key = getattr(settings, "OCR_SPACE_API_KEY", None) if api_key is None else api_key
        self.endpoint_url = endpoint_url or getattr(
            settings, "OCR_SPACE_URL", "https://api.ocr.space/parse/image"
        )
        self.timeout_s = timeout_s if timeout_s is not None else getattr(settings, "OCR_SPACE_TIMEOUT_S", 15.0)

    def is_configured(self) -> bool:
        return bool(self.api_key and str(self.api_key).strip())

    def recognize(
        self,
        image_bytes: bytes,
        image_width: int | None = None,
        image_height: int | None = None,
        options: dict[str, Any] | None = None,
    ) -> OcrResult:
        t0 = time.perf_counter()
        if not self.is_configured():
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_UNAVAILABLE,
                failure_reason="OCR.space API key not configured.",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        # Check payload size (OCR.space free tier max file size is 1 MB)
        if len(image_bytes) > 1_024_000:
            import cv2
            import numpy as np

            arr = np.frombuffer(image_bytes, np.uint8)
            decoded = cv2.imdecode(arr, cv2.IMREAD_COLOR)
            if decoded is not None:
                for q in (75, 60, 45):
                    ok, buf = cv2.imencode(".jpg", decoded, [int(cv2.IMWRITE_JPEG_QUALITY), q])
                    if ok and len(buf) <= 1_000_000:
                        image_bytes = buf.tobytes()
                        break

        data_fields = {
            "apikey": self.api_key,
            "OCREngine": str(getattr(settings, "OCR_SPACE_ENGINE", 1)),
            "language": getattr(settings, "OCR_SPACE_LANGUAGE", "eng"),
            "isOverlayRequired": "true",
            "scale": "true",
            "detectOrientation": "true",
        }

        files = {"file": ("scan.jpg", image_bytes, "image/jpeg")}

        import httpx

        def _do_post():
            return httpx.post(
                self.endpoint_url,
                data=data_fields,
                files=files,
                timeout=self.timeout_s,
            )

        try:
            resp = self._execute_with_retry(_do_post)
            resp.raise_for_status()
            body = resp.json()
        except httpx.TimeoutException:
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_TIMEOUT,
                failure_reason="OCR.space request timed out.",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            status = STATUS_PROVIDER_ERROR
            if code in (401, 403):
                status = STATUS_AUTH_ERROR
            elif code == 429:
                status = STATUS_RATE_LIMITED
            clean_msg = sanitize_sensitive_text(str(e))
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=status,
                failure_reason=f"OCR.space HTTP {code}: {clean_msg}",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except Exception as e:
            clean_msg = sanitize_sensitive_text(str(e))
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_PROVIDER_ERROR,
                failure_reason=f"OCR.space error: {type(e).__name__}: {clean_msg}",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        return self._normalize_response(body, image_width, image_height, round((time.perf_counter() - t0) * 1000, 2))

    def _normalize_response(
        self,
        body: dict,
        image_width: int | None,
        image_height: int | None,
        duration_ms: float,
    ) -> OcrResult:
        if body.get("IsErroredOnProcessing"):
            msg = body.get("ErrorMessage") or body.get("ErrorDetails") or "Unknown OCR.space processing error"
            if isinstance(msg, list):
                msg = "; ".join(str(m) for m in msg)
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_PROVIDER_ERROR,
                failure_reason=f"OCR.space processing error: {msg}",
                duration_ms=duration_ms,
            )

        parsed_results = body.get("ParsedResults") or []
        if not parsed_results:
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_NO_TEXT,
                failure_reason="OCR.space returned no parsed results.",
                duration_ms=duration_ms,
                source_width=image_width,
                source_height=image_height,
            )

        lines: list[OcrLine] = []
        for pr in parsed_results:
            overlay = ((pr or {}).get("TextOverlay") or {}).get("Lines") or []
            for ln in overlay:
                text = (ln.get("LineText") or "").strip()
                if not text:
                    continue

                words: list[OcrWord] = []
                xs: list[float] = []
                ys: list[float] = []
                heights: list[float] = []

                for w in ln.get("Words") or []:
                    w_text = (w.get("WordText") or "").strip()
                    try:
                        left = float(w.get("Left", 0))
                        top = float(w.get("Top", 0))
                        wd = float(w.get("Width", 0))
                        ht = float(w.get("Height", 0))
                    except (TypeError, ValueError):
                        continue

                    w_box = [[left, top], [left + wd, top], [left + wd, top + ht], [left, top + ht]]
                    xs += [left, left + wd]
                    ys += [top, top + ht]
                    heights.append(ht)

                    if w_text:
                        words.append(OcrWord(text=w_text, confidence=None, box=w_box))

                try:
                    line_h = float(ln.get("MaxHeight") or (max(heights) if heights else 0.0))
                except (TypeError, ValueError):
                    line_h = max(heights) if heights else 0.0

                line_quad = (
                    [
                        [min(xs), min(ys)],
                        [max(xs), min(ys)],
                        [max(xs), max(ys)],
                        [min(xs), max(ys)],
                    ]
                    if xs and ys
                    else [[0.0, 0.0]]
                )

                lines.append(
                    OcrLine(
                        text=text,
                        confidence=None,  # Honest null per Section 14
                        box=line_quad,
                        height_px=line_h,
                        words=words,
                    )
                )

        if not lines:
            # Fallback to plain text if overlay was empty
            for pr in parsed_results:
                for raw_line in ((pr or {}).get("ParsedText") or "").splitlines():
                    t = raw_line.strip()
                    if t:
                        lines.append(OcrLine(text=t, confidence=None, box=[[0.0, 0.0]], height_px=0.0))

        if not lines:
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_NO_TEXT,
                failure_reason="OCR.space detected no text.",
                duration_ms=duration_ms,
                source_width=image_width,
                source_height=image_height,
            )

        return OcrResult(
            lines=lines,
            engine=self.name,
            provider=self.name,
            status=STATUS_SUCCESS,
            mean_confidence=None,
            source_width=image_width,
            source_height=image_height,
            duration_ms=duration_ms,
        )
