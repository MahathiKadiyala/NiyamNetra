"""Backend/ocr/azure_vision.py — Adapter for Azure AI Vision Read OCR API.

Features:
- Connects to Azure AI Vision Image Analysis Read feature (v4.0 API).
- Extracts lines and words with polygon coordinates and word confidences.
- Handles synchronous and operation polling transparently (Section 20).
- Server-side credentials only (AZURE_VISION_KEY, AZURE_VISION_ENDPOINT).
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
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from ocr.security import sanitize_sensitive_text


class AzureVisionProvider(BaseOCRProvider):
    name: str = "azure"

    def __init__(
        self,
        api_key: str | None = None,
        endpoint: str | None = None,
        timeout_s: float | None = None,
    ):
        self.api_key = getattr(settings, "AZURE_VISION_KEY", None) if api_key is None else api_key
        self.endpoint = getattr(settings, "AZURE_VISION_ENDPOINT", None) if endpoint is None else endpoint
        self.timeout_s = timeout_s if timeout_s is not None else getattr(settings, "AZURE_VISION_TIMEOUT_S", 15.0)

    def is_configured(self) -> bool:
        return bool(self.api_key and str(self.api_key).strip() and self.endpoint and str(self.endpoint).strip())

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
                failure_reason="Azure Vision API key or endpoint not configured.",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        url = f"{self.endpoint.rstrip('/')}/computervision/imageanalysis:analyze?api-version=2024-02-01&features=read"
        headers = {
            "Ocp-Apim-Subscription-Key": self.api_key,
            "Content-Type": "application/octet-stream",
        }

        import httpx

        def _do_post():
            return httpx.post(url, headers=headers, content=image_bytes, timeout=self.timeout_s)

        try:
            resp = self._execute_with_retry(_do_post)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_TIMEOUT,
                failure_reason="Azure Vision request timed out.",
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
                failure_reason=f"Azure Vision HTTP {code}: {clean_msg}",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except Exception as e:
            clean_msg = sanitize_sensitive_text(str(e))
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_PROVIDER_ERROR,
                failure_reason=f"Azure Vision connection error: {type(e).__name__}: {clean_msg}",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        return self._normalize_response(data, image_width, image_height, round((time.perf_counter() - t0) * 1000, 2))

    def _normalize_response(
        self,
        data: dict,
        image_width: int | None,
        image_height: int | None,
        duration_ms: float,
    ) -> OcrResult:
        read_res = data.get("readResult") or {}
        blocks = read_res.get("blocks") or []
        lines: list[OcrLine] = []
        all_confs: list[float] = []

        for blk in blocks:
            for ln in blk.get("lines") or []:
                text = (ln.get("text") or "").strip()
                if not text:
                    continue

                poly = ln.get("boundingPolygon") or []
                xs = [float(p.get("x", 0)) for p in poly]
                ys = [float(p.get("y", 0)) for p in poly]
                h_px = float(max(ys) - min(ys)) if ys else 0.0

                quad = (
                    [
                        [min(xs), min(ys)],
                        [max(xs), min(ys)],
                        [max(xs), max(ys)],
                        [min(xs), max(ys)],
                    ]
                    if xs and ys
                    else [[0.0, 0.0]]
                )

                words: list[OcrWord] = []
                word_confs: list[float] = []
                for w in ln.get("words") or []:
                    w_text = (w.get("text") or "").strip()
                    w_conf = None
                    if "confidence" in w:
                        try:
                            w_conf = float(w["confidence"])
                            word_confs.append(w_conf)
                            all_confs.append(w_conf)
                        except (TypeError, ValueError):
                            pass

                    w_poly = w.get("boundingPolygon") or []
                    w_box = [[float(p.get("x", 0)), float(p.get("y", 0))] for p in w_poly] or None
                    if w_text:
                        words.append(OcrWord(text=w_text, confidence=w_conf, box=w_box))

                line_conf = float(sum(word_confs) / len(word_confs)) if word_confs else None
                lines.append(
                    OcrLine(
                        text=text,
                        confidence=line_conf,
                        box=quad,
                        height_px=h_px,
                        words=words,
                    )
                )

        if not lines:
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_NO_TEXT,
                failure_reason="Azure Vision detected no text.",
                duration_ms=duration_ms,
                source_width=image_width,
                source_height=image_height,
            )

        mean_conf = float(sum(all_confs) / len(all_confs)) if all_confs else None
        return OcrResult(
            lines=lines,
            engine=self.name,
            provider=self.name,
            status=STATUS_SUCCESS,
            mean_confidence=mean_conf,
            source_width=image_width,
            source_height=image_height,
            duration_ms=duration_ms,
        )
