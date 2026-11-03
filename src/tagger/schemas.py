"""Strict request/result schemas and deterministic model-output interpretation."""

import json
import math
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .config import Settings
from .errors import ServiceError

NONE_ID = "__none__"
NONE_DESCRIPTION = "None of the supplied categories applies to this content."
ID_PATTERN = re.compile(r"[a-z][a-z0-9_-]{0,63}\Z", re.ASCII)


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class Option(StrictModel):
    id: str
    description: str


class ClassifyRequest(StrictModel):
    text: str
    options: list[Option]


class Probability(StrictModel):
    id: str
    probability: float = Field(ge=0, le=1)


class ClassifyResponse(StrictModel):
    status: Literal["classified", "no_match", "needs_review"]
    label: str | None
    candidate: str
    distribution: list[Probability]
    provider_confidence: float | None
    backend: Literal["jev"]
    configured_model: str | None
    reported_model: str | None


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("nonfinite JSON value")


def decode_json(body: bytes) -> object:
    return json.loads(
        body.decode("utf-8"), object_pairs_hook=_unique_object, parse_constant=_invalid_constant
    )


def _valid_text(value: str, cap: int, code: str) -> None:
    try:
        valid = bool(value.strip()) and len(value.encode("utf-8")) <= cap
    except UnicodeError:
        valid = False
    if not valid:
        raise ServiceError(code)


def parse_request(body: bytes, settings: Settings) -> ClassifyRequest:
    try:
        request = ClassifyRequest.model_validate(decode_json(body))
    except (ValueError, UnicodeError, RecursionError):
        raise ServiceError("invalid_request") from None
    _valid_text(request.text, settings.max_text_bytes, "invalid_text")
    if not 2 <= len(request.options) <= settings.max_categories:
        raise ServiceError("invalid_category_count")
    ids = [option.id for option in request.options]
    if len(set(ids)) != len(ids) or any(not ID_PATTERN.fullmatch(i) for i in ids):
        raise ServiceError("invalid_category_id")
    for option in request.options:
        _valid_text(
            option.description,
            settings.max_category_description_bytes,
            "invalid_category_description",
        )
    return request


def interpret_result(
    payload: object, request: ClassifyRequest, settings: Settings, sum_tolerance: float
) -> ClassifyResponse:
    """Validate provider values without repairing, renormalizing, or synthesizing scores."""
    invalid = ServiceError("backend_invalid_output")
    if not isinstance(payload, dict) or not isinstance(payload.get("answers"), dict):
        raise invalid
    answers = payload["answers"]
    if set(answers) != {"classification"}:
        raise invalid
    answer = answers["classification"]
    if not isinstance(answer, dict) or answer.get("type") != "choice":
        raise invalid
    ids = [option.id for option in request.options] + [NONE_ID]
    probabilities = answer.get("probabilities")
    if not isinstance(probabilities, dict) or set(probabilities) != set(ids):
        raise invalid
    scores = list(probabilities.values())
    if any(type(p) not in (float, int) or not 0 <= p <= 1 or not math.isfinite(p) for p in scores):
        raise invalid
    if abs(math.fsum(scores) - 1) > sum_tolerance:
        raise invalid
    maximum = max(scores)
    winners = [i for i in ids if probabilities[i] == maximum]
    if not isinstance(answer.get("choice"), str) or answer["choice"] not in winners:
        raise invalid
    candidate = winners[0]
    status = (
        "needs_review"
        if len(winners) > 1
        else ("no_match" if candidate == NONE_ID else "classified")
    )
    confidence = answer.get("confidence")
    if type(confidence) not in (float, int):
        confidence = None
    elif not 0 <= confidence <= 1 or not math.isfinite(confidence):
        raise invalid
    model = payload.get("model")
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            raise invalid
        try:
            model.encode("utf-8")
        except UnicodeError:
            raise invalid from None
    try:
        return ClassifyResponse(
            status=status,
            label=candidate if status == "classified" else None,
            candidate=candidate,
            distribution=[Probability(id=i, probability=probabilities[i]) for i in ids],
            provider_confidence=confidence,
            backend=settings.backend,
            configured_model=settings.backend_model,
            reported_model=model,
        )
    except ValidationError:
        raise invalid from None
