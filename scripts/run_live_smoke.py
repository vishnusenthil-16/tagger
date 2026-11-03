"""Start a temporary local Tagger process and smoke-test the configured real backend.

Supply TAGGER_BACKEND*, preferably via a secret manager. The service-client key is
random, exists only in child environments, and is never printed or persisted.
"""

import json
import os
import secrets
import socket
import subprocess
import sys
import time


def main() -> int:
    env = dict(os.environ)
    env.pop("TYPESAFE_API_KEY", None)  # Never inherit an unrelated project's convenience key.
    backend = env.get("TAGGER_BACKEND")
    if backend != "jev":
        print("Set TAGGER_BACKEND and its endpoint configuration.", file=sys.stderr)
        return 2
    env["TAGGER_API_KEY"] = secrets.token_urlsafe(32)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [sys.executable, "-m", "tagger.cli", "--host", "127.0.0.1", "--port", str(port)],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    try:
        deadline = time.monotonic() + 10
        while True:
            if process.poll() is not None or time.monotonic() >= deadline:
                print(json.dumps({"result": "failed", "stage": "service_startup"}))
                return 1
            try:
                with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                    break
            except OSError:
                time.sleep(0.025)
        smoke = subprocess.run(
            [
                sys.executable,
                "-m",
                "tagger.smoke",
                "--url",
                f"http://127.0.0.1:{port}",
                "--expect-backend",
                backend,
            ],
            env=env,
            capture_output=True,
            text=True,
            timeout=110,
        )
        # The smoke CLI only outputs a sanitized report, never raw HTTP failures.
        print(smoke.stdout or smoke.stderr, end="")
        return smoke.returncode
    except subprocess.TimeoutExpired:
        print(json.dumps({"result": "failed", "stage": "smoke_deadline"}))
        return 1
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()


if __name__ == "__main__":
    raise SystemExit(main())
