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
    # Parameterized keys/tokens: key=value, key: value, key "value", key 'value', key value
    re.compile(r"(?i)(?:key|apikey|api_key|secret|token|password)\s*[:=\s]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    # Bearer tokens
    re.compile(r"(?i)(?:bearer\s+)([a-zA-Z0-9_\-\.]{8,})"),
    # Authorization header
    re.compile(r"(?i)(?:authorization\s*[:=\s]\s*['\"]?(?:bearer\s+)?)['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    # Google API Key format
    re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b"),
    # Google API Key in headers
    re.compile(r"(?i)(?:x-goog-api-key)\s*[:=\s]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    # OCR.space API Key format (e.g. K89123456789012)
    re.compile(r"\bK\d{8,}[a-zA-Z0-9]*\b"),
    # Azure Vision Key (Ocp-Apim-Subscription-Key or 32-hex key)
    re.compile(r"(?i)(?:ocp-apim-subscription-key|subscription[_-]?key)\s*[:=\s]\s*['\"]?([a-zA-Z0-9_\-\.]{8,})['\"]?"),
    re.compile(r"(?i)(?:azure[_-]?vision[_-]?key)\s*[:=\s]\s*['\"]?([a-fA-F0-9]{32})['\"]?"),
    # URL query parameter keys
    re.compile(r"(?i)([?&](?:key|apikey|api_key|token|subscription-key)=)[^&\s'\"]+"),
    # Basic auth in URLs: ://user:password@host
    re.compile(r"://([^:]*):([^@\s]{4,})@"),
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

        db_url = getattr(settings, "DATABASE_URL", None)
        if db_url and isinstance(db_url, str):
            import urllib.parse
            try:
                parsed = urllib.parse.urlsplit(db_url)
                if parsed.password:
                    raw_pw = parsed.password
                    unquoted_pw = urllib.parse.unquote(raw_pw)
                    if len(raw_pw) >= 4:
                        sanitized = sanitized.replace(raw_pw, "[REDACTED]")
                    if len(unquoted_pw) >= 4:
                        sanitized = sanitized.replace(unquoted_pw, "[REDACTED]")
            except Exception:
                pass
    except Exception:
        pass

    # 2. Pattern-based scrubbing
    for pat in _SECRET_PATTERNS:
        def _repl(m: re.Match) -> str:
            full = m.group(0)
            if m.lastindex:
                if m.lastindex == 2 and "://" in full and "@" in full:
                    return f"://{m.group(1)}:[REDACTED]@"
                if full.startswith(("?", "&")) and m.group(1):
                    return f"{m.group(1)}[REDACTED]"
                secret_val = m.group(m.lastindex)
                return full.replace(secret_val, "[REDACTED]")
            return "[REDACTED]"
        sanitized = pat.sub(_repl, sanitized)

    return sanitized


def sanitize_diagnostic_data(data: Any) -> Any:
    """Recursively scrub sensitive text from dictionaries, lists, and primitives."""
    if data is None:
        return None
    if isinstance(data, str):
        return sanitize_sensitive_text(data)
    if isinstance(data, dict):
        sanitized_dict = {}
        for k, v in data.items():
            k_lower = str(k).lower()
            if any(s in k_lower for s in ("key", "secret", "token", "password", "credential", "auth_header")):
                sanitized_dict[k] = "[REDACTED]" if v else v
            else:
                sanitized_dict[k] = sanitize_diagnostic_data(v)
        return sanitized_dict
    if isinstance(data, list):
        return [sanitize_diagnostic_data(item) for item in data]
    if isinstance(data, tuple):
        return tuple(sanitize_diagnostic_data(item) for item in data)
    return data
