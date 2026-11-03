"""Real TCP tests: CLI process -> Tagger HTTP -> local synthetic provider HTTP.

No external models or credentials are used. These are not provider compatibility proof.
"""

import json
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest


def wait_until_listening(process, port):
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            pytest.fail("Tagger exited before accepting connections")
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.1):
                return
        except OSError:
            time.sleep(0.02)
    pytest.fail("Tagger did not accept connections within five seconds")


def test_real_http_roundtrip_and_smoke_cli():
    backend = "jev"
    observed = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            observed.append((self.path, self.headers.get("authorization"), body))
            assert self.path == "/provider/v1/systemone"
            criteria = body["questions"]["classification"]["criteria"]
            choice = next(iter(criteria))
            result = {
                "model": "synthetic-provider",
                "answers": {
                    "classification": {
                        "type": "choice",
                        "choice": choice,
                        "confidence": 1,
                        "probabilities": {key: int(key == choice) for key in criteria},
                    }
                },
            }
            encoded = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)

    provider = ThreadingHTTPServer(("127.0.0.1", 0), Provider)
    thread = threading.Thread(target=provider.serve_forever, daemon=True)
    thread.start()
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    # Deliberately remove inherited backend settings and unrelated credentials.
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("TAGGER_", "TYPESAFE_", "JEV_", "LAYA_"))
    }
    env.update(
        {
            "TAGGER_BACKEND": backend,
            "TAGGER_BACKEND_BASE_URL": f"http://127.0.0.1:{provider.server_port}/provider",
            "TAGGER_API_KEY": "test-service-key",
            "TAGGER_BACKEND_API_KEY": "test-provider-key",
            "TAGGER_BACKEND_MODEL": "test-model",
            "HTTP_PROXY": "http://127.0.0.1:1",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
            "NO_PROXY": "",
        }
    )
    process = subprocess.Popen(
        [sys.executable, "-m", "tagger.cli", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        wait_until_listening(process, port)
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
            timeout=10,
        )
        assert smoke.returncode == 0, smoke.stderr
        report = json.loads(smoke.stdout)
        assert report["passed"] == report["total"] == 3
        assert len(observed) == 3
        assert all(auth == "Bearer test-provider-key" for _, auth, _ in observed)
        with httpx.Client(trust_env=False) as client:
            result = client.post(f"http://127.0.0.1:{port}/v1/classify", json={"text": "private"})
            assert result.status_code == 401
        assert len(observed) == 3
    finally:
        process.terminate()
        stdout, stderr = process.communicate(timeout=5)
        provider.shutdown()
        provider.server_close()
        thread.join(timeout=2)
    logs = (stdout + stderr).decode()
    for private in [
        "test-service-key",
        "test-provider-key",
        "private",
        "ceramic coffee",
        "closes unexpectedly",
        "/provider/v1/systemone",
    ]:
        assert private not in logs
