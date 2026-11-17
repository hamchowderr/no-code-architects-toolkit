import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# The toolkit reads these at import time.
os.environ.setdefault("API_KEY", "test-key")
os.environ.setdefault("LOCAL_STORAGE_PATH", tempfile.mkdtemp(prefix="ncat-tests-"))
os.environ.pop("REDIS_URL", None)

# Blueprints are discovered relative to the working directory.
os.chdir(ROOT)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

API_KEY = os.environ["API_KEY"]


class WebhookSink:
    """A real HTTP server that records every webhook the toolkit posts."""

    def __init__(self):
        self.received = []
        self._cond = threading.Condition()
        sink = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(length) or b"null")
                with sink._cond:
                    sink.received.append({"path": self.path, "body": body})
                    sink._cond.notify_all()
                self.send_response(204)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def wait_for(self, count, timeout=30):
        deadline = time.time() + timeout
        with self._cond:
            while len(self.received) < count:
                left = deadline - time.time()
                if left <= 0:
                    raise AssertionError(f"Expected {count} webhooks, got {len(self.received)}: {self.received}")
                self._cond.wait(left)
            return list(self.received)

    def close(self):
        self.server.shutdown()


@pytest.fixture
def webhook_sink():
    sink = WebhookSink()
    yield sink
    sink.close()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def redis_server(tmp_path):
    """A real redis-server on a spare port. Skips when redis-server is not installed."""
    binary = shutil.which("redis-server")
    if not binary:
        pytest.skip("redis-server is not installed")
    port = _free_port()
    proc = subprocess.Popen(
        [binary, "--port", str(port), "--save", "", "--appendonly", "no", "--dir", str(tmp_path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    import redis

    url = f"redis://127.0.0.1:{port}/0"
    client = redis.Redis.from_url(url)
    for _ in range(100):
        try:
            client.ping()
            break
        except Exception:
            time.sleep(0.05)
    yield url
    proc.terminate()
    proc.wait(timeout=10)
