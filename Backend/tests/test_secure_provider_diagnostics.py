"""Backend/tests/test_secure_provider_diagnostics.py — Module 10: Secure Provider Configuration and Diagnostics.

Validates:
1. Health response when required provider credentials are absent.
2. Configured-provider readiness without a false claim of live verification.
3. Safe diagnostic behavior after a successful mocked provider request.
4. Google Vision authorization/billing failure followed by successful fallback, using test doubles.
5. Missing fallback credentials and correct safe-skip behavior.
6. Redaction of keys embedded in provider exception strings or URLs.
7. Redaction of authorization headers and tokens from logs and serialized errors.
8. Absence of secret values from health responses, diagnostic responses, and OCR errors.
9. Distinction between missing configuration and actual provider failure.
10. Unsupported or invalid provider configuration.
11. Correct OCR_PROVIDER=auto and explicit-provider behavior.
12. Continued operation of existing fallback and OCR-failure legal-safety tests.
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from config import settings
from main import app
from ocr.azure_vision import AzureVisionProvider
from ocr.base import (
    BaseOCRProvider,
    OcrLine,
    OcrResult,
    STATUS_AUTH_ERROR,
    STATUS_BILLING_ERROR,
    STATUS_NO_TEXT,
    STATUS_PROVIDER_ERROR,
    STATUS_RATE_LIMITED,
    STATUS_SKIPPED,
    STATUS_SUCCESS,
    STATUS_TIMEOUT,
    STATUS_UNAVAILABLE,
)
from ocr.diagnostics import (
    get_ocr_health_diagnostics,
    record_provider_failure,
    record_provider_fallback,
    record_provider_success,
    reset_diagnostic_records,
)
from ocr.fallback import FallbackOCRProvider
from ocr.factory import get_candidate_providers, get_ocr_provider
from ocr.google_vision import GoogleVisionProvider
from ocr.ocr_space import OCRSpaceProvider
from ocr.security import sanitize_diagnostic_data, sanitize_sensitive_text


# Controlled mock provider test double
class MockTestProvider(BaseOCRProvider):
    def __init__(
        self,
        name: str,
        configured: bool = True,
        result: OcrResult | None = None,
        exc: Exception | None = None,
    ):
        self.name = name
        self._configured = configured
        self._result = result
        self._exc = exc
        self.call_count = 0

    def is_configured(self) -> bool:
        return self._configured

    def recognize(self, image_bytes, image_width=None, image_height=None, options=None):
        self.call_count += 1
        if self._exc:
            raise self._exc
        if self._result is not None:
            return self._result
        return OcrResult(engine=self.name, provider=self.name, status=STATUS_SUCCESS)


@pytest.fixture(autouse=True)
def clean_diagnostics_state():
    """Reset diagnostic telemetry state before and after each test."""
    reset_diagnostic_records()
    yield
    reset_diagnostic_records()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=False)


# ---------------------------------------------------------------------------
# Test 1: Health response when required provider credentials are absent
# ---------------------------------------------------------------------------
def test_health_response_when_required_provider_credentials_absent(client, monkeypatch):
    """When no provider credentials are configured, /health reports unconfigured without crashing."""
    monkeypatch.setattr(settings, "GOOGLE_VISION_API_KEY", None)
    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", None)
    monkeypatch.setattr(settings, "AZURE_VISION_KEY", None)
    monkeypatch.setattr(settings, "OCR_PROVIDER", "auto")

    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()

    assert "ocr" in data
    ocr_diag = data["ocr"]
    assert ocr_diag["configured"] is False
    assert ocr_diag["readiness"] == "unconfigured"
    assert ocr_diag["verification_status"] == "missing_configuration"
    assert ocr_diag["live_verified"] is False
    assert ocr_diag["verification_source"] == "none"


# ---------------------------------------------------------------------------
# Test 2: Configured-provider readiness without a false claim of live verification
# ---------------------------------------------------------------------------
def test_configured_provider_readiness_without_false_live_verification(client, monkeypatch):
    """When credentials are provided in settings, provider is ready but NOT falsely claimed as live verified."""
    dummy_key = "K81111111111111"
    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", dummy_key)
    monkeypatch.setattr(settings, "GOOGLE_VISION_API_KEY", None)
    monkeypatch.setattr(settings, "AZURE_VISION_KEY", None)
    monkeypatch.setattr(settings, "OCR_PROVIDER", "auto")

    resp = client.get("/health")
    assert resp.status_code == 200
    ocr_diag = resp.json()["ocr"]

    assert ocr_diag["configured"] is True
    assert ocr_diag["readiness"] == "ready_unverified"
    assert ocr_diag["verification_status"] == "configured_not_live_verified"
    assert ocr_diag["live_verified"] is False
    assert ocr_diag["verification_source"] == "none"
    assert dummy_key not in resp.text


# ---------------------------------------------------------------------------
# Test 3: Safe diagnostic behavior after a successful mocked provider request
# ---------------------------------------------------------------------------
def test_safe_diagnostic_behavior_after_successful_mocked_request(client, monkeypatch):
    """After a recorded request succeeds, readiness reflects verification and discloses no secrets."""
    dummy_key = "K82222222222222"
    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", dummy_key)

    record_provider_success(provider="ocr_space", duration_ms=142.5, line_count=8, is_test=True)

    resp = client.get("/health")
    assert resp.status_code == 200
    ocr_diag = resp.json()["ocr"]

    assert ocr_diag["configured"] is True
    assert ocr_diag["live_verified"] is True
    assert ocr_diag["readiness"] == "live_verified"
    assert ocr_diag["verification_status"] == "live_verified"
    assert ocr_diag["verification_source"] == "test_double"
    assert dummy_key not in resp.text


# ---------------------------------------------------------------------------
# Test 4: Google Vision authorization/billing failure followed by successful fallback
# ---------------------------------------------------------------------------
def test_google_vision_billing_failure_followed_by_successful_fallback():
    """Google Vision fails with billing error; OCR.space fallback succeeds; telemetry records degraded state."""
    p_google = MockTestProvider(
        name="google_vision",
        configured=True,
        result=OcrResult(
            engine="google_vision",
            provider="google_vision",
            status=STATUS_BILLING_ERROR,
            failure_reason="Google Cloud Vision HTTP 403: Billing account not configured.",
            duration_ms=90.0,
        ),
    )
    p_ocrspace = MockTestProvider(
        name="ocr_space",
        configured=True,
        result=OcrResult(
            lines=[OcrLine(text="NET QUANTITY 500g", confidence=None, box=[[0.0, 0.0]])],
            engine="ocr_space",
            provider="ocr_space",
            status=STATUS_SUCCESS,
            duration_ms=210.0,
        ),
    )

    fallback = FallbackOCRProvider(candidates=[p_google, p_ocrspace])
    result = fallback.recognize(b"synthetic_packaging_image")

    assert result.status == STATUS_SUCCESS
    assert result.provider == "ocr_space"
    assert len(result.attempts) == 2
    assert result.attempts[0]["provider"] == "google_vision"
    assert result.attempts[0]["status"] == STATUS_BILLING_ERROR
    assert result.attempts[0]["category"] == "billing_error"
    assert result.attempts[1]["provider"] == "ocr_space"
    assert result.attempts[1]["status"] == STATUS_SUCCESS

    # Diagnostics show fallback occurred and state is degraded
    diag = get_ocr_health_diagnostics()
    assert diag["fallback_occurred"] is True
    assert diag["readiness"] == "degraded"
    assert diag["verification_status"] == "fallback_succeeded"


# ---------------------------------------------------------------------------
# Test 5: Missing fallback credentials and correct safe-skip behavior
# ---------------------------------------------------------------------------
def test_missing_fallback_credentials_safe_skip():
    """Unconfigured fallback providers are skipped safely without network attempts or exceptions."""
    p_google = MockTestProvider(
        name="google_vision",
        configured=True,
        result=OcrResult(
            engine="google_vision",
            provider="google_vision",
            status=STATUS_AUTH_ERROR,
            failure_reason="Google Cloud Vision HTTP 401: Invalid API Key",
            duration_ms=50.0,
        ),
    )
    p_unconfigured_ocrspace = MockTestProvider(name="ocr_space", configured=False)
    p_unconfigured_azure = MockTestProvider(name="azure", configured=False)

    fallback = FallbackOCRProvider(candidates=[p_google, p_unconfigured_ocrspace, p_unconfigured_azure])
    result = fallback.recognize(b"synthetic_packaging_image")

    assert p_google.call_count == 1
    assert p_unconfigured_ocrspace.call_count == 0
    assert p_unconfigured_azure.call_count == 0

    assert len(result.attempts) == 3
    assert result.attempts[1]["status"] == STATUS_SKIPPED
    assert result.attempts[1]["category"] == "skipped_unconfigured"
    assert result.attempts[2]["status"] == STATUS_SKIPPED
    assert result.attempts[2]["category"] == "skipped_unconfigured"


# ---------------------------------------------------------------------------
# Test 6: Redaction of keys embedded in provider exception strings or URLs
# ---------------------------------------------------------------------------
def test_redaction_of_keys_in_provider_exception_strings_or_urls():
    """Provider API keys and tokens embedded in URLs and exception strings are sanitized."""
    raw_ocrspace_url = "https://api.ocr.space/parse/image?apikey=K89999999999999&language=eng"
    raw_google_url = "https://vision.googleapis.com/v1/images:annotate?key=AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6"
    raw_azure_url = "https://test.cognitiveservices.azure.com/computervision/imageanalysis:analyze?subscription-key=abcdef0123456789abcdef0123456789"
    raw_exc = "HTTP 401: Unauthorized access with key=K89999999999999 and token Bearer my_secret_token_12345"

    clean_ocrspace = sanitize_sensitive_text(raw_ocrspace_url)
    clean_google = sanitize_sensitive_text(raw_google_url)
    clean_azure = sanitize_sensitive_text(raw_azure_url)
    clean_exc = sanitize_sensitive_text(raw_exc)

    assert "K89999999999999" not in clean_ocrspace
    assert "[REDACTED]" in clean_ocrspace

    assert "AIzaSyA1B2C3D4E5F6G7H8I9J0K1L2M3N4O5P6" not in clean_google
    assert "[REDACTED]" in clean_google

    assert "abcdef0123456789abcdef0123456789" not in clean_azure
    assert "[REDACTED]" in clean_azure

    assert "my_secret_token_12345" not in clean_exc
    assert "K89999999999999" not in clean_exc
    assert "[REDACTED]" in clean_exc


# ---------------------------------------------------------------------------
# Test 7: Redaction of authorization headers and tokens from logs and serialized errors
# ---------------------------------------------------------------------------
def test_redaction_of_authorization_headers_and_tokens():
    """Headers with Bearer tokens or provider keys are sanitized before serialization."""
    headers_dict = {
        "Authorization": "Bearer secret_jwt_token_9999999",
        "X-Goog-Api-Key": "AIzaSyTestGoogleVisionKey123456",
        "Ocp-Apim-Subscription-Key": "0123456789abcdef0123456789abcdef",
        "Content-Type": "application/json",
    }

    sanitized = sanitize_diagnostic_data(headers_dict)
    assert sanitized["Authorization"] == "Bearer [REDACTED]"
    assert sanitized["X-Goog-Api-Key"] == "[REDACTED]"
    assert sanitized["Ocp-Apim-Subscription-Key"] == "[REDACTED]"
    assert sanitized["Content-Type"] == "application/json"


# ---------------------------------------------------------------------------
# Test 8: Absence of secret values from health responses, diagnostic responses, and OCR errors
# ---------------------------------------------------------------------------
def test_absence_of_secret_values_from_health_and_diagnostics(client, monkeypatch):
    """Neither /health nor OCR diagnostics ever leak configured secrets or keys."""
    test_key_ocr = "K84444444444444"
    test_key_google = "AIzaSySecretGoogleKey9876543210"
    test_key_azure = "deadbeef01234567deadbeef01234567"

    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", test_key_ocr)
    monkeypatch.setattr(settings, "GOOGLE_VISION_API_KEY", test_key_google)
    monkeypatch.setattr(settings, "AZURE_VISION_KEY", test_key_azure)

    # 1. Health endpoint
    h_resp = client.get("/health")
    assert h_resp.status_code == 200
    h_text = h_resp.text
    assert test_key_ocr not in h_text
    assert test_key_google not in h_text
    assert test_key_azure not in h_text

    # 2. Diagnostics payload
    diag = get_ocr_health_diagnostics()
    diag_str = json.dumps(diag)
    assert test_key_ocr not in diag_str
    assert test_key_google not in diag_str
    assert test_key_azure not in diag_str

    # 3. Provider error reporting
    p = OCRSpaceProvider(api_key=test_key_ocr)
    with patch("httpx.post", side_effect=Exception(f"Connection to api.ocr.space?apikey={test_key_ocr} dropped")):
        err_res = p.recognize(b"fake_bytes")
        assert test_key_ocr not in err_res.failure_reason
        assert "[REDACTED]" in err_res.failure_reason


# ---------------------------------------------------------------------------
# Test 9: Distinction between missing configuration and actual provider failure
# ---------------------------------------------------------------------------
def test_distinction_between_missing_config_and_actual_provider_failure(monkeypatch):
    """System differentiates unconfigured from auth, rate-limited, timeout, network, and total failures."""
    # Case A: Missing configuration
    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", None)
    monkeypatch.setattr(settings, "GOOGLE_VISION_API_KEY", None)
    monkeypatch.setattr(settings, "AZURE_VISION_KEY", None)
    reset_diagnostic_records()
    diag_unconfigured = get_ocr_health_diagnostics()
    assert diag_unconfigured["readiness"] == "unconfigured"
    assert diag_unconfigured["verification_status"] == "missing_configuration"

    # Case B: Rate limiting failure
    monkeypatch.setattr(settings, "OCR_SPACE_API_KEY", "K85555555555555")
    record_provider_failure("ocr_space", STATUS_RATE_LIMITED, "rate_limited", "HTTP 429: Too Many Requests", 45.0)
    diag_rate_limited = get_ocr_health_diagnostics()
    assert diag_rate_limited["readiness"] == "rate_limited"
    assert diag_rate_limited["verification_status"] == "rate_limited"

    # Case C: Timeout failure
    reset_diagnostic_records()
    record_provider_failure("google_vision", STATUS_TIMEOUT, "timeout", "Read timeout after 12s", 12000.0)
    diag_timeout = get_ocr_health_diagnostics()
    assert diag_timeout["readiness"] == "timeout"
    assert diag_timeout["verification_status"] == "timeout"

    # Case D: Network failure
    reset_diagnostic_records()
    record_provider_failure("azure", STATUS_PROVIDER_ERROR, "network_error", "DNS resolution failed", 15.0)
    diag_network = get_ocr_health_diagnostics()
    assert diag_network["readiness"] == "network_error"
    assert diag_network["verification_status"] == "network_error"

    # Case E: All providers failed
    reset_diagnostic_records()
    record_provider_failure("chain", STATUS_PROVIDER_ERROR, "all_failed", "All OCR providers failed", 500.0)
    diag_all_failed = get_ocr_health_diagnostics()
    assert diag_all_failed["readiness"] == "all_failed"
    assert diag_all_failed["verification_status"] == "all_providers_failed"


# ---------------------------------------------------------------------------
# Test 10: Unsupported or invalid provider configuration
# ---------------------------------------------------------------------------
def test_unsupported_or_invalid_provider_configuration(monkeypatch):
    """Unsupported provider names fail safely by raising ValueError and reporting invalid configuration."""
    with pytest.raises(ValueError) as excinfo:
        get_ocr_provider("unsupported_provider_xyz")
    assert "Unsupported OCR provider 'unsupported_provider_xyz'" in str(excinfo.value)

    monkeypatch.setattr(settings, "OCR_PROVIDER", "bogus_provider")
    diag = get_ocr_health_diagnostics()
    assert diag["readiness"] == "invalid_configuration"
    assert diag["verification_status"] == "unsupported_provider"
    assert diag["configured"] is False


# ---------------------------------------------------------------------------
# Test 11: Correct OCR_PROVIDER=auto and explicit-provider behavior
# ---------------------------------------------------------------------------
def test_correct_ocr_provider_auto_and_explicit_behavior(monkeypatch):
    """Factory correctly instantiates FallbackOCRProvider for 'auto' and specific providers explicitly."""
    # 1. auto
    monkeypatch.setattr(settings, "OCR_PROVIDER", "auto")
    prov_auto = get_ocr_provider()
    assert isinstance(prov_auto, FallbackOCRProvider)

    # 2. google_vision explicit
    prov_google = get_ocr_provider("google_vision")
    assert isinstance(prov_google, GoogleVisionProvider)

    # 3. ocr_space explicit
    prov_ocrspace = get_ocr_provider("ocr_space")
    assert isinstance(prov_ocrspace, OCRSpaceProvider)

    # 4. azure explicit
    prov_azure = get_ocr_provider("azure")
    assert isinstance(prov_azure, AzureVisionProvider)


# ---------------------------------------------------------------------------
# Test 12: Continued operation of existing fallback and OCR-failure legal-safety tests
# ---------------------------------------------------------------------------
def test_continued_operation_of_fallback_and_legal_safety():
    """OCR failure must never be recorded as a confirmed legal violation or empty success."""
    p_fail1 = MockTestProvider(
        name="google_vision",
        configured=True,
        result=OcrResult(
            engine="google_vision",
            provider="google_vision",
            status=STATUS_TIMEOUT,
            failure_reason="Connection timed out",
        ),
    )
    p_fail2 = MockTestProvider(
        name="ocr_space",
        configured=True,
        result=OcrResult(
            engine="ocr_space",
            provider="ocr_space",
            status=STATUS_AUTH_ERROR,
            failure_reason="Invalid credentials",
        ),
    )

    fallback = FallbackOCRProvider(candidates=[p_fail1, p_fail2])
    res = fallback.recognize(b"fake_evidence_bytes")

    # Invariant: OCR technical failure MUST NOT be empty success
    assert res.status != STATUS_SUCCESS
    assert res.lines == []
    assert res.engine == "none"
    assert "All OCR providers failed" in res.failure_reason
