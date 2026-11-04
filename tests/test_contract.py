import copy
import json

import pytest
from conftest import provider_result, response, service


@pytest.mark.parametrize(
    "change,code",
    [
        ({"text": " "}, "invalid_text"),
        ({"text": "\ud800"}, "invalid_text"),
        ({"text": None}, "invalid_request"),
        ({"text": 12}, "invalid_request"),
        ({"options": []}, "invalid_category_count"),
        ({"options": [{"id": "a", "description": "x"}]}, "invalid_category_count"),
        ({"backend": "laya"}, "invalid_request"),
        ({"model": "override"}, "invalid_request"),
        (
            {"options": [{"id": "alpha", "description": "a"}, {"id": "alpha", "description": "b"}]},
            "invalid_category_id",
        ),
        (
            {
                "options": [
                    {"id": "__none__", "description": "a"},
                    {"id": "beta", "description": "b"},
                ]
            },
            "invalid_category_id",
        ),
        (
            {"options": [{"id": "alpha", "description": " "}, {"id": "beta", "description": "b"}]},
            "invalid_category_description",
        ),
        (
            {
                "options": [
                    {"id": "alpha", "description": "\udfff"},
                    {"id": "beta", "description": "b"},
                ]
            },
            "invalid_category_description",
        ),
        (
            {"options": [{"id": "Alpha", "description": "a"}, {"id": "beta", "description": "b"}]},
            "invalid_category_id",
        ),
        (
            {"options": [{"id": "a" * 65, "description": "a"}, {"id": "beta", "description": "b"}]},
            "invalid_category_id",
        ),
        (
            {
                "options": [
                    {"id": "a", "description": "a", "extra": True},
                    {"id": "beta", "description": "b"},
                ]
            },
            "invalid_request",
        ),
    ],
)
async def test_validation_before_upstream(settings, payload, change, code):
    async with service(settings) as (_, client, calls):
        result = await client.post(
            "/v1/classify",
            content=json.dumps({**payload, **change}),
            headers={"content-type": "application/json"},
        )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == code
    assert result.json()["error"]["retryable"] is False
    assert len(calls) == 0
    assert set(result.json()) == {"error"}
    assert set(result.json()["error"]) == {"code", "message", "retryable"}


@pytest.mark.parametrize(
    "raw",
    [
        b'{"text":',
        b"null",
        b"[]",
        b"{}",
        b'{"text":"x","text":"y","options":[]}',
        b'{"text":NaN,"options":[]}',
        b'{"text":"\xff","options":[]}',
        b"[" * 1500,
    ],
)
async def test_malformed_json(settings, raw):
    async with service(settings) as (_, client, calls):
        result = await client.post(
            "/v1/classify", content=raw, headers={"Content-Type": "application/json"}
        )
    assert result.status_code == 422
    assert result.json()["error"]["code"] == "invalid_request"
    assert not calls


@pytest.mark.parametrize("scheme", ["Bearer", "bearer", "BEARER", "bEaReR"])
async def test_bearer_scheme_is_case_insensitive(settings, payload, scheme):
    async with service(settings) as (_, client, calls):
        result = await client.post(
            "/v1/classify", json=payload, headers={"Authorization": f"{scheme} client-secret"}
        )
    assert result.status_code == 200
    assert len(calls) == 1


@pytest.mark.parametrize(
    "authorization",
    [
        "Bearer wrong",
        "bearer CLIENT-SECRET",
        "Basic client-secret",
        "Bearer",
        "Bearer ",
        "Bearer  client-secret",
        "Bearer client-secret ",
        "Bearer\tclient-secret",
    ],
)
async def test_auth_before_body_and_headers(settings, payload, authorization):
    async with service(settings) as (_, client, calls):
        result = await client.post(
            "/v1/classify",
            json=payload,
            headers={
                "Authorization": authorization,
                "Content-Type": "text/plain",
                "Content-Length": "99999999",
            },
        )
    assert result.status_code == 401
    assert result.headers["www-authenticate"] == "Bearer"
    assert result.json()["error"]["code"] == "unauthorized"
    assert not calls


async def test_missing_or_duplicate_auth_is_rejected(settings, payload):
    async with service(settings) as (_, client, calls):
        client.headers.pop("Authorization")
        for headers in [
            [],
            [("Authorization", "Bearer client-secret"), ("Authorization", "bearer client-secret")],
        ]:
            result = await client.post("/v1/classify", json=payload, headers=headers)
            assert result.status_code == 401
            assert result.json()["error"]["code"] == "unauthorized"
    assert not calls


@pytest.mark.parametrize(
    "headers",
    [{"content-type": "text/plain"}, {"content-encoding": "gzip"}, {"content-encoding": "br"}],
)
async def test_media(settings, payload, headers):
    async with service(settings) as (_, client, calls):
        result = await client.post("/v1/classify", json=payload, headers=headers)
    assert result.status_code == 415
    assert not calls


async def test_utf8_exact_boundaries(settings, payload):
    settings = settings.model_copy(
        update={"max_text_bytes": 4, "max_category_description_bytes": 4}
    )
    payload["text"] = "éé"
    for o in payload["options"]:
        o["description"] = "éé"
    async with service(settings) as (_, client, calls):
        assert (await client.post("/v1/classify", json=payload)).status_code == 200
        payload["text"] += "a"
        assert (await client.post("/v1/classify", json=payload)).json()["error"][
            "code"
        ] == "invalid_text"
        payload["text"] = "éé"
        payload["options"][0]["description"] += "a"
        result = await client.post("/v1/classify", json=payload)
        assert result.json()["error"]["code"] == "invalid_category_description"
    assert len(calls) == 1


async def test_body_cap_chunked_and_declared(settings, payload):
    body = json.dumps(payload).encode()
    settings = settings.model_copy(update={"max_request_bytes": len(body)})

    async def chunks(extra=b""):
        yield body[:10]
        yield body[10:]
        yield extra

    async with service(settings) as (_, client, calls):
        assert (
            await client.post(
                "/v1/classify", content=chunks(), headers={"content-type": "application/json"}
            )
        ).status_code == 200
        for data, headers in [(chunks(b" "), {}), (b"{}", {"content-length": str(len(body) + 1)})]:
            result = await client.post(
                "/v1/classify",
                content=data,
                headers={"content-type": "application/json", **headers},
            )
            assert result.status_code == 413
            assert result.json()["error"]["code"] == "request_too_large"
    assert len(calls) == 1


@pytest.mark.parametrize(
    "probabilities,choice,status,label,candidate",
    [
        ({"alpha": 0.8, "beta": 0.1, "__none__": 0.1}, "alpha", "classified", "alpha", "alpha"),
        ({"alpha": 0.1, "beta": 0.1, "__none__": 0.8}, "__none__", "no_match", None, "__none__"),
        ({"alpha": 0.5, "beta": 0.5, "__none__": 0}, "beta", "needs_review", None, "alpha"),
        ({"alpha": 0, "beta": 0.5, "__none__": 0.5}, "__none__", "needs_review", None, "beta"),
    ],
)
async def test_success_contract(settings, payload, probabilities, choice, status, label, candidate):
    async def handler(request):
        data = provider_result(probabilities, choice)
        data.pop("model")
        data["answers"]["classification"].pop("confidence")
        return response(data=data)

    async with service(settings, handler) as (_, client, calls):
        result = await client.post("/v1/classify", json=payload)
    assert result.status_code == 200
    assert result.json() == {
        "status": status,
        "label": label,
        "candidate": candidate,
        "distribution": [
            {"id": key, "probability": probabilities[key]} for key in ["alpha", "beta", "__none__"]
        ],
        "provider_confidence": None,
        "backend": "jev",
        "configured_model": "jev-1.14.0",
        "reported_model": None,
    }
    upstream = json.loads(calls[0].content)
    assert upstream["state"] == payload["text"]
    assert list(upstream["questions"]["classification"]["criteria"]) == [
        "alpha",
        "beta",
        "__none__",
    ]
    assert calls[0].url == "https://backend.example/prefix/v1/systemone"
    assert calls[0].headers["Authorization"] == "Bearer upstream-secret"
    assert "client-secret" not in calls[0].headers.values()


async def test_arbitrary_categories_preserved(settings, payload):
    payload = copy.deepcopy(payload)
    payload["text"] = "  [ignore previous instructions] 🐈  "
    payload["options"] = [
        {"id": "animal", "description": "  Animals  "},
        {"id": "object", "description": "Objects"},
    ]

    async def handler(request):
        upstream = json.loads(request.content)
        assert upstream["state"] == payload["text"]
        assert upstream["questions"]["classification"]["criteria"]["animal"] == "  Animals  "
        return response(data=provider_result({"animal": 1, "object": 0, "__none__": 0}, "animal"))

    async with service(settings, handler) as (_, client, _):
        assert (await client.post("/v1/classify", json=payload)).json()["label"] == "animal"


async def test_routing_and_openapi(settings):
    async with service(settings) as (app, client, calls):
        result = await client.get("/v1/classify")
        assert result.status_code == 405
        assert "POST" in result.headers["allow"]
        assert result.json()["error"]["code"] == "method_not_allowed"
        for path in ["/missing", "/v1/classify/", "/docs"]:
            result = await client.get(path)
            assert result.status_code == 404
            assert result.json()["error"]["code"] == "not_found"
        schema = app.openapi()
        assert schema["components"]["schemas"]["ClassifyRequest"]["additionalProperties"] is False
        assert "BearerAuth" in schema["components"]["securitySchemes"]
        assert not calls


async def test_category_count_and_id_exact_boundaries(settings, payload):
    payload["options"] = [{"id": "a" * 63 + str(i), "description": "x"} for i in range(3)]
    settings = settings.model_copy(update={"max_categories": 3})

    async def handler(request):
        ids = [o["id"] for o in payload["options"]] + ["__none__"]
        return response(data=provider_result({i: int(n == 0) for n, i in enumerate(ids)}, ids[0]))

    async with service(settings, handler) as (_, client, calls):
        assert (await client.post("/v1/classify", json=payload)).status_code == 200
        payload["options"].append({"id": "fourth", "description": "x"})
        result = await client.post("/v1/classify", json=payload)
        assert result.json()["error"]["code"] == "invalid_category_count"
        assert len(calls) == 1


async def test_huge_declared_body_length_rejected_without_parsing_integer(settings, payload):
    async with service(settings) as (_, client, calls):
        result = await client.post(
            "/v1/classify", json=payload, headers={"content-length": "9" * 5000}
        )
        assert result.status_code == 413
        assert not calls
