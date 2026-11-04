"""FastAPI service with bounded admission, cancellation and sanitized errors."""

import asyncio
import hmac
import logging
import time
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException
from starlette.requests import ClientDisconnect

from .backends import JevAdapter
from .config import Settings
from .errors import ERRORS, ErrorResponse, ServiceError
from .schemas import ClassifyRequest, ClassifyResponse, parse_request

log = logging.getLogger("tagger")


class BoundaryMiddleware:
    """One wall-clock deadline, including uploads; never log raw exceptions."""

    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        scope.setdefault("state", {})["backend_started"] = False
        response_started = False
        outcome = "internal_error"
        started_at = time.monotonic()

        async def track_send(message):
            nonlocal response_started, outcome
            if message["type"] == "http.response.start":
                if time.monotonic() - started_at >= self.settings.request_timeout_seconds:
                    raise TimeoutError
                response_started = True
                outcome = str(message["status"])
            await send(message)

        error = None
        try:
            async with asyncio.timeout(self.settings.request_timeout_seconds):
                await self.app(scope, receive, track_send)
        except TimeoutError:
            error = ServiceError(
                "backend_timeout" if scope["state"]["backend_started"] else "request_timeout"
            )
        except ClientDisconnect:
            outcome = "client_disconnected"
        except ServiceError as exc:
            error = exc
        except Exception:
            error = ServiceError("internal_error")
        finally:
            if error is not None:
                outcome = error.code
                if not response_started:
                    await error.response()(scope, receive, send)
            log.info(
                "outcome=%s backend=%s duration_ms=%.1f",
                outcome,
                self.settings.backend,
                (time.monotonic() - started_at) * 1000,
            )


async def read_body(request: Request, settings: Settings) -> bytes:
    headers = request.headers
    auth = headers.getlist("authorization")
    if len(auth) != 1:
        raise ServiceError("unauthorized")
    scheme, _, token = auth[0].partition(" ")
    expected = settings.api_key.get_secret_value().encode("ascii")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token.encode("latin-1"), expected):
        raise ServiceError("unauthorized")
    media_types = headers.getlist("content-type")
    encodings = headers.getlist("content-encoding")
    if (
        len(media_types) != 1
        or media_types[0].split(";", 1)[0].strip().lower() != "application/json"
        or len(encodings) > 1
        or (encodings and encodings[0].strip().lower() != "identity")
    ):
        raise ServiceError("unsupported_media_type")
    lengths = headers.getlist("content-length")
    if lengths:
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ServiceError("invalid_request")
        declared = lengths[0].lstrip("0") or "0"
        if (
            len(declared) > len(str(settings.max_request_bytes))
            or int(declared) > settings.max_request_bytes
        ):
            raise ServiceError("request_too_large")
    body = bytearray()
    async for chunk in request.stream():
        if len(body) + len(chunk) > settings.max_request_bytes:
            raise ServiceError("request_too_large")
        body.extend(chunk)
    return bytes(body)


async def until_disconnect(request: Request) -> None:
    # Only called after the entire body is consumed, so it cannot steal upload chunks.
    while True:
        message = await request.receive()
        if message["type"] == "http.disconnect":
            return


def create_app(
    settings: Settings | None = None, transport: httpx.AsyncBaseTransport | None = None
) -> FastAPI:
    settings = settings or Settings.from_env()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # Explicitly ignore proxy/.netrc environment settings and refuse redirects.
        async with httpx.AsyncClient(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=None,
            limits=httpx.Limits(
                max_connections=settings.max_concurrent_requests,
                max_keepalive_connections=settings.max_concurrent_requests,
            ),
        ) as client:
            app.state.adapter = JevAdapter(settings, client)
            app.state.in_flight = 0
            yield

    app = FastAPI(
        title="Tagger",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        redirect_slashes=False,
    )
    app.add_middleware(BoundaryMiddleware, settings=settings)

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException):
        code = {404: "not_found", 405: "method_not_allowed"}.get(exc.status_code, "internal_error")
        return ServiceError(code).response(headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError):
        return ServiceError("invalid_request").response()

    @app.post(
        "/v1/classify",
        response_model=ClassifyResponse,
        responses={status: {"model": ErrorResponse} for status, _, _ in ERRORS.values()},
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {"$ref": "#/components/schemas/ClassifyRequest"}
                    },
                },
            },
            "security": [{"BearerAuth": []}],
        },
    )
    async def classify(request: Request) -> ClassifyResponse:
        parsed = parse_request(await read_body(request, settings), settings)
        # Atomic on the process event loop: no await between check and increment.
        if app.state.in_flight >= settings.max_concurrent_requests:
            raise ServiceError("overloaded")
        app.state.in_flight += 1
        tasks = []
        try:

            def mark_started():
                request.state.backend_started = True

            upstream = asyncio.create_task(app.state.adapter.classify(parsed, mark_started))
            disconnected = asyncio.create_task(until_disconnect(request))
            tasks = [upstream, disconnected]
            done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
            if disconnected in done:
                raise ClientDisconnect
            return upstream.result()
        finally:
            for task in tasks:
                if not task.done():
                    task.cancel()
            try:
                # Keep capacity until local cleanup has actually finished.
                await asyncio.gather(*tasks, return_exceptions=True)
            finally:
                app.state.in_flight -= 1

    default_openapi = app.openapi

    def openapi():
        if app.openapi_schema is None:
            schema = default_openapi()
            request_schema = ClassifyRequest.model_json_schema(
                ref_template="#/components/schemas/{model}"
            )
            definitions = request_schema.pop("$defs", {})
            schema["components"]["schemas"].update(definitions)
            schema["components"]["schemas"]["ClassifyRequest"] = request_schema
            schema["components"]["securitySchemes"] = {
                "BearerAuth": {"type": "http", "scheme": "bearer"},
            }
            app.openapi_schema = schema
        return app.openapi_schema

    app.openapi = openapi
    return app
