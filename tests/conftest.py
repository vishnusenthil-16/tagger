import json
from contextlib import asynccontextmanager

import httpx
import pytest

from tagger.app import create_app
from tagger.config import Settings


@pytest.fixture
def settings():
    return Settings(
        backend="jev",
        backend_base_url="https://backend.example/prefix",
        api_key="client-secret",
        backend_api_key="upstream-secret",
        backend_model="jev-1.14.0",
    )


@pytest.fixture
def payload():
    return {
        "text": "Some content",
        "options": [
            {"id": "alpha", "description": "First category"},
            {"id": "beta", "description": "Second category"},
        ],
    }


def provider_result(probabilities=None, choice="alpha", **kwargs):
    return {
        "model": "reported-version",
        "answers": {
            "classification": {
                "type": "choice",
                "choice": choice,
                "probabilities": probabilities or {"alpha": 0.8, "beta": 0.1, "__none__": 0.1},
                "confidence": 0.7,
            }
        },
        **kwargs,
    }


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk

    async def aclose(self):
        self.closed = True


def response(status=200, data=None, *, raw=None, headers=None):
    body = (
        raw if raw is not None else json.dumps(provider_result() if data is None else data).encode()
    )
    return httpx.Response(
        status,
        stream=Stream([body]),
        headers={"content-type": "application/json", **(headers or {})},
    )


@asynccontextmanager
async def service(settings, handler=None):
    calls = []

    async def handle(request):
        calls.append(request)
        return await handler(request) if handler else response()

    app = create_app(settings, transport=httpx.MockTransport(handle))
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://tagger.test",
            headers={"Authorization": "Bearer client-secret"},
        ) as client:
            yield app, client, calls
