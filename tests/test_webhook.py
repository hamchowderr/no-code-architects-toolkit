"""send_webhook: a timeout, retries on brief failures, never twice after a slow answer."""

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from services import webhook


class Receiver:
    def __init__(self, answers, delay=0.0):
        self.answers = list(answers)
        self.delay = delay
        self.bodies = []
        receiver = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):
                length = int(self.headers.get("Content-Length", 0))
                receiver.bodies.append(json.loads(self.rfile.read(length)))
                if receiver.delay:
                    time.sleep(receiver.delay)
                status = receiver.answers.pop(0) if receiver.answers else 204
                self.send_response(status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def quick(monkeypatch):
    monkeypatch.setenv("WEBHOOK_RETRIES", "3")


def test_posts_the_result_once(quick):
    receiver = Receiver([204])
    try:
        webhook.send_webhook(receiver.url, {"job_id": "a", "code": 200})
    finally:
        receiver.close()
    assert receiver.bodies == [{"job_id": "a", "code": 200}]


def test_tries_again_when_the_receiver_is_briefly_unavailable(quick, monkeypatch):
    monkeypatch.setattr(webhook, "_session", _no_backoff(webhook._session))
    receiver = Receiver([503, 502, 204])
    try:
        webhook.send_webhook(receiver.url, {"job_id": "b"})
    finally:
        receiver.close()
    assert len(receiver.bodies) == 3


def test_gives_up_after_the_retries_without_raising(quick, monkeypatch):
    monkeypatch.setenv("WEBHOOK_RETRIES", "1")
    monkeypatch.setattr(webhook, "_session", _no_backoff(webhook._session))
    receiver = Receiver([503, 503, 503])
    try:
        webhook.send_webhook(receiver.url, {"job_id": "c"})
    finally:
        receiver.close()
    assert len(receiver.bodies) == 2


def test_does_not_retry_a_refusal(quick):
    receiver = Receiver([400])
    try:
        webhook.send_webhook(receiver.url, {"job_id": "d"})
    finally:
        receiver.close()
    assert len(receiver.bodies) == 1


def test_a_receiver_that_never_answers_cannot_hold_the_worker(quick, monkeypatch):
    monkeypatch.setenv("WEBHOOK_TIMEOUT", "0.5")
    receiver = Receiver([204], delay=3)
    started = time.monotonic()
    try:
        webhook.send_webhook(receiver.url, {"job_id": "e"})
        took = time.monotonic() - started
    finally:
        receiver.close()
    assert took < 2.5
    # A slow answer is not posted again: the receiver may already have the result.
    assert len(receiver.bodies) == 1


def test_an_unreachable_receiver_is_logged_not_raised(quick, monkeypatch):
    monkeypatch.setenv("WEBHOOK_RETRIES", "0")
    webhook.send_webhook("http://127.0.0.1:9/hook", {"job_id": "f"})


def test_bad_settings_fall_back_to_the_defaults(monkeypatch):
    monkeypatch.setenv("WEBHOOK_TIMEOUT", "soon")
    monkeypatch.setenv("WEBHOOK_RETRIES", "-2")
    assert webhook.webhook_timeout() == 30
    assert webhook.webhook_retries() == 3


def _no_backoff(make):
    def session():
        s = make()
        for adapter in s.adapters.values():
            adapter.max_retries.backoff_factor = 0
            adapter.max_retries.respect_retry_after_header = False
        return s

    return session
