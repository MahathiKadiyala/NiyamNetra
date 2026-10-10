"""Backend/ocr/google_vision.py — Direct REST adapter for Google Cloud Vision OCR.

Features:
- Uses DOCUMENT_TEXT_DETECTION for dense-label multilingual packaging text.
- Extracts hierarchical blocks, paragraph lines, word bounding boxes, and symbol confidences.
- Zero heavyweight Google Cloud SDK dependencies (httpx only; safe for Render 512MB).
- Server-side credentials only (GOOGLE_VISION_API_KEY).
"""
from __future__ import annotations

import base64
import time
from typing import Any

from config import settings
from ocr.base import (
    BaseOCRProvider,
    OcrBlock,
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


class GoogleVisionProvider(BaseOCRProvider):
    name: str = "google_vision"

    def __init__(self, api_key: str | None = None, endpoint_url: str | None = None, timeout_s: float | None = None):
        self.api_key = getattr(settings, "GOOGLE_VISION_API_KEY", None) if api_key is None else api_key
        self.endpoint_url = endpoint_url or getattr(
            settings, "GOOGLE_VISION_URL", "https://vision.googleapis.com/v1/images:annotate"
        )
        self.timeout_s = timeout_s if timeout_s is not None else getattr(settings, "GOOGLE_VISION_TIMEOUT_S", 12.0)

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
                failure_reason="Google Cloud Vision API key not configured.",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        content_b64 = base64.b64encode(image_bytes).decode("ascii")
        payload = {
            "requests": [
                {
                    "image": {"content": content_b64},
                    "features": [{"type": "DOCUMENT_TEXT_DETECTION"}],
                    "imageContext": {"languageHints": ["en", "hi"]},
                }
            ]
        }

        import httpx

        headers = {"X-Goog-Api-Key": self.api_key}

        def _do_post():
            return httpx.post(
                self.endpoint_url,
                headers=headers,
                json=payload,
                timeout=self.timeout_s,
            )

        try:
            resp = self._execute_with_retry(_do_post)
            resp.raise_for_status()
            data = resp.json()
        except httpx.TimeoutException:
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_TIMEOUT,
                failure_reason="Google Cloud Vision request timed out.",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            status = STATUS_PROVIDER_ERROR
            msg = str(e)
            try:
                err_body = e.response.json().get("error", {})
                msg = err_body.get("message", msg)
            except Exception:
                pass

            if code in (401, 403):
                status = STATUS_AUTH_ERROR
            elif code == 429:
                status = STATUS_RATE_LIMITED

            clean_msg = sanitize_sensitive_text(msg)
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=status,
                failure_reason=f"Google Cloud Vision HTTP {code}: {clean_msg}",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )
        except Exception as e:
            clean_msg = sanitize_sensitive_text(str(e))
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_PROVIDER_ERROR,
                failure_reason=f"Google Cloud Vision connection error: {type(e).__name__}: {clean_msg}",
                duration_ms=round((time.perf_counter() - t0) * 1000, 2),
            )

        # Parse Google Vision Response
        return self._normalize_response(data, image_width, image_height, round((time.perf_counter() - t0) * 1000, 2))

    def _normalize_response(
        self,
        data: dict,
        image_width: int | None,
        image_height: int | None,
        duration_ms: float,
    ) -> OcrResult:
        responses = (data or {}).get("responses") or [{}]
        first = responses[0] if responses else {}
        if first.get("error"):
            msg = (first["error"] or {}).get("message", "Unknown Vision error")
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_PROVIDER_ERROR,
                failure_reason=f"Google Vision API error: {msg}",
                duration_ms=duration_ms,
            )

        full = first.get("fullTextAnnotation")
        if not full:
            # Check textAnnotations fallback
            anns = first.get("textAnnotations") or []
            if not anns:
                return OcrResult(
                    engine=self.name,
                    provider=self.name,
                    status=STATUS_NO_TEXT,
                    failure_reason="Google Vision returned no text detected.",
                    duration_ms=duration_ms,
                    source_width=image_width,
                    source_height=image_height,
                )
            # Fallback simple lines from textAnnotations
            raw_text = anns[0].get("description", "")
            lines = []
            for t in raw_text.splitlines():
                t = t.strip()
                if t:
                    lines.append(OcrLine(text=t, confidence=None, box=[[0.0, 0.0]], height_px=0.0))
            return OcrResult(
                lines=lines,
                engine=self.name,
                provider=self.name,
                status=STATUS_SUCCESS,
                source_width=image_width,
                source_height=image_height,
                duration_ms=duration_ms,
            )

        lines: list[OcrLine] = []
        blocks: list[OcrBlock] = []
        all_confs: list[float] = []

        for page in full.get("pages") or []:
            for block in page.get("blocks") or []:
                block_lines: list[OcrLine] = []
                block_words: list[str] = []
                block_confs: list[float] = []

                for para in block.get("paragraphs") or []:
                    para_words: list[OcrWord] = []
                    para_text_tokens: list[str] = []
                    para_confs: list[float] = []
                    line_xs: list[float] = []
                    line_ys: list[float] = []

                    for w in para.get("words") or []:
                        sym_text = "".join(s.get("text", "") for s in w.get("symbols") or [])
                        w_conf = None
                        if w.get("confidence") is not None:
                            try:
                                w_conf = float(w["confidence"])
                                para_confs.append(w_conf)
                                block_confs.append(w_conf)
                                all_confs.append(w_conf)
                            except (TypeError, ValueError):
                                pass

                        w_box: list[list[float]] = []
                        for v in (w.get("boundingBox") or {}).get("vertices") or []:
                            try:
                                vx = float(v.get("x", 0))
                                vy = float(v.get("y", 0))
                                w_box.append([vx, vy])
                                line_xs.append(vx)
                                line_ys.append(vy)
                            except (TypeError, ValueError):
                                continue

                        if sym_text.strip():
                            para_words.append(OcrWord(text=sym_text, confidence=w_conf, box=w_box or None))
                            para_text_tokens.append(sym_text)
                            block_words.append(sym_text)

                    line_text = " ".join(para_text_tokens).strip()
                    if line_text:
                        line_h = float(max(line_ys) - min(line_ys)) if line_ys else 0.0
                        line_quad = (
                            [
                                [min(line_xs), min(line_ys)],
                                [max(line_xs), min(line_ys)],
                                [max(line_xs), max(line_ys)],
                                [min(line_xs), max(line_ys)],
                            ]
                            if line_xs and line_ys
                            else [[0.0, 0.0]]
                        )
                        mean_p_conf = float(sum(para_confs) / len(para_confs)) if para_confs else None
                        ocr_line = OcrLine(
                            text=line_text,
                            confidence=mean_p_conf,
                            box=line_quad,
                            height_px=line_h,
                            words=para_words,
                        )
                        lines.append(ocr_line)
                        block_lines.append(ocr_line)

                b_text = " ".join(block_words).strip()
                if b_text:
                    b_conf = float(sum(block_confs) / len(block_confs)) if block_confs else None
                    blocks.append(OcrBlock(text=b_text, confidence=b_conf, lines=block_lines))

        if not lines:
            return OcrResult(
                engine=self.name,
                provider=self.name,
                status=STATUS_NO_TEXT,
                failure_reason="Google Vision returned no readable text.",
                duration_ms=duration_ms,
                source_width=image_width,
                source_height=image_height,
            )

        mean_overall = float(sum(all_confs) / len(all_confs)) if all_confs else None
        return OcrResult(
            lines=lines,
            blocks=blocks,
            engine=self.name,
            provider=self.name,
            status=STATUS_SUCCESS,
            mean_confidence=mean_overall,
            source_width=image_width,
            source_height=image_height,
            duration_ms=duration_ms,
        )
