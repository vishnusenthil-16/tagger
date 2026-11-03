"""Explicit live smoke checks against an already-running Tagger service."""

import argparse
import json
import math
import os
import sys
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from .schemas import ClassifyResponse

CASES = [
    {
        "text": "The application closes unexpectedly when I open a file.",
        "options": [
            {"id": "defect", "description": "Software defects"},
            {"id": "request", "description": "Requests for new functionality"},
        ],
    },
    {
        "text": "A ceramic coffee mug.",
        "options": [
            {"id": "kitchen", "description": "Kitchenware"},
            {"id": "clothing", "description": "Clothing and accessories"},
        ],
    },
    {
        "text": "Rain is expected tomorrow.",
        "options": [
            {"id": "sports", "description": "Sports results"},
            {"id": "music", "description": "Music performances"},
        ],
    },
]


def main() -> None:
    parser = argparse.ArgumentParser(description="Send three synthetic contract checks to Tagger")
    parser.add_argument("--url", default="http://127.0.0.1:8080", help="Tagger base URL")
    parser.add_argument("--expect-backend", choices=["jev"], required=True)
    parser.add_argument("--timeout", type=float, default=35)
    args = parser.parse_args()
    url = urlsplit(args.url)
    if (
        url.scheme not in {"http", "https"}
        or not url.hostname
        or url.username is not None
        or url.password is not None
        or url.query
        or url.fragment
    ):
        parser.error("Use an HTTP(S) URL without credentials, query or fragment")
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    key = os.environ.get("TAGGER_API_KEY")
    if not key or not key.isascii() or any(c.isspace() or ord(c) < 33 for c in key):
        parser.error("Set TAGGER_API_KEY to the service-client key")
    results = []
    try:
        with httpx.Client(timeout=args.timeout, trust_env=False, follow_redirects=False) as client:
            for case in CASES:
                response = client.post(
                    args.url.rstrip("/") + "/v1/classify",
                    json=case,
                    headers={"Authorization": "Bearer " + key},
                )
                response.raise_for_status()
                result = ClassifyResponse.model_validate(response.json())
                ids = [o["id"] for o in case["options"]] + ["__none__"]
                if (
                    result.backend != args.expect_backend
                    or [p.id for p in result.distribution] != ids
                ):
                    raise ValueError("Unexpected backend or labels")
                maximum = max(p.probability for p in result.distribution)
                winners = [p.id for p in result.distribution if p.probability == maximum]
                expected_status = (
                    "needs_review"
                    if len(winners) > 1
                    else "no_match"
                    if winners[0] == "__none__"
                    else "classified"
                )
                if (
                    result.candidate != winners[0]
                    or result.status != expected_status
                    or result.label != (winners[0] if expected_status == "classified" else None)
                ):
                    raise ValueError("Inconsistent result")
                results.append(
                    {
                        "status": result.status,
                        "backend": result.backend,
                        "configured_model": result.configured_model,
                        "reported_model": result.reported_model,
                    }
                )
    except (httpx.HTTPError, ValueError, ValidationError):
        print(
            json.dumps({"passed": len(results), "total": len(CASES), "result": "failed"}),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
    print(
        json.dumps(
            {"passed": len(results), "total": len(CASES), "result": "passed", "checks": results}
        )
    )


if __name__ == "__main__":
    main()
