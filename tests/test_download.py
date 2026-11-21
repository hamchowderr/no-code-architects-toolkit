"""
/v1/BETA/media/download: a video brought in from a link with yt-dlp. A local server stands in
for the site, so the real download path runs without the internet.
"""

import os
import shutil
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from conftest import API_KEY

DATA = os.path.join(os.path.dirname(__file__), "data")


@pytest.fixture
def site(tmp_path):
    shutil.copy(os.path.join(DATA, "reframe_speaker.mp4"), tmp_path / "talk.mp4")
    (tmp_path / "photos.html").write_text("<html><body><img src='a.jpg'></body></html>")
    handler = partial(SimpleHTTPRequestHandler, directory=str(tmp_path))
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def uploaded(monkeypatch):
    """The files the route would put in the bucket, kept here instead."""
    from routes.v1.media import download as route

    kept = []

    def keep(path):
        kept.append(os.path.getsize(path))
        return f"https://bucket.example/{os.path.basename(path)}"

    monkeypatch.setattr(route, "upload_file", keep)
    return kept


@pytest.fixture
def client(monkeypatch):
    import app as app_module

    monkeypatch.delenv("REDIS_URL", raising=False)
    application = app_module.create_app()
    application.testing = True
    return application.test_client()


def test_a_video_link_is_downloaded_and_put_in_the_bucket(client, site, uploaded):
    res = client.post(
        "/v1/BETA/media/download",
        json={"media_url": f"{site}/talk.mp4"},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    media = res.get_json()["response"]["media"]
    assert media["media_url"] == "https://bucket.example/talk.mp4"
    assert uploaded == [os.path.getsize(os.path.join(DATA, "reframe_speaker.mp4"))]


def test_a_cookie_text_is_handed_to_yt_dlp_as_a_file(client, site, uploaded, monkeypatch):
    from routes.v1.media import download as route

    seen = {}
    real = route.yt_dlp.YoutubeDL

    def watching(opts):
        seen["cookies"] = open(opts["cookiefile"]).read()
        seen["js"] = opts.get("js_runtimes")
        return real(opts)

    monkeypatch.setattr(route.yt_dlp, "YoutubeDL", watching)
    cookies = "# Netscape HTTP Cookie File\n.example.com\tTRUE\t/\tTRUE\t0\tSID\tabc\n"
    res = client.post(
        "/v1/BETA/media/download",
        json={"media_url": f"{site}/talk.mp4", "cookie": cookies},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    assert seen["cookies"] == cookies
    if shutil.which("node"):
        assert list(seen["js"]) == ["node"]


def test_a_link_with_nothing_to_download_says_so(client, site, uploaded):
    res = client.post(
        "/v1/BETA/media/download",
        json={"media_url": f"{site}/photos.html"},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code in (400, 500)
    assert uploaded == []


def test_a_post_of_photos_is_said_plainly_not_a_missing_file(tmp_path):
    from routes.v1.media.download import NothingToDownload, downloaded_file

    # What an Instagram post of photos looks like: yt-dlp plans "Post by x.NA" and writes nothing.
    with pytest.raises(NothingToDownload, match="no video or sound"):
        downloaded_file(str(tmp_path / "Post by instagram.NA"), str(tmp_path))
    # A format change that altered the ending still finds the file that was written.
    (tmp_path / "Clip.mkv").write_bytes(b"x" * 10)
    assert downloaded_file(str(tmp_path / "Clip.webm"), str(tmp_path)) == str(tmp_path / "Clip.mkv")
