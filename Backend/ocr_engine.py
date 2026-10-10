from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import re
import cv2
import numpy as np

from config import settings
from ocr import (
    AzureVisionProvider,
    BaseOCRProvider,
    GoogleVisionProvider,
    OCRSpaceProvider,
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
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
    get_ocr_provider,
)


SUPPORTED_INDIC_LANGS = {
    "en": "en",
    "english": "en",
    "hi": "hi",
    "hindi": "hi",
    "devanagari": "devanagari",
    "te": "te",
    "telugu": "te",
    "ta": "ta",
    "tamil": "ta",
    "kn": "kannada",
    "kannada": "kannada",
    "bn": "bn",
    "bengali": "bn",
    "mr": "mr",
    "marathi": "mr",
}


def get_paddle(lang: str = "en"):
    """Deprecated: PaddleOCR removed from runtime architecture (Render 512MB requirement).

    Supports OCR_PADDLE_LANG legacy signature check.
    """
    _lang = getattr(settings, "OCR_PADDLE_LANG", "en")
    raise RuntimeError(
        f"PaddleOCR is removed from the production runtime architecture (OCR_PADDLE_LANG={_lang}). "
        "Use cloud OCR provider abstraction (Google Cloud Vision / OCR.space / Azure Vision)."
    )


def deskew(gray: np.ndarray) -> tuple[np.ndarray, float]:
    """Rotate small residual skew out of a grey image."""
    thr = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)[1]
    pts = cv2.findNonZero(thr)
    if pts is None or len(pts) < 50:
        return gray, 0.0

    angle = cv2.minAreaRect(pts.astype(np.float32))[-1]
    if angle < -45:
        angle += 90
    elif angle > 45:
        angle -= 90
    if abs(angle) < 0.3 or abs(angle) > 15:
        return gray, 0.0

    h, w = gray.shape
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    out = cv2.warpAffine(
        gray, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
    )
    return out, float(angle)


def preprocess_for_ocr(bgr: np.ndarray) -> np.ndarray:
    """Returns the preprocessed array for image clarity."""
    gray = cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY)
    gray, _ = deskew(gray)
    gray = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    gray = cv2.bilateralFilter(gray, 7, 50, 50)
    return cv2.cvtColor(gray, cv2.COLOR_GRAY2BGR)


def _encode_bgr_to_jpeg(bgr: np.ndarray, quality: int = 80, max_dim: int = 1600) -> bytes:
    h, w = bgr.shape[:2]
    if max(h, w) > max_dim:
        scale = max_dim / float(max(h, w))
        bgr = cv2.resize(bgr, (max(1, int(round(w * scale))), max(1, int(round(h * scale)))), interpolation=cv2.INTER_AREA)
    ok, buf = cv2.imencode(".jpg", bgr, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise ValueError("Failed to encode BGR image to JPEG bytes")
    return buf.tobytes()


def run_ocr(
    image_input: np.ndarray | bytes | str | Path,
    lang: str | None = None,
    provider: str | None = None,
    panel: str | None = None,
    image_id: int | None = None,
    scan_id: int | None = None,
    inspection_id: int | None = None,
    options: dict[str, Any] | None = None,
) -> OcrResult:
    """Cloud OCR entry point. Dispatches to configured Cloud OCR provider.

    Accepts:
    - BGR numpy array (encoded to JPEG transport bytes)
    - Raw JPEG/PNG bytes
    - File path to derived artifact

    Never loads PaddleOCR or heavyweight local ML frameworks into memory.
    """
    w_px: int | None = None
    h_px: int | None = None

    if isinstance(image_input, (str, Path)):
        p = Path(image_input)
        if not p.exists():
            return OcrResult(
                engine="none",
                status=STATUS_INPUT_UNAVAILABLE,
                failure_reason=f"OCR input file missing: {p}",
                panel=panel,
                image_id=image_id,
                scan_id=scan_id,
                inspection_id=inspection_id,
            )
        img_bytes = p.read_bytes()
        try:
            from PIL import Image
            with Image.open(p) as im:
                w_px, h_px = im.width, im.height
        except Exception:
            pass
    elif isinstance(image_input, bytes):
        img_bytes = image_input
        try:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(img_bytes)) as im:
                w_px, h_px = im.width, im.height
        except Exception:
            pass
    elif isinstance(image_input, np.ndarray):
        h_px, w_px = image_input.shape[:2]
        img_bytes = _encode_bgr_to_jpeg(image_input)
    else:
        return OcrResult(
            engine="none",
            status=STATUS_INPUT_UNAVAILABLE,
            failure_reason=f"Unsupported image_input type: {type(image_input)}",
            panel=panel,
            image_id=image_id,
            scan_id=scan_id,
            inspection_id=inspection_id,
        )

    # Resolve Cloud OCR Provider
    ocr_provider = get_ocr_provider(provider)
    result = ocr_provider.recognize(
        image_bytes=img_bytes,
        image_width=w_px,
        image_height=h_px,
        options=options,
    )
    if panel is not None:
        result.panel = panel
    if image_id is not None:
        result.image_id = image_id
    if scan_id is not None:
        result.scan_id = scan_id
    if inspection_id is not None:
        result.inspection_id = inspection_id
    return result


DECLARED_FIELDS = (
    "manufacturer", "packer", "importer", "commodity", "net_quantity",
    "mrp", "date_of_manufacture", "best_before", "country_of_origin",
    "consumer_care", "batch_number", "unit_sale_price", "dimensions",
)

# Rule 6(1)(e) — MRP is inclusive of all taxes. The wording varies; the
# obligation does not.
MRP_PATTERNS = [
    r"(?:M\.?R\.?P\.?|Maximum\s+Retail\s+Price|एम\.?आर\.?पी\.?|अधिकतम\s+खुदरा\s+मूल्य|मूल्य|ధర|விலை)[^\d]{0,20}(\d+(?:[.,]\d{1,2})?)",
    r"(?:Rs\.?|INR|₹|रु\.?|రూ\.?)\s?(\d+(?:[.,]\d{1,2})?)",
]
NET_QTY_PATTERN = (
    r"(?:Net\s*(?:Qty|Quantity|Wt|Weight|Vol|Volume)|Contents|शुद्ध\s*(?:मात्रा|वज़न)|मात्रा|పరిమాణం|அளவு)"
    r"[^\d]{0,15}(\d+(?:[.,]\d+)?)\s*"
    r"(kg|g|gm|grams?|mg|l|litre|liters?|ltr|ml|m|cm|mm|pcs?|N|U|किग्रा|ग्राम|मिली|लीटर)\b"
)
INCL_TAXES = r"(?:incl(?:usive)?\.?\s+of\s+all\s+taxes|सभी\s+कर(?:ों)?\s+सहित|అన్ని\s+పన్నులతో\s+కలిపి)"
COUNTRY_PATTERN = r"(?:Country\s+of\s+Origin|Made\s+in|Origin|मूल\s*देश|ఉత్పత్తి\s*దేశం|பிறப்பிடம்)\s*[:\-]?\s*([A-Za-z \u0900-\u097F\u0C00-\u0C7F\u0B80-\u0BFF]{3,40})"
BEST_BEFORE_PATTERN = (
    r"(?:Best\s+Before|Use\s+By|Expiry|Exp\.?|उपयोग\s*की\s*अंतिम\s*तिथि|समाप्ति)\s*[:\-]?\s*"
    r"(\d{1,2}\s*(?:month|months|mth)s?|\d{1,2}[/\-]\d{2,4}|\d{1,2}\s+\w+\s+\d{2,4})"
)
CARE_PATTERN = (
    r"(?:Customer|Consumer|ग्राहक|उपभोक्ता|వినియోగదారు)\s+(?:Care|Service|Complaints?|सेवा|సహాయం)"
    r"[\s\S]{0,120}?((?:1800[\-\s]?\d{3}[\-\s]?\d{3,4}|1800\d{6,7})|(?:\+?91[\-\s]?)?[6-9]\d{9}|[\w.\-]+@[\w.\-]+\.\w{2,})"
)
# P0 fix: manufacturer/packer/importer + commodity + date were never extracted,
# so CHK01 always reported them missing. Patterns below are intentionally broad
# (recall over precision) — the rules engine decides violation, not this file.
MFR_PATTERNS = [
    r"(?:Mfd\.?\s*by|Manufactured\s*by|Mfg\.?\s*by|Packed\s*by|Imported\s*by|Marketed\s*by|Mfr\.?|Pkd\.?\s*by|Quality\s+products\s+from|Products?\s+from|Made\s+by)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 .,&\-/()]{3,120})",
    r"(?:Manufacturer|Packer|Importer)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 .,&\-/()]{3,120})",
    r"([A-Za-z0-9 .,&\-/()]{3,80}\s+(?:Pvt\.?\s*Ltd\.?|Private\s+Limited|Limited|Ltd\.?|Industries|Enterprises))",
]
COMMODITY_PATTERN = (
    r"(?:Commodity|Product|Item|Name\s*of\s*(?:the\s*)?(?:Commodity|Product))"
    r"\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9 .()\-/&]{2,80})"
)
DATE_PATTERNS = [
    r"(?:MFD|MFG|Mfd\.?|Mfg\.?|Manufactured|Packed\s*on|Pkd\.?|Date\s*of\s*(?:Mfg|Manufacture|Packing|Packaging|Import))\s*[:\-]?\s*(\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}|\d{1,2}[/\-.]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{2,4}|\d{2,4}\s*/\s*\d{1,2})",
    r"(?:Month\s*(?:and|&)\s*Year\s*of\s*(?:Manufacture|Mfg|Packing|Packaging|Import|Mfd))\s*[:\-]?\s*(\d{1,2}[/\-.]\d{2,4}|(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\s+\d{2,4}|\d{1,2}\s+\d{4})",
]
BATCH_PATTERN = r"(?:Batch\s*(?:No\.?|Number)?|Lot\s*(?:No\.?|Number)?|B\.?\s*No\.?)\s*[:\-]?\s*([A-Za-z0-9][A-Za-z0-9\-/]{1,30})"
DIMENSIONS_PATTERN = (
    r"(?:Dimensions?|Size)\s*[:\-]?\s*(\d+(?:[.,]\d+)?\s*(?:cm|mm|m|inch|in)\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:cm|mm|m|inch|in)(?:\s*[x×]\s*\d+(?:[.,]\d+)?\s*(?:cm|mm|m|inch|in))?)"
)


@dataclass(slots=True)
class ExtractedField:
    value: str | None
    confidence: float | None
    source_line: str | None
    found: bool


def extract_fields(ocr: OcrResult) -> dict[str, ExtractedField]:
    """Absence is reported as absence. It is never reported as a violation here.

    Whether a missing field is a violation depends on the exemptions in Rule 3
    and Rule 26 and on the transaction type, and none of that is knowable in
    this function. The rules engine decides. This function only reports what
    the text does and does not contain.
    """
    text = ocr.full_text
    out: dict[str, ExtractedField] = {}

    def put(name: str, m: re.Match | None, group: int = 1):
        if m:
            line = _line_for(ocr, m.group(0))
            out[name] = ExtractedField(
                value=m.group(group).strip(),
                confidence=line.confidence if line else ocr.mean_confidence,
                source_line=line.text if line else None,
                found=True,
            )
        else:
            out[name] = ExtractedField(None, None, None, False)

    mrp_m = next((m for p in MRP_PATTERNS if (m := re.search(p, text, re.I))), None)
    put("mrp", mrp_m)
    put("net_quantity", re.search(NET_QTY_PATTERN, text, re.I))
    put("country_of_origin", re.search(COUNTRY_PATTERN, text, re.I))
    put("best_before", re.search(BEST_BEFORE_PATTERN, text, re.I))
    put("consumer_care", re.search(CARE_PATTERN, text, re.I))
    # P0 fix: previously these Rule-6 fields were never populated, so CHK01
    # always failed manufacturer/commodity/date. Broad recall patterns above.
    mfr_m = next((m for p in MFR_PATTERNS if (m := re.search(p, text, re.I))), None)
    put("manufacturer", mfr_m)
    # packer/importer share the manufacturer evidence: any of the three
    # satisfies "name+address of manufacturer/packer/importer" presence.
    if mfr_m:
        line = _line_for(ocr, mfr_m.group(0))
        for alias in ("packer", "importer"):
            out[alias] = ExtractedField(
                value=mfr_m.group(1).strip(),
                confidence=line.confidence if line else ocr.mean_confidence,
                source_line=line.text if line else None,
                found=True,
            )
    else:
        out["packer"] = ExtractedField(None, None, None, False)
        out["importer"] = ExtractedField(None, None, None, False)
    put("commodity", re.search(COMMODITY_PATTERN, text, re.I))
    date_m = next((m for p in DATE_PATTERNS if (m := re.search(p, text, re.I))), None)
    put("date_of_manufacture", date_m)
    put("batch_number", re.search(BATCH_PATTERN, text, re.I))
    put("dimensions", re.search(DIMENSIONS_PATTERN, text, re.I))
    # Fallback: bare brand line (e.g. "Parle Hide & Seek") with no
    # "Commodity:" prefix. Use the longest alpha line that is not MRP/net-qty/
    # date/care, flagged at half confidence so the engine can weigh it.
    # (Runs after date/batch puts above — it reads their source_lines.)
    if not out["commodity"].found:
        try:
            _skip = (out["mrp"].source_line or "", out["net_quantity"].source_line or "",
                     out["date_of_manufacture"].source_line or "",
                     out["consumer_care"].source_line or "")
            _cands = [l for l in ocr.lines
                      if l.text and len(l.text.strip()) >= 3
                      and l.text not in _skip
                      and not re.search(r"(MRP|Net\s*(Qty|Quantity|Wt)|MFD|MFG|Batch|Customer|Consumer)", l.text, re.I)
                      and re.search(r"[A-Za-z]{3,}", l.text)]
            if _cands:
                _best = max(_cands, key=lambda l: len(l.text.strip()))
                _c = (0.5 * _best.confidence) if _best.confidence else 0.3
                out["commodity"] = ExtractedField(
                    value=_best.text.strip()[:80], confidence=_c,
                    source_line=_best.text, found=True)
        except Exception:
            pass

    out["net_quantity_unit"] = ExtractedField(
        (re.search(NET_QTY_PATTERN, text, re.I).group(2)
         if re.search(NET_QTY_PATTERN, text, re.I) else None),
        None, None,
        bool(re.search(NET_QTY_PATTERN, text, re.I)),
    )
    out["mrp_inclusive_wording"] = ExtractedField(
        "present" if re.search(INCL_TAXES, text, re.I) else None,
        None, None,
        bool(re.search(INCL_TAXES, text, re.I)),
    )
    return out

def _line_for(ocr: OcrResult, fragment: str) -> OcrLine | None:
    frag = fragment.strip().lower()[:24]
    return next((l for l in ocr.lines if frag and frag in l.text.lower()), None)


def read_barcode(bgr: np.ndarray) -> str | None:
    try:
        from pyzbar.pyzbar import decode
    except ImportError:
        return None       # libzbar0 absent; barcode is an aid, not a requirement
    for sym in decode(bgr):
        return sym.data.decode("utf-8", errors="replace")
    return None
