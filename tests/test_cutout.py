"""
/v1/video/cutout and /v1/video/background: cut the speaker out of a video with no green
screen, and put a new background behind them.

The mask is made for real (MediaPipe Selfie Segmenter on LiteRT, FFmpeg) on the test clip,
which has two people talking.
"""

import os
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from services.v1.video import cutout

from conftest import API_KEY, ROOT

DATA = os.path.join(ROOT, "tests", "data")
CLIP = os.path.join(DATA, "reframe_cut.mp4")  # 640x360, 8 s, two people, with sound


def grey(path, at, size=(64, 36)):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", path, "-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]},format=gray", "-f", "rawvideo", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(size[1], size[0]).astype(np.int16)


def rgb(path, at, size=(64, 36)):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", path, "-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(size[1], size[0], 3).astype(np.int16)


@pytest.fixture(scope="module")
def mask(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("mask") / "mask.mp4")
    result = cutout.make_mask(CLIP, out)
    return out, result


# --- Small parts -----------------------------------------------------------------------------

def test_the_mask_keeps_the_video_shape_with_even_sides_and_no_larger_than_960():
    assert cutout.mask_size(640, 360) == (640, 360)
    assert cutout.mask_size(1080, 1920) == (540, 960)
    assert cutout.mask_size(3840, 2160) == (960, 540)
    assert cutout.mask_size(901, 507) == (900, 506)


def test_confidence_becomes_a_soft_mask_that_carries_over_between_frames():
    conf = np.array([[0.1, 0.5, 0.9]], dtype=np.float32)
    _, mask = cutout.soften(conf)
    assert mask[0, 0] == 0 and mask[0, 2] == 1 and 0.4 < mask[0, 1] < 0.6
    carried, _ = cutout.soften(np.ones((1, 3), np.float32), previous=np.zeros((1, 3), np.float32))
    assert np.allclose(carried, 1 - cutout.SMOOTH)


def test_the_model_ships_with_its_licence():
    folder = os.path.dirname(cutout.MODEL)
    assert os.path.getsize(cutout.MODEL) > 200_000
    assert "Apache License" in open(os.path.join(folder, "LICENSE-MediaPipe")).read()


def test_background_graph_lays_the_person_over_each_kind_of_background():
    blur = cutout.background_graph(641, 361, "blur", 30)
    assert "[0:v]scale=640:360" in blur and "gblur=sigma=30" in blur and "[fg0][m]alphamerge[person]" in blur
    assert "drawbox=c=0x1b3a5c:t=fill" in cutout.background_graph(640, 360, "color:#1b3a5c")
    picture = cutout.background_graph(640, 360, "image")
    assert "[2:v]scale=640:360:force_original_aspect_ratio=increase,crop=640:360" in picture
    assert picture.endswith("[bg][person]overlay=format=auto:shortest=1,format=yuv420p[vout]")


# --- For real --------------------------------------------------------------------------------

def test_mask_has_one_frame_per_video_frame_and_finds_the_people(mask):
    path, result = mask
    assert result["width"] == 640 and result["height"] == 360
    assert result["fps"] == 25 and abs(result["frames"] - 8.064 * 25) <= 2
    assert 0.1 < result["person_share"] < 0.6
    frame = grey(path, 2.0)
    assert frame.max() > 240 and frame.min() < 15  # someone, and somewhere nobody is
    info = cutout.probe(path)
    assert abs(info["duration"] - 8.064) < 0.1


def test_a_phone_video_on_its_side_is_cut_out_the_right_way_up(tmp_path):
    turned = str(tmp_path / "turned.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-display_rotation", "90", "-i", CLIP, "-t", "1", "-c:v", "libx264", "-an", turned],
        check=True,
    )
    info = cutout.probe(turned)
    assert (info["width"], info["height"]) == (360, 640)
    result = cutout.make_mask(turned, str(tmp_path / "m.mp4"))
    assert (result["width"], result["height"]) == (360, 640)


def test_a_file_with_no_picture_is_refused(tmp_path):
    with pytest.raises(cutout.CutoutError, match="no picture"):
        cutout.make_mask(os.path.join(DATA, "jfk.ogg"), str(tmp_path / "m.mp4"))


def test_head_top_finds_the_highest_point_of_the_person(tmp_path):
    drawn = str(tmp_path / "box.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=200x400:d=1",
         "-vf", "drawbox=x=60:y=120:w=80:h=280:c=white:t=fill", "-pix_fmt", "yuv420p", drawn],
        check=True,
    )
    x, y = cutout.head_top(drawn, 0.5, 200, 400)
    assert abs(y - 30) <= 2 and abs(x - 50) <= 3
    empty = str(tmp_path / "empty.mp4")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=black:s=200x400:d=1", "-pix_fmt", "yuv420p", empty], check=True)
    assert cutout.head_top(empty, 0.5, 200, 400) is None


def test_a_colour_background_replaces_everything_but_the_people(mask, tmp_path):
    path, _ = mask
    out = str(tmp_path / "out.mp4")
    result = cutout.replace_background(CLIP, path, out, kind="color:#1b3a5c")
    assert abs(result["duration"] - 8.064) < 0.1
    m = grey(path, 2.0)
    after, before = rgb(out, 2.0), rgb(CLIP, 2.0)
    nobody = m < 10
    person = m > 245
    assert nobody.sum() > 50 and person.sum() > 50
    assert np.abs(after[nobody] - [0x1B, 0x3A, 0x5C]).mean() < 12
    assert np.abs(after[person] - before[person]).mean() < 10
    assert cutout.probe(out)["has_audio"]


def test_a_blurred_background_keeps_the_people_sharp(mask, tmp_path):
    path, _ = mask
    out = str(tmp_path / "out.mp4")
    cutout.replace_background(CLIP, path, out, kind="blur", blur=20)
    m = grey(path, 2.0, (320, 180))
    after, before = rgb(out, 2.0, (320, 180)), rgb(CLIP, 2.0, (320, 180))
    person, nobody = m > 245, m < 10

    def detail(img, where):
        edges = np.abs(np.diff(img.mean(axis=2), axis=1))[:, :-0 or None]
        return edges[where[:, 1:]].mean()

    assert detail(after, nobody) < detail(before, nobody) * 0.6
    assert np.abs(after[person] - before[person]).mean() < 10


# --- The requests ----------------------------------------------------------------------------

@pytest.fixture
def file_server():
    handler = partial(SimpleHTTPRequestHandler, directory=DATA)
    handler.log_message = lambda *a: None
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()


@pytest.fixture
def client(monkeypatch, tmp_path):
    import app as app_module
    from routes.v1.video import background as bg_route
    from routes.v1.video import cutout as route

    uploads = []

    def fake_upload(path):
        kept = tmp_path / os.path.basename(path)
        shutil.copy(path, kept)
        uploads.append(str(kept))
        return f"https://files.example.com/{os.path.basename(path)}"

    monkeypatch.setattr(route, "upload_file", fake_upload)
    monkeypatch.setattr(bg_route, "upload_file", fake_upload)
    monkeypatch.delenv("REDIS_URL", raising=False)
    application = app_module.create_app()
    application.testing = True
    c = application.test_client()
    c.uploads = uploads
    return c


def test_cutout_request_hands_back_the_mask(client, file_server):
    res = client.post("/v1/video/cutout", json={"video_url": f"{file_server}/reframe_cut.mp4", "id": "c-1"}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()["response"]
    assert body["mask_url"].endswith("_mask.mp4") and body["frames"] > 190
    assert cutout.probe(client.uploads[0])["width"] == 640


def test_background_request_makes_the_mask_when_none_is_given(client, file_server):
    res = client.post(
        "/v1/video/background",
        json={"video_url": f"{file_server}/reframe_cut.mp4", "background": {"type": "color", "color": "#000000"}},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()["response"]
    assert body["file_url"].endswith("_background.mp4") and body["mask_url"].endswith("_mask.mp4")
    assert len(client.uploads) == 2


def test_requests_check_their_fields(client, file_server):
    headers = {"X-API-Key": API_KEY}
    assert client.post("/v1/video/cutout", json={"video_url": "https://x.example/a.mp4"}).status_code == 401
    bad = [
        ("/v1/video/cutout", {}),
        ("/v1/video/cutout", {"video_url": "https://x.example/a.mp4", "extra": 1}),
        ("/v1/video/background", {"video_url": "https://x.example/a.mp4"}),
        ("/v1/video/background", {"video_url": "https://x.example/a.mp4", "background": {"type": "stars"}}),
        ("/v1/video/background", {"video_url": "https://x.example/a.mp4", "background": {"type": "color", "color": "blue"}}),
    ]
    for path, payload in bad:
        assert client.post(path, json=payload, headers=headers).status_code == 400, payload
    res = client.post(
        "/v1/video/background",
        json={"video_url": f"{file_server}/reframe_cut.mp4", "background": {"type": "image"}},
        headers=headers,
    )
    assert res.status_code == 400 and "needs the image's link" in res.get_json()["message"]
