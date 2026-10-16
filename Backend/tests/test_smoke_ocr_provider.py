"""Backend/tests/test_smoke_ocr_provider.py — Module 7.4 Automated Tests.

Validates:
1. External calls blocked without explicit '--live-provider-call' opt-in.
2. Missing or corrupt image path handling.
3. Successful OCR response reporting using existing provider chain.
4. Fallback occurrence detection and reporting.
5. All-provider failure reporting.
6. Zero-usable-text handling.
7. Safe error and secret redaction (no leaked keys/tokens or raw OCR text).
8. Zero database writes (no inspections, scans, or findings created).
9. CLI entrypoint with human-readable and JSON output modes.
"""
from __future__ import annotations

import io
import json
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from PIL import Image, ImageDraw

from config import settings
from database import SessionLocal
from models import Finding, Inspection, Scan, ScanImage
from ocr.base import (
    OcrLine,
    OcrResult,
    STATUS_AUTH_ERROR,
    STATUS_INPUT_UNAVAILABLE,
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
)
from scripts.smoke_ocr_provider import main, run_smoke_ocr


@pytest.fixture
def dummy_image_file():
    """Create a temporary real JPEG image with dummy label content."""
    img = Image.new("RGB", (640, 480), color=(250, 250, 245))
    draw = ImageDraw.Draw(img)
    draw.text((20, 30), "TEST PRODUCT NET QTY 500 g", fill=(10, 10, 10))
    with tempfile.NamedTemporaryFile(suffix=".jpg", prefix="smoke_test_", delete=False) as tf:
        img.save(tf, format="JPEG", quality=85)
        path = tf.name
    yield path
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def test_external_calls_blocked_without_explicit_opt_in(dummy_image_file):
    """Requirement 3: Without --live-provider-call, no external provider calls can occur."""
    with patch("ocr.factory.get_ocr_provider") as mock_get_provider:
        exit_code, summary = run_smoke_ocr(dummy_image_file, live_provider_call=False)

        assert exit_code == 2
        assert summary["status"] == "BLOCKED_OPT_IN_REQUIRED"
        assert summary["success"] is False
        assert "strictly required" in summary["error"]
        # Crucial: provider factory and provider recognize must NOT be invoked
        mock_get_provider.assert_not_called()


def test_missing_image_path_handling():
    """Requirement 4: Nonexistent image path returns clean failure without crash."""
    non_existent = "non_existent_file_path_12345.jpg"
    exit_code, summary = run_smoke_ocr(non_existent, live_provider_call=True)

    assert exit_code == 1
    assert summary["status"] == STATUS_INPUT_UNAVAILABLE
    assert summary["success"] is False
    assert "does not exist" in summary["error"]


def test_corrupt_image_path_handling():
    """Requirement 4: Non-image file returns clean failure without crash."""
    with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as tf:
        tf.write(b"NOT_A_VALID_JPEG_HEADER_CORRUPTED_BYTES")
        corrupt_path = tf.name

    try:
        exit_code, summary = run_smoke_ocr(corrupt_path, live_provider_call=True)
        assert exit_code == 1
        assert summary["status"] == STATUS_INPUT_UNAVAILABLE
        assert summary["success"] is False
        assert "Invalid or unreadable image format" in summary["error"]
    finally:
        try:
            os.remove(corrupt_path)
        except OSError:
            pass


def test_successful_ocr_response_reporting(dummy_image_file):
    """Requirement 2: Successful OCR extraction reports provider, line count, and safe confidence."""
    mock_provider = MagicMock()
    mock_provider.name = "ocr_space"
    mock_provider.is_configured.return_value = True

    mock_result = OcrResult(
        provider="ocr_space",
        engine="ocr_space",
        status=STATUS_SUCCESS,
        lines=[
            OcrLine(text="BRAND NAME RICE", confidence=0.98),
            OcrLine(text="NET QTY 1 kg", confidence=0.95),
            OcrLine(text="MRP Rs 120.00", confidence=0.92),
        ],
        mean_confidence=0.95,
        duration_ms=450.0,
        attempts=[
            {"provider": "google_vision", "status": STATUS_SKIPPED, "category": "skipped_unconfigured", "duration_ms": 0.0},
            {"provider": "ocr_space", "status": STATUS_SUCCESS, "category": "success", "duration_ms": 450.0},
        ],
    )
    mock_provider.recognize.return_value = mock_result

    with patch("ocr.factory.get_ocr_provider", return_value=mock_provider):
        exit_code, summary = run_smoke_ocr(dummy_image_file, live_provider_call=True)

        assert exit_code == 0
        assert summary["success"] is True
        assert summary["status"] == STATUS_SUCCESS
        assert summary["provider"] == "ocr_space"
        assert summary["line_count"] == 3
        assert summary["mean_confidence"] == 0.95
        assert summary["fallback_occurred"] is True
        assert summary["dimensions"] == "640x480"
        # Invariant: No raw OCR line text in summary dictionary
        assert "BRAND NAME RICE" not in json.dumps(summary)
        assert "NET QTY 1 kg" not in json.dumps(summary)


def test_zero_usable_text_handling(dummy_image_file):
    """Requirement 2 & 4: Zero extracted lines returns nonzero exit code."""
    mock_provider = MagicMock()
    mock_provider.name = "ocr_space"
    mock_provider.is_configured.return_value = True

    mock_result = OcrResult(
        provider="ocr_space",
        engine="ocr_space",
        status=STATUS_NO_TEXT,
        lines=[],
        mean_confidence=None,
        duration_ms=300.0,
        attempts=[{"provider": "ocr_space", "status": STATUS_NO_TEXT, "category": "no_text", "duration_ms": 300.0}],
    )
    mock_provider.recognize.return_value = mock_result

    with patch("ocr.factory.get_ocr_provider", return_value=mock_provider):
        exit_code, summary = run_smoke_ocr(dummy_image_file, live_provider_call=True)

        assert exit_code == 1
        assert summary["success"] is False
        assert summary["status"] == STATUS_NO_TEXT
        assert summary["line_count"] == 0


def test_all_providers_failure_reporting(dummy_image_file):
    """Requirement 2: When all providers fail, failure reason and attempts trace are reported safely."""
    mock_provider = MagicMock()
    mock_provider.name = "auto"
    mock_provider.is_configured.return_value = True

    mock_result = OcrResult(
        provider="none",
        engine="none",
        status=STATUS_PROVIDER_ERROR,
        failure_reason="All OCR providers failed: google_vision (timeout); ocr_space (rate limited)",
        lines=[],
        duration_ms=800.0,
        attempts=[
            {"provider": "google_vision", "status": "OCR_TIMEOUT", "category": "timeout", "duration_ms": 500.0, "failure_reason": "Timeout"},
            {"provider": "ocr_space", "status": "OCR_RATE_LIMITED", "category": "rate_limited", "duration_ms": 300.0, "failure_reason": "Rate limited"},
        ],
    )
    mock_provider.recognize.return_value = mock_result

    with patch("ocr.factory.get_ocr_provider", return_value=mock_provider):
        exit_code, summary = run_smoke_ocr(dummy_image_file, live_provider_call=True)

        assert exit_code == 1
        assert summary["success"] is False
        assert summary["status"] == STATUS_PROVIDER_ERROR
        assert summary["line_count"] == 0
        assert "All OCR providers failed" in summary["failure_reason"]
        assert len(summary["attempts"]) == 2


def test_safe_error_and_secret_redaction(dummy_image_file):
    """Requirement 2: API keys, tokens, and credentials are scrubbed from diagnostic outputs."""
    raw_secret_key = "K89123456789012"
    bearer_token = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.dummy_token_1234567"

    mock_provider = MagicMock()
    mock_provider.name = "ocr_space"
    mock_provider.is_configured.return_value = True

    # Leaked secret in raw failure reason
    leak_reason = f"Provider rejected request with key={raw_secret_key} and Bearer {bearer_token}"
    mock_result = OcrResult(
        provider="ocr_space",
        status=STATUS_AUTH_ERROR,
        failure_reason=leak_reason,
        lines=[],
        attempts=[{"provider": "ocr_space", "status": STATUS_AUTH_ERROR, "failure_reason": leak_reason}],
    )
    mock_provider.recognize.return_value = mock_result

    with patch("ocr.factory.get_ocr_provider", return_value=mock_provider):
        exit_code, summary = run_smoke_ocr(dummy_image_file, live_provider_call=True)

        serialized = json.dumps(summary)
        assert raw_secret_key not in serialized
        assert bearer_token not in serialized
        assert "[REDACTED]" in serialized


def test_smoke_test_performs_zero_database_writes(dummy_image_file):
    """Requirement 2 & 4: Smoke test performs zero DB writes and creates no records."""
    db = SessionLocal()
    try:
        insp_count_before = db.query(Inspection).count()
        scan_count_before = db.query(Scan).count()
        find_count_before = db.query(Finding).count()
        img_count_before = db.query(ScanImage).count()

        mock_provider = MagicMock()
        mock_provider.name = "ocr_space"
        mock_provider.is_configured.return_value = True
        mock_provider.recognize.return_value = OcrResult(
            provider="ocr_space",
            status=STATUS_SUCCESS,
            lines=[OcrLine(text="NET WT 500 g", confidence=0.99)],
        )

        with patch("ocr.factory.get_ocr_provider", return_value=mock_provider):
            exit_code, summary = run_smoke_ocr(dummy_image_file, live_provider_call=True)
            assert exit_code == 0

        # Assert count invariant
        assert db.query(Inspection).count() == insp_count_before
        assert db.query(Scan).count() == scan_count_before
        assert db.query(Finding).count() == find_count_before
        assert db.query(ScanImage).count() == img_count_before
    finally:
        db.close()


def test_cli_main_entrypoint(dummy_image_file, capsys):
    """Test CLI invocation with human-readable and JSON output formats."""
    # 1. Blocked mode
    code_blocked = main([dummy_image_file])
    captured_blocked = capsys.readouterr().out
    assert code_blocked == 2
    assert "RESULT: BLOCKED" in captured_blocked
    assert "--live-provider-call" in captured_blocked

    # 2. JSON mode (mocked success)
    mock_provider = MagicMock()
    mock_provider.name = "ocr_space"
    mock_provider.is_configured.return_value = True
    mock_provider.recognize.return_value = OcrResult(
        provider="ocr_space",
        status=STATUS_SUCCESS,
        lines=[OcrLine(text="PARLE G", confidence=0.98)],
        mean_confidence=0.98,
    )

    with patch("ocr.factory.get_ocr_provider", return_value=mock_provider):
        code_json = main([dummy_image_file, "--live-provider-call", "--json"])
        captured_json = capsys.readouterr().out
        assert code_json == 0
        parsed = json.loads(captured_json)
        assert parsed["status"] == STATUS_SUCCESS
        assert parsed["line_count"] == 1
        assert parsed["provider"] == "ocr_space"
