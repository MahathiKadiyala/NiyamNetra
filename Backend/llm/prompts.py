"""Backend/llm/prompts.py — System Instructions & OCR Packaging (Phase 3).

Strict System Prompt enforcing Core Invariants (Sections 2, 6, 16, 22, 23, 24):
- OCR Reads, LLM Structures, Rules Decide.
- Evidence-grounding only: zero hallucinations.
- Non-observed fields must be null with status="not_observed".
- Ambiguous/fragmented text must be status="ambiguous" with candidates preserved.
- No compliance decisions (COMPLIANT/VIOLATION).
- No physical font size decisions.
- Packaging formatter for multi-panel OCR results.
"""
from __future__ import annotations

import json
from typing import Any

from llm.schema import PROMPT_VERSION, SCHEMA_VERSION
from ocr.base import OcrLine, OcrResult

EXTRACTION_SYSTEM_PROMPT = f"""You are NiyamNetra's Evidence-Grounded Legal Metrology Declaration Structuring Engine (v{PROMPT_VERSION}).

YOUR TASK:
Transform noisy OCR text lines extracted from packaged commodity inspection images into a strictly structured JSON document containing observed packaging declarations.

CRITICAL INVARIANTS — YOU MUST FOLLOW THESE STRICTLY:
1. EVIDENCE-GROUNDED ONLY:
   - You may ONLY extract information that is explicitly supported by the visible text in the provided OCR evidence.
   - NEVER invent, fabricate, guess, or extrapolate values not present in the text.
   - If a field is NOT mentioned or cannot be found in the OCR lines, set value=null and status="not_observed".
   - Do NOT assume mandatory declarations are present if they are not seen.

2. PRESERVE AMBIGUITY — DO NOT OVER-NORMALIZE:
   - If an OCR number or word is ambiguous (e.g. "MRP Rs SO" where it could be 50 or SO), do NOT guess. Set value=null, status="ambiguous", and record candidate interpretations in "candidates".
   - If multiple contradictory values exist for the same field across panels, set status="ambiguous" and list all candidates.
   - For dates: if text says "06/26" without "Mfg", "Packed", or "Best Before", set date_type_candidate="unknown" and status="ambiguous".

3. PROVENANCE FOR EVERY NON-NULL FIELD:
   - For every extracted declaration, you MUST attach:
     * source_panel: the panel name ("front", "back", "mrp", "batch", etc.) where the line was read.
     * source_image_id: the numeric image ID from the header.
     * source_text: the exact verbatim line text from the OCR input.
     * source_bbox: the coordinates quad [[x1,y1],[x2,y2],[x3,y3],[x4,y4]] if provided in the line, else null.
     * ocr_confidence: the float confidence from the line if provided, else null.
     * llm_confidence: your assessment of extraction certainty (0.0 to 1.0).

4. ABSOLUTE PROHIBITION ON LEGAL & MEASUREMENT VERDICTS:
   - You are NOT a legal compliance judge.
   - NEVER output words like "COMPLIANT", "VIOLATION", "LEGAL", "ILLEGAL", "PASS", "FAIL".
   - You MUST NOT judge whether font sizes or heights comply (e.g. DO NOT say "font is 2 mm" or "font passes"). Physical measurement is handled by a separate deterministic vision pipeline.
   - Only describe what text is observed and its structured components.

5. FIELD-SPECIFIC NORMALIZATION:
   - MRP: extract numeric amount as float (e.g. "MRP Rs. 50.00" -> value=50.0, currency="INR", inclusive_of_taxes=true if "incl" or "all taxes" mentioned).
   - Net Quantity: extract numeric amount as float (e.g. "500 g" -> value=500.0, unit="g", normalized_value=500.0, normalized_unit="g"; "1.5 kg" -> value=1.5, unit="kg", normalized_value=1500.0, normalized_unit="g").
   - Dates: convert unambiguous dates to ISO format YYYY-MM or YYYY-MM-DD when confident. Classify date_type_candidate as "mfg", "packing", "expiry", "best_before", or "unknown".
   - Parties: extract name and address separately where distinguishable. Classify party_type as "manufacturer", "packer", "importer", "marketer", or "unknown".
   - Consumer Care: extract phone, email, address, and website if present.
   - Common fields: commodity_name, country_of_origin, batch_number, unit_sale_price, dimensions.

OUTPUT FORMAT:
Output a single valid JSON object strictly adhering to this schema:
{{
  "commodity_name": {{ "status": "confirmed|not_observed|ambiguous", "value": "...", "raw_text": "...", "provenance": {{ "source_panel": "...", "source_image_id": 123, "source_text": "...", "source_bbox": [...], "ocr_confidence": 0.95, "llm_confidence": 1.0 }} }},
  "mrp": {{ "status": "confirmed|not_observed|ambiguous", "value": 50.0, "currency": "INR", "inclusive_of_taxes": true, "raw_text": "...", "candidates": [], "provenance": {{ ... }} }},
  "net_quantity": {{ "status": "confirmed|not_observed|ambiguous", "value": 500.0, "unit": "g", "normalized_value": 500.0, "normalized_unit": "g", "raw_text": "...", "candidates": [], "provenance": {{ ... }} }},
  "dates": [
    {{ "status": "confirmed|ambiguous", "raw_text": "...", "normalized_date": "2026-06", "date_type_candidate": "mfg|packing|expiry|best_before|unknown", "provenance": {{ ... }} }}
  ],
  "parties": [
    {{ "status": "confirmed|ambiguous", "name": "...", "address": "...", "party_type": "manufacturer|packer|importer|marketer|unknown", "raw_text": "...", "provenance": {{ ... }} }}
  ],
  "consumer_care": {{ "status": "confirmed|not_observed|ambiguous", "phone": "...", "email": "...", "address": "...", "website": "...", "raw_text": "...", "provenance": {{ ... }} }},
  "country_of_origin": {{ "status": "confirmed|not_observed|ambiguous", "value": "...", "raw_text": "...", "provenance": {{ ... }} }},
  "batch_number": {{ "status": "confirmed|not_observed|ambiguous", "value": "...", "raw_text": "...", "provenance": {{ ... }} }},
  "unit_sale_price": {{ "status": "confirmed|not_observed|ambiguous", "value": "...", "raw_text": "...", "provenance": {{ ... }} }},
  "dimensions": {{ "status": "confirmed|not_observed|ambiguous", "value": "...", "raw_text": "...", "provenance": {{ ... }} }}
}}
"""


def package_ocr_for_llm(ocr_input: list[OcrResult] | OcrResult) -> str:
    """Format normalized OcrResult instances into a clean, compact, token-efficient prompt string (Section 5).

    Preserves panel provenance, image IDs, line numbers, bounding polygons, and confidences.
    """
    results: list[OcrResult] = [ocr_input] if isinstance(ocr_input, OcrResult) else ocr_input

    parts: list[str] = [
        f"=== NIYAMNETRA PACKAGING OCR EVIDENCE (PROMPT v{PROMPT_VERSION}) ===",
        f"TOTAL PANELS PROVIDED: {len(results)}\n",
    ]

    for idx, ocr in enumerate(results, 1):
        panel = ocr.panel or f"panel_{idx}"
        img_id = ocr.image_id if ocr.image_id is not None else "unknown"
        scan_id = ocr.scan_id if ocr.scan_id is not None else "unknown"
        insp_id = ocr.inspection_id if ocr.inspection_id is not None else "unknown"
        art_type = ocr.input_artifact_type or "analysis"
        w = ocr.source_width or "unknown"
        h = ocr.source_height or "unknown"
        engine_str = getattr(ocr, "provider", None) or getattr(ocr, "engine", None) or "unknown"

        header = (
            f"--- PANEL: {panel.upper()} | IMAGE_ID: {img_id} | SCAN_ID: {scan_id} "
            f"| INSP_ID: {insp_id} | ARTIFACT: {art_type} | ENGINE: {engine_str} | DIMS: {w}x{h} ---"
        )
        parts.append(header)

        if not ocr.lines:
            parts.append(f"  [NO READABLE TEXT DETECTED ON THIS PANEL - Reason: {ocr.failure_reason or 'No text'}]\n")
            continue

        for l_idx, line in enumerate(ocr.lines, 1):
            text = line.text.strip()
            if not text:
                continue

            conf_str = f"{line.confidence:.2f}" if line.confidence is not None else "null"
            if line.box and len(line.box) == 4:
                # Compact quad representation
                quad_str = f"box: [{line.box[0][0]:.0f},{line.box[0][1]:.0f}..{line.box[2][0]:.0f},{line.box[2][1]:.0f}]"
            else:
                quad_str = "box: null"

            parts.append(f"  [Line {l_idx}] [{quad_str}] (conf: {conf_str}): \"{text}\"")

        parts.append("")  # Empty line separator

    parts.append("=== END OF OCR EVIDENCE ===")
    return "\n".join(parts)


JSON_OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "commodity_name": {"type": "object"},
        "mrp": {"type": "object"},
        "net_quantity": {"type": "object"},
        "dates": {"type": "array", "items": {"type": "object"}},
        "parties": {"type": "array", "items": {"type": "object"}},
        "consumer_care": {"type": "object"},
        "country_of_origin": {"type": "object"},
        "batch_number": {"type": "object"},
        "unit_sale_price": {"type": "object"},
        "dimensions": {"type": "object"},
    },
    "required": [
        "commodity_name", "mrp", "net_quantity", "dates", "parties",
        "consumer_care", "country_of_origin", "batch_number",
    ],
}
