"""Typed startup configuration. Environment values and validation inputs stay private."""

import math
import os
import re
from typing import Literal, Self
from urllib.parse import urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    ValidationError,
    field_validator,
    model_validator,
)


class ConfigurationError(ValueError):
    pass


class Settings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, hide_input_in_errors=True)

    backend: Literal["jev"]
    backend_base_url: str
    api_key: SecretStr
    backend_api_key: SecretStr
    backend_model: str
    request_timeout_seconds: float = Field(default=30, gt=0, allow_inf_nan=False, strict=True)
    max_request_bytes: int = Field(default=1048576, gt=0, strict=True)
    max_text_bytes: int = Field(default=65536, gt=0, strict=True)
    max_categories: int = Field(default=19, ge=2, strict=True)
    max_category_description_bytes: int = Field(default=2048, gt=0, strict=True)
    max_concurrent_requests: int = Field(default=1, gt=0, strict=True)

    @field_validator("backend_base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        try:
            parsed = urlsplit(value)
            port = parsed.port
            valid = (
                parsed.scheme in {"http", "https"}
                and parsed.hostname
                and parsed.username is None
                and parsed.password is None
                and "?" not in value
                and "#" not in value
                and "\\" not in value
                and not any(c.isspace() or ord(c) < 32 for c in value)
                and (port is None or port > 0)
                and all(part not in {".", ".."} for part in parsed.path.split("/"))
            )
        except ValueError:
            valid = False
        if not valid:
            raise ValueError(
                "Expected an absolute HTTP(S) base URL without credentials, query or fragment"
            )
        return value.rstrip("/")

    @field_validator("api_key", "backend_api_key")
    @classmethod
    def valid_key(cls, value: SecretStr) -> SecretStr:
        raw = value.get_secret_value()
        if not raw or not re.fullmatch(r"[!-~]+", raw):
            raise ValueError("API keys must be nonempty printable ASCII without whitespace")
        return value

    @field_validator("backend_model")
    @classmethod
    def valid_model(cls, value: str) -> str:
        if not value.strip() or any(ord(c) < 32 for c in value):
            raise ValueError("Model identifier must be nonblank and contain no control characters")
        try:
            value.encode("utf-8")
        except UnicodeError:
            raise ValueError("Model identifier must be valid Unicode") from None
        return value

    @model_validator(mode="after")
    def check_limits(self) -> Self:
        if self.api_key.get_secret_value() == self.backend_api_key.get_secret_value():
            raise ValueError("Client and backend API keys must be different")
        if max(self.max_text_bytes, self.max_category_description_bytes) > self.max_request_bytes:
            raise ValueError("Field byte caps cannot exceed the request body cap")
        if self.max_categories > 254 or self.max_request_bytes > 32 * 1024 * 1024:
            raise ValueError("Jev supports at most 254 caller categories and a 32 MiB body")
        return self

    @classmethod
    def from_env(cls) -> Self:
        values = {}
        integer_fields = {
            "max_request_bytes",
            "max_text_bytes",
            "max_categories",
            "max_category_description_bytes",
            "max_concurrent_requests",
        }
        for field in cls.model_fields:
            name = "TAGGER_" + field.upper()
            raw = os.environ.get(name)
            if raw is None:
                continue
            if field in integer_fields:
                if not re.fullmatch(r"[0-9]+", raw):
                    raise ConfigurationError(f"{name} must be a positive integer")
                try:
                    values[field] = int(raw)
                except ValueError:
                    raise ConfigurationError(f"{name} exceeds supported integer size") from None
            elif field == "request_timeout_seconds":
                try:
                    value = float(raw)
                    if not math.isfinite(value) or value <= 0:
                        raise ValueError
                    values[field] = value
                except ValueError:
                    raise ConfigurationError(f"{name} must be positive and finite") from None
            else:
                values[field] = raw
        try:
            return cls.model_validate(values)
        except ValidationError as exc:
            # Never stringify Pydantic's error inputs: URLs and keys can contain secrets.
            fields = sorted(
                {
                    str(e["loc"][0]) if e["loc"] else "backend requirements/limits"
                    for e in exc.errors(include_input=False)
                }
            )
            raise ConfigurationError("Invalid configuration: " + ", ".join(fields)) from None
