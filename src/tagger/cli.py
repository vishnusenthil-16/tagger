"""Local service entry point; never auto-read unrelated project credentials."""

import argparse
import logging
import sys

import uvicorn

from .app import create_app
from .config import ConfigurationError, Settings


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Tagger categorization service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8080)
    args = parser.parse_args()
    try:
        app = create_app(Settings.from_env())
    except ConfigurationError as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(2) from None
    # Avoid request-path/query logging; the application logs sanitized outcomes only.
    logger = logging.getLogger("tagger")
    logger.setLevel(logging.INFO)
    logger.addHandler(logging.StreamHandler())
    logger.propagate = False
    uvicorn.run(app, host=args.host, port=args.port, workers=1, access_log=False)


if __name__ == "__main__":
    main()
