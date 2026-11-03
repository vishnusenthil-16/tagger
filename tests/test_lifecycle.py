import asyncio
import json

import httpx
from conftest import response, service

from tagger.app import create_app


async def test_overload_immediate_no_queue_and_release(settings, payload):
    entered, release = asyncio.Event(), asyncio.Event()

    async def handler(request):
        entered.set()
        await release.wait()
        return response()

    async with service(settings, handler) as (app, client, calls):
        first = asyncio.create_task(client.post("/v1/classify", json=payload))
        await asyncio.wait_for(entered.wait(), 1)
        second = await asyncio.wait_for(client.post("/v1/classify", json=payload), 0.2)
        assert second.status_code == 503
        assert second.json()["error"] == {
            "code": "overloaded",
            "message": "Service capacity is currently full.",
            "retryable": True,
        }
        assert len(calls) == 1
        release.set()
        assert (await first).status_code == 200
        assert app.state.in_flight == 0
        assert (await client.post("/v1/classify", json=payload)).status_code == 200


async def test_timeout_waits_for_cleanup_then_releases(settings, payload):
    settings = settings.model_copy(update={"request_timeout_seconds": 0.03})
    cleaned = asyncio.Event()

    async def handler(request):
        try:
            await asyncio.Event().wait()
        finally:
            await asyncio.sleep(0.01)
            cleaned.set()

    async with service(settings, handler) as (app, client, calls):
        result = await client.post("/v1/classify", json=payload)
        assert result.status_code == 504
        assert result.json()["error"]["code"] == "backend_timeout"
        assert cleaned.is_set()
        assert app.state.in_flight == 0
        assert len(calls) == 1


async def test_upload_deadline_before_backend(settings):
    settings = settings.model_copy(update={"request_timeout_seconds": 0.02})

    async def slow_body():
        yield b'{"text":'
        await asyncio.Event().wait()

    async with service(settings) as (app, client, calls):
        result = await client.post(
            "/v1/classify", content=slow_body(), headers={"content-type": "application/json"}
        )
        assert result.status_code == 408
        assert result.json()["error"]["code"] == "request_timeout"
        assert app.state.in_flight == 0
        assert not calls


async def test_total_deadline_not_reset_between_chunks(settings, payload):
    settings = settings.model_copy(update={"request_timeout_seconds": 0.04})
    encoded = json.dumps(payload).encode()

    async def slow_body():
        yield encoded[:10]
        await asyncio.sleep(0.025)
        yield encoded[10:]

    async def handler(request):
        await asyncio.sleep(0.025)
        return response()

    async with service(settings, handler) as (app, client, calls):
        result = await client.post(
            "/v1/classify", content=slow_body(), headers={"content-type": "application/json"}
        )
        assert result.status_code == 504
        assert len(calls) == 1
        assert app.state.in_flight == 0


async def test_disconnect_cancels_upstream_and_holds_slot_through_cleanup(settings, payload):
    entered, cleanup_started, cleanup_release = asyncio.Event(), asyncio.Event(), asyncio.Event()
    received = asyncio.Queue()
    sent = []

    async def handler(request):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cleanup_started.set()
            await cleanup_release.wait()

    app = create_app(settings, httpx.MockTransport(handler))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/v1/classify",
        "raw_path": b"/v1/classify",
        "query_string": b"",
        "root_path": "",
        "headers": [
            (b"authorization", b"Bearer client-secret"),
            (b"content-type", b"application/json"),
        ],
        "server": ("localhost", 80),
        "client": ("localhost", 1234),
    }

    async def send(message):
        sent.append(message)

    async with app.router.lifespan_context(app):
        await received.put(
            {"type": "http.request", "body": json.dumps(payload).encode(), "more_body": False}
        )
        task = asyncio.create_task(app(scope, received.get, send))
        await asyncio.wait_for(entered.wait(), 1)
        await received.put({"type": "http.disconnect"})
        await asyncio.wait_for(cleanup_started.wait(), 1)
        assert app.state.in_flight == 1
        cleanup_release.set()
        await asyncio.wait_for(task, 1)
        assert app.state.in_flight == 0
        assert not sent


async def test_two_slots_and_error_recovery(settings, payload):
    settings = settings.model_copy(update={"max_concurrent_requests": 2})
    entered, release = asyncio.Queue(), asyncio.Event()

    async def handler(request):
        await entered.put(True)
        await release.wait()
        return response(500, {"error": "private"})

    async with service(settings, handler) as (app, client, calls):
        tasks = [asyncio.create_task(client.post("/v1/classify", json=payload)) for _ in range(2)]
        for _ in range(2):
            await asyncio.wait_for(entered.get(), 1)
        assert app.state.in_flight == 2
        assert (await client.post("/v1/classify", json=payload)).status_code == 503
        release.set()
        results = await asyncio.gather(*tasks)
        assert all(r.status_code == 502 for r in results)
        assert app.state.in_flight == 0
        assert len(calls) == 2
