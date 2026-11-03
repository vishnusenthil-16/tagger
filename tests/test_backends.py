import json

import httpx
import pytest
from conftest import Stream, provider_result, response, service

from tagger.backends import MAX_RESPONSE_BYTES, JevAdapter
from tagger.schemas import ClassifyRequest


@pytest.mark.parametrize(
    "status,data,expected,code,retryable",
    [
        (400, {"error": "unknown"}, 502, "backend_error", False),
        (422, {"detail": "invalid fields"}, 502, "backend_error", False),
        (
            400,
            {"error": {"code": "max_tokens_exceeded"}},
            422,
            "backend_input_limit_exceeded",
            False,
        ),
        (422, {"code": "max_tokens_exceeded"}, 422, "backend_input_limit_exceeded", False),
        (400, {"error": "max_tokens_exceeded"}, 422, "backend_input_limit_exceeded", False),
        (400, {"message": "user said max_tokens_exceeded"}, 502, "backend_error", False),
        (401, {"error": "secret"}, 502, "backend_auth_failed", False),
        (403, {"error": "secret"}, 502, "backend_auth_failed", False),
        (413, {}, 422, "backend_input_limit_exceeded", False),
        (429, {}, 503, "backend_unavailable", True),
        (529, {}, 503, "backend_unavailable", True),
        (500, {}, 502, "backend_error", False),
        (503, {}, 502, "backend_error", False),
        (302, {}, 502, "backend_error", False),
    ],
)
async def test_upstream_errors(settings, payload, status, data, expected, code, retryable):
    async def handler(request):
        return response(status, data, headers={"location": "https://do-not-follow.example"})

    async with service(settings, handler) as (_, client, calls):
        result = await client.post("/v1/classify", json=payload)
    assert result.status_code == expected
    assert result.json()["error"]["code"] == code
    assert result.json()["error"]["retryable"] is retryable
    assert len(calls) == 1  # no redirect, retry or fallback


@pytest.mark.parametrize(
    "exception,code,status",
    [
        (httpx.ConnectError, "backend_unavailable", 503),
        (httpx.ReadError, "backend_unavailable", 503),
        (httpx.RemoteProtocolError, "backend_unavailable", 503),
        (httpx.ConnectTimeout, "backend_timeout", 504),
        (httpx.ReadTimeout, "backend_timeout", 504),
        (RuntimeError, "internal_error", 500),
    ],
)
async def test_transport_errors(settings, payload, exception, code, status, caplog):
    async def handler(request):
        raise exception("secret-and-private-text")

    async with service(settings, handler) as (_, client, calls):
        result = await client.post("/v1/classify", json=payload)
    assert result.status_code == status
    assert result.json()["error"]["code"] == code
    assert len(calls) == 1
    assert "secret-and-private-text" not in result.text + caplog.text


@pytest.mark.parametrize(
    "probabilities,choice",
    [
        ({"alpha": 0.8, "beta": 0.2}, "alpha"),
        ({"alpha": 0.8, "beta": 0.1, "__none__": 0.1, "extra": 0}, "alpha"),
        ({"alpha": 0.8, "beta": 0.1, "__none__": 0.1}, "beta"),
        ({"alpha": 0.8, "beta": 0.1, "__none__": 0.1}, "unknown"),
        ({"alpha": float("nan"), "beta": 0.1, "__none__": 0.1}, "alpha"),
        ({"alpha": float("inf"), "beta": 0.1, "__none__": 0.1}, "alpha"),
        ({"alpha": True, "beta": 0, "__none__": 0}, "alpha"),
        ({"alpha": "0.8", "beta": 0.1, "__none__": 0.1}, "alpha"),
        ({"alpha": 1.1, "beta": -0.1, "__none__": 0}, "alpha"),
        ({"alpha": 0.4, "beta": 0.2, "__none__": 0.1}, "alpha"),
        ({"alpha": 0.98, "beta": 0, "__none__": 0}, "alpha"),
        ({"alpha": 0, "beta": 0, "__none__": 0}, "alpha"),
    ],
)
async def test_invalid_probabilities(settings, payload, probabilities, choice):
    async def handler(request):
        return response(data=provider_result(probabilities, choice))

    async with service(settings, handler) as (_, client, _):
        result = await client.post("/v1/classify", json=payload)
    assert result.status_code == 502
    assert result.json()["error"]["code"] == "backend_invalid_output"


@pytest.mark.parametrize(
    "data",
    [
        {},
        [],
        {"answers": []},
        {"answers": {"classification": []}},
        {"answers": {"wrong-question": {}}},
        {"answers": {"classification": {"type": "noul"}}},
        provider_result(model=17),
        provider_result(model=""),
        provider_result(model="\ud800"),
    ],
)
async def test_invalid_shapes(settings, payload, data):
    async def handler(request):
        return response(data=data)

    async with service(settings, handler) as (_, client, _):
        result = await client.post("/v1/classify", json=payload)
    assert result.json()["error"]["code"] == "backend_invalid_output"


@pytest.mark.parametrize(
    "confidence,expected_status,expected_value",
    [
        (None, 200, None),
        ("not numeric", 200, None),
        (True, 200, None),
        (0.37, 200, 0.37),
        (float("inf"), 502, None),
        (-0.1, 502, None),
        (1.1, 502, None),
    ],
)
async def test_confidence(settings, payload, confidence, expected_status, expected_value):
    async def handler(request):
        data = provider_result()
        data["answers"]["classification"]["confidence"] = confidence
        return response(data=data)

    async with service(settings, handler) as (_, client, _):
        result = await client.post("/v1/classify", json=payload)
    assert result.status_code == expected_status
    if expected_status == 200:
        assert result.json()["provider_confidence"] == expected_value


@pytest.mark.parametrize("kind", ["oversized", "invalid_json", "duplicate", "compression", "media"])
async def test_response_envelope_limits(settings, payload, kind):
    stream = Stream([b"x" * (MAX_RESPONSE_BYTES + 1)])

    async def handler(request):
        if kind == "oversized":
            return httpx.Response(200, stream=stream, headers={"content-type": "application/json"})
        if kind == "invalid_json":
            return response(raw=b"not-json-private-provider-body")
        if kind == "duplicate":
            return response(raw=b'{"answers":{},"answers":{}}')
        if kind == "compression":
            return response(headers={"content-encoding": "gzip"})
        return response(headers={"content-type": "text/html"})

    async with service(settings, handler) as (_, client, _):
        result = await client.post("/v1/classify", json=payload)
    assert result.json()["error"]["code"] == "backend_invalid_output"
    assert "private-provider-body" not in result.text
    if kind == "oversized":
        assert stream.closed


async def test_jev_rounding(settings, payload):
    probabilities = {"alpha": 0.33, "beta": 0.33, "__none__": 0.33}

    async def handler(request):
        assert request.headers["authorization"] == "Bearer upstream-secret"
        assert json.loads(request.content)["model"] == "jev-1.14.0"
        return response(data=provider_result(probabilities))

    async with service(settings, handler) as (_, client, _):
        result = await client.post("/v1/classify", json=payload)
    assert result.status_code == 200
    assert result.json()["distribution"][0]["probability"] == 0.33
    assert result.json()["configured_model"] == "jev-1.14.0"


async def test_assembled_request_cap(settings, payload):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: pytest.fail("called"))
    ) as client:
        adapter = JevAdapter(settings, client)
        adapter.body_limit = 1
        from tagger.errors import ServiceError

        with pytest.raises(ServiceError, match="backend_input_limit_exceeded"):
            await adapter.classify(
                ClassifyRequest.model_validate(payload), lambda: pytest.fail("started")
            )


async def test_huge_numeric_probability_is_invalid_not_internal_error(settings, payload):
    async def handler(request):
        return response(data=provider_result({"alpha": 10**400, "beta": 0, "__none__": 0}))

    async with service(settings, handler) as (_, client, _):
        result = await client.post("/v1/classify", json=payload)
    assert result.status_code == 502
    assert result.json()["error"]["code"] == "backend_invalid_output"
