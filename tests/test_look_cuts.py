"""
/v1/media/look with `cuts`: where a video cuts from one shot to the next, so the dashboard
can line a cutaway up with them. No picture model needed. reframe_cut.mp4 is four seconds
of two astronauts talking, then a camera cut to another room; reframe_speaker.mp4 is one
shot.
"""

import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.v1.media import look as lk

from conftest import API_KEY, ROOT

DATA = os.path.join(ROOT, "tests", "data")


def test_finds_the_camera_cut():
    cuts = lk.camera_cuts(os.path.join(DATA, "reframe_cut.mp4"))
    assert len(cuts) == 1
    assert 3.8 <= cuts[0] <= 4.2


def test_one_shot_has_no_cuts():
    assert lk.camera_cuts(os.path.join(DATA, "reframe_speaker.mp4")) == []


@pytest.fixture
def file_server(tmp_path):
    os.symlink(os.path.join(DATA, "reframe_cut.mp4"), tmp_path / "reframe_cut.mp4")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def client(monkeypatch):
    import app as app_module

    monkeypatch.delenv("REDIS_URL", raising=False)
    application = app_module.create_app()
    application.testing = True
    return application.test_client()


def test_request_for_only_the_cuts(client, file_server):
    res = client.post(
        "/v1/media/look",
        json={"media_url": f"{file_server}/reframe_cut.mp4", "cuts": True, "frames": False},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    media = res.get_json()["response"]["media"]
    assert "frames" not in media
    assert media["duration"] > 4 and media["width"] > 0
    assert len(media["cuts"]) == 1 and 3.8 <= media["cuts"][0] <= 4.2


def test_cuts_and_frames_must_be_true_or_false(client):
    res = client.post(
        "/v1/media/look",
        json={"texts": ["a"], "cuts": "yes"},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 400
