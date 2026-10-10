"""Backend/ocr/security.py — Credential protection and diagnostic sanitization.

Guarantees:
- Provider credentials (API keys, tokens, endpoints) are never leaked in logs,
  diagnostic traces, exception strings, or serialized errors.
- Client frontends never receive server secrets.
"""
from __future__ import annotations

import re
from typing import Any

# Regex patterns matching common credential formats and parameters
_SECRET_PATTERNS = [
    re.compile(r"(?i)(?:key|apikey|api_key|secret|token|password)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"(?i)bearer\s+([a-zA-Z0-9_\-\.]{8,})"),
    re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    re.compile(r"(?i)(?:Ocp-Apim-Subscription-Key)\s*[:=]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"(?i)https?://[^\s]*[?&](?:key|apikey|api_key)=([a-zA-Z0-9_\-\.]{6,})"),
]


def sanitize_sensitive_text(text: str | None) -> str | None:
    """Scrub sensitive credentials, tokens, and keys from strings."""
    if not text:
        return text
    sanitized = str(text)

    # 1. Direct redaction of known configured secrets from settings
    try:
        from config import settings
        secret_attrs = (
            "GOOGLE_VISION_API_KEY",
            "OCR_SPACE_API_KEY",
            "AZURE_VISION_KEY",
            "GROQ_API_KEY",
            "GEMINI_API_KEY",
            "JWT_SECRET",
            "SUPABASE_SERVICE_KEY",
        )
        for attr in secret_attrs:
            val = getattr(settings, attr, None)
            if val and isinstance(val, str) and len(val.strip()) >= 4:
                sanitized = sanitized.replace(val.strip(), "[REDACTED]")
    except Exception:
        pass

    # 2. Pattern-based scrubbing
    for pat in _SECRET_PATTERNS:
        def _repl(m: re.Match) -> str:
            full = m.group(0)
            if m.lastindex:
                secret_val = m.group(1)
                return full.replace(secret_val, "[REDACTED]")
            return "[REDACTED]"
        sanitized = pat.sub(_repl, sanitized)

    return sanitized
