"""
With BLOCK_PRIVATE_URLS on, every link in a request is checked before any work starts
(services/file_management.py payload_url_problem, called from validate_payload). Most
endpoints hand links straight to FFmpeg or ffprobe, which fetch them on their own, so
checking only the toolkit's own downloads is not enough.
"""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import API_KEY
from services import file_management as fm
from services import webhook


@pytest.fixture
def blocked(monkeypatch):
    monkeypatch.setenv("BLOCK_PRIVATE_URLS", "true")


@pytest.mark.parametrize(
    "body",
    [
        {"media_url": "http://127.0.0.1:8080/x.mp4"},
        {"video_urls": [{"video_url": "https://files.example.com/a.mp4"}, {"video_url": "http://10.0.0.5/b.mp4"}]},
        {"inputs": [{"file_url": "http://localhost/a.mp4"}]},
        {"media_url": "https://files.example.com/a.mp4", "webhook_url": "http://169.254.169.254/latest"},
        {"filters": [{"filter": "subtitles='http://192.168.1.4/c.ass'"}]},
        {"outputs": [{"options": [{"option": "-vf", "argument": "subtitles=http://[::1]/c.srt"}]}]},
        {"media_url": "file:///etc/passwd"},
    ],
)
def test_private_links_anywhere_in_a_request_are_refused(blocked, body, monkeypatch):
    monkeypatch.setattr(fm, "redirect_problem", lambda url: None)
    monkeypatch.setattr(fm.socket, "getaddrinfo", fake_dns)
    assert fm.payload_url_problem(body) is not None


def test_public_links_and_plain_text_pass(blocked, monkeypatch):
    monkeypatch.setattr(fm, "redirect_problem", lambda url: None)
    monkeypatch.setattr(fm.socket, "getaddrinfo", fake_dns)
    body = {
        "media_url": "https://files.example.com/a.mp4",
        "text": "see http://localhost for details",  # caption text is never fetched
        "filters": [{"filter": "drawtext=text='hi'"}],
    }
    assert fm.payload_url_problem(body) is None


def test_nothing_is_checked_when_the_setting_is_off(monkeypatch):
    monkeypatch.delenv("BLOCK_PRIVATE_URLS", raising=False)
    assert fm.payload_url_problem({"media_url": "http://127.0.0.1/x"}) is None


def fake_dns(host, port, *a, **k):
    ips = {"files.example.com": "93.184.215.14", "localhost": "127.0.0.1"}
    ip = ips.get(host, host.strip("[]"))
    family = fm.socket.AF_INET6 if ":" in ip else fm.socket.AF_INET
    return [(family, 1, 6, "", (ip, port))]


@pytest.fixture
def redirecting_site():
    class Handler(BaseHTTPRequestHandler):
        def do_HEAD(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/secret")
            self.end_headers()

        def log_message(self, *a):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server.server_address[1]
    server.shutdown()


def test_a_public_link_that_redirects_inside_is_refused(blocked, redirecting_site, monkeypatch):
    # The first hop is treated as public so only the redirect's target decides.
    real = fm.url_problem
    port = redirecting_site
    monkeypatch.setattr(fm, "url_problem", lambda url: None if f":{port}/" in url else real(url))
    assert fm.redirect_problem(f"http://127.0.0.1:{port}/video.mp4") is not None


@pytest.fixture
def client(monkeypatch):
    import app as app_module

    monkeypatch.delenv("REDIS_URL", raising=False)
    application = app_module.create_app()
    application.testing = True
    return application.test_client()


def test_the_route_refuses_before_any_work(client, blocked, monkeypatch):
    from services.v1.media import metadata

    called = []
    monkeypatch.setattr(metadata, "get_media_metadata", lambda *a, **k: called.append(a), raising=False)
    res = client.post(
        "/v1/media/metadata",
        json={"media_url": "http://127.0.0.1:8080/x.mp4"},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 400
    assert "private" in res.get_json()["message"]
    assert called == []


def test_a_webhook_to_a_private_address_is_not_sent(blocked, monkeypatch):
    sent = []
    monkeypatch.setattr(webhook, "_session", lambda: pytest.fail("posted"))
    webhook.send_webhook("http://127.0.0.1:9/hook", {"job_id": "j"})
    assert sent == []
