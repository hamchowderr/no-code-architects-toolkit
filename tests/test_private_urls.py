"""
BLOCK_PRIVATE_URLS (services/file_management.py): a shared toolkit won't download from
private or internal addresses, including after a redirect, so a link can't pull another
container's data into the bucket.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services import file_management as fm


@pytest.fixture
def local_site():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/hop":
                self.send_response(302)
                self.send_header("Location", "/file.txt")
                self.end_headers()
                return
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"inside")

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.mark.parametrize(
    "url",
    [
        "http://127.0.0.1/x",
        "http://localhost/x",
        "http://169.254.169.254/latest/meta-data",
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://100.64.0.1/x",
        "http://[::1]/x",
        "http://[::ffff:127.0.0.1]/x",
    ],
)
def test_private_addresses_are_refused_when_blocked(monkeypatch, url):
    monkeypatch.setenv("BLOCK_PRIVATE_URLS", "true")
    assert fm.url_problem(url) is not None


def test_only_web_links_are_downloaded(monkeypatch):
    monkeypatch.delenv("BLOCK_PRIVATE_URLS", raising=False)
    assert fm.url_problem("file:///etc/passwd") is not None
    assert fm.url_problem("ftp://example.com/x") is not None


def test_local_setups_still_download_from_containers(monkeypatch, local_site, tmp_path):
    monkeypatch.delenv("BLOCK_PRIVATE_URLS", raising=False)
    path = fm.download_file(f"{local_site}/hop", str(tmp_path))
    assert open(path, "rb").read() == b"inside"


def test_a_blocked_toolkit_downloads_nothing_private(monkeypatch, local_site, tmp_path):
    monkeypatch.setenv("BLOCK_PRIVATE_URLS", "true")
    with pytest.raises(ValueError, match="private"):
        fm.download_file(f"{local_site}/file.txt", str(tmp_path))
    assert list(tmp_path.iterdir()) == []


def test_every_redirect_is_checked(monkeypatch, local_site, tmp_path):
    seen = []
    monkeypatch.setattr(fm, "url_problem", lambda url: seen.append(url) or (None if url.endswith("/hop") else "private"))
    with pytest.raises(ValueError):
        fm.download_file(f"{local_site}/hop", str(tmp_path))
    assert seen == [f"{local_site}/hop", f"{local_site}/file.txt"]
