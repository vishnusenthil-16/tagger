import pytest
from pydantic import ValidationError

from tagger.config import ConfigurationError, Settings


@pytest.mark.parametrize(
    "updates",
    [
        {"backend": "other"},
        {"backend_base_url": "file:///tmp/x"},
        {"backend_base_url": "https://user:secret@example.com"},
        {"backend_base_url": "https://example.com?secret=x"},
        {"backend_base_url": "https://example.com/#"},
        {"backend_base_url": "http://example.com:99999"},
        {"backend_base_url": "https://example.com/../x"},
        {"backend_base_url": "https://example.com/\n"},
        {"api_key": ""},
        {"api_key": "key\r\nx: y"},
        {"api_key": "é"},
        {"api_key": None},
        {"backend_api_key": None},
        {"backend_model": None},
        {"backend_model": " "},
        {"backend_model": "\ud800"},
        {"max_text_bytes": 0},
        {"max_text_bytes": True},
        {"max_text_bytes": 2.5},
        {"max_request_bytes": 100},
        {"max_categories": 1},
        {"max_categories": 255},
        {"max_concurrent_requests": 0},
        {"request_timeout_seconds": 0},
        {"request_timeout_seconds": float("nan")},
        {"request_timeout_seconds": float("inf")},
        {"backend": "laya"},
        {"max_request_bytes": 33554433},
    ],
)
def test_invalid_settings(settings, updates):
    with pytest.raises(ValidationError):
        Settings.model_validate({**settings.model_dump(), **updates})


def test_environment_is_explicit(monkeypatch):
    for field in Settings.model_fields:
        monkeypatch.delenv("TAGGER_" + field.upper(), raising=False)
    monkeypatch.setenv("TYPESAFE_API_KEY", "unrelated-project-key")
    with pytest.raises(ConfigurationError):
        Settings.from_env()
    for name, value in {
        "BACKEND": "jev",
        "BACKEND_BASE_URL": "https://example.com",
        "API_KEY": "client",
        "BACKEND_API_KEY": "backend",
        "BACKEND_MODEL": "model",
        "MAX_CATEGORIES": "7",
    }.items():
        monkeypatch.setenv("TAGGER_" + name, value)
    assert Settings.from_env().max_categories == 7
    monkeypatch.setenv("TAGGER_MAX_CATEGORIES", "3.5")
    with pytest.raises(ConfigurationError, match="TAGGER_MAX_CATEGORIES"):
        Settings.from_env()


def test_config_errors_redact_inputs(monkeypatch):
    monkeypatch.setenv("TAGGER_BACKEND_BASE_URL", "https://secret:password@example.com")
    with pytest.raises(ConfigurationError) as caught:
        Settings.from_env()
    assert "password" not in str(caught.value)
    assert "secret" not in str(caught.value)


def test_identical_keys_are_rejected_without_exposing_them(settings, monkeypatch):
    shared_key = "shared-credential-sentinel"
    values = {**settings.model_dump(), "api_key": shared_key, "backend_api_key": shared_key}
    with pytest.raises(
        ValidationError, match="Client and backend API keys must be different"
    ) as caught:
        Settings.model_validate(values)
    assert shared_key not in str(caught.value)

    for name in Settings.model_fields:
        monkeypatch.delenv("TAGGER_" + name.upper(), raising=False)
    for name in ("backend", "backend_base_url", "backend_model", "api_key", "backend_api_key"):
        monkeypatch.setenv("TAGGER_" + name.upper(), values[name])
    with pytest.raises(ConfigurationError) as caught:
        Settings.from_env()
    assert shared_key not in str(caught.value)
    assert str(caught.value) == "Invalid configuration: backend requirements/limits"


@pytest.mark.parametrize("value", ["true", "NaN", "inf", "0", "-1", "not-a-number"])
def test_invalid_timeout_environment(monkeypatch, value):
    monkeypatch.setenv("TAGGER_REQUEST_TIMEOUT_SECONDS", value)
    with pytest.raises(ConfigurationError, match="TAGGER_REQUEST_TIMEOUT_SECONDS"):
        Settings.from_env()


def test_huge_integer_configuration_is_sanitized(monkeypatch):
    monkeypatch.setenv("TAGGER_MAX_CATEGORIES", "9" * 5000)
    with pytest.raises(ConfigurationError, match="TAGGER_MAX_CATEGORIES"):
        Settings.from_env()
