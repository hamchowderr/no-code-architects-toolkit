"""
/v1/media/check: what footage is like before editing, measured by FFmpeg alone. Each test
makes its own small file with FFmpeg so the numbers are known.
"""

import os
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import pytest

from services.v1.media import check as ck

from conftest import API_KEY


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="module")
def media(tmp_path_factory):
    folder = tmp_path_factory.mktemp("check")
    files = {
        "good": folder / "good.mp4",
        "bad": folder / "bad.mp4",
        "clipped": folder / "clipped.mov",
        "mute": folder / "mute.mp4",
        "turned": folder / "turned.mp4",
    }
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=4", "-f", "lavfi", "-i", "sine=f=440:d=4",
           "-af", "volume=-6dB", "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(files["good"]))
    # Landscape, two seconds black, two moving, three of one still picture, very quiet.
    ffmpeg("-f", "lavfi", "-i", "color=black:s=320x180:r=30:d=2",
           "-f", "lavfi", "-i", "testsrc2=s=320x180:r=30:d=2",
           "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=3",
           "-f", "lavfi", "-i", "sine=f=440:d=7",
           "-filter_complex", "[0][1][2]concat=n=3:v=1:a=0[v];[3]volume=-45dB[a]",
           "-map", "[v]", "-map", "[a]", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(files["bad"]))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=3", "-f", "lavfi", "-i", "sine=f=200:d=3",
           "-af", "volume=30dB,alimiter=limit=1:level=false", "-shortest", "-c:v", "libx264",
           "-pix_fmt", "yuv420p", "-c:a", "pcm_s16le", str(files["clipped"]))
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=360x640:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(files["mute"]))
    # Stored sideways with a rotation, the way phones film upright.
    flat = folder / "flat.mp4"
    ffmpeg("-f", "lavfi", "-i", "testsrc2=s=640x360:r=25:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p", str(flat))
    ffmpeg("-display_rotation", "90", "-i", str(flat), "-c", "copy", str(files["turned"]))
    return files


def test_good_footage_measures_clean(media):
    found = ck.check_footage(str(media["good"]))
    assert (found["width"], found["height"]) == (360, 640)
    assert found["fps"] == 30 and 3.9 <= found["duration"] <= 4.1
    assert found["has_audio"] and found["black"] == [] and found["frozen"] == []
    assert -40 < found["loudness_lufs"] < -15
    assert found["peak_db"] < -10


def test_black_frozen_and_quiet(media):
    found = ck.check_footage(str(media["bad"]))
    assert (found["width"], found["height"]) == (320, 180)
    assert len(found["black"]) == 1
    assert found["black"][0]["start"] == 0 and 1.9 <= found["black"][0]["end"] <= 2.1
    # The still picture at the end is frozen.
    assert any(f["start"] >= 3.9 and f["end"] >= 6.9 for f in found["frozen"])
    assert found["loudness_lufs"] is None or found["loudness_lufs"] < -50


def test_clipped_sound_hits_full_scale_often(media):
    found = ck.check_footage(str(media["clipped"]))
    assert found["peak_db"] >= -0.1
    assert found["peak_count"] > 100


def test_no_sound_leaves_the_sound_fields_out(media):
    found = ck.check_footage(str(media["mute"]))
    assert found["has_audio"] is False and "loudness_lufs" not in found


def test_rotation_counts_as_it_plays(media):
    found = ck.check_footage(str(media["turned"]))
    assert abs(found["rotation"]) == 90
    assert (found["width"], found["height"]) == (360, 640)


@pytest.fixture
def file_server(media):
    folder = os.path.dirname(media["good"])
    handler = partial(SimpleHTTPRequestHandler, directory=str(folder))
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


def test_endpoint(client, file_server):
    res = client.post(
        "/v1/media/check",
        json={"media_url": f"{file_server}/bad.mp4"},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    found = res.get_json()["response"]
    assert found["width"] == 320 and len(found["black"]) == 1


def test_endpoint_needs_a_link(client):
    res = client.post("/v1/media/check", json={}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 400
