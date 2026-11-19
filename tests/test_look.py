"""
/v1/media/look: CLIP vectors for frames, pictures and words, so footage can be found by
what it shows. The model runs for real (models/look, fetched by models/look/fetch.py) on
the NASA clips in tests/data and on simple drawn pictures.
"""

import os
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from services.v1.media import look as lk

from conftest import API_KEY, ROOT

DATA = os.path.join(ROOT, "tests", "data")
SPEAKER = os.path.join(DATA, "reframe_speaker.mp4")

pytestmark = pytest.mark.skipif(
    not os.path.exists(os.path.join(lk.MODEL_DIR, "vision_model_quantized.onnx")),
    reason="picture model not fetched (python models/look/fetch.py)",
)


def drawing(kind):
    import cv2

    img = np.full((300, 400, 3), 255, dtype=np.uint8)
    if kind == "red circle":
        cv2.circle(img, (200, 150), 110, (220, 20, 20), -1)
    else:
        cv2.rectangle(img, (90, 40), (310, 260), (20, 40, 220), -1)
    return img


def similarity(a, b):
    return float(np.dot(np.asarray(a), np.asarray(b)))


# --- Sampling ----------------------------------------------------------------------------------

def test_frames_are_spread_and_capped():
    assert lk.frame_times(8.0) == [1.0, 3.0, 5.0, 7.0]
    assert lk.frame_times(1.0) == [0.5]
    long = lk.frame_times(3600)
    assert len(long) == lk.MAX_FRAMES
    assert long[0] > 0 and long[-1] < 3600
    assert lk.frame_times(10, every=5) == [2.5, 7.5]


# --- The model ---------------------------------------------------------------------------------

def test_words_find_the_picture_they_describe():
    pictures = lk.picture_vectors([drawing("red circle"), drawing("blue square")])
    words = lk.text_vectors(["a red circle", "a blue square"])
    assert pictures.shape == (2, 512) and words.shape == (2, 512)
    assert np.allclose(np.linalg.norm(pictures, axis=1), 1, atol=1e-4)
    scores = pictures @ words.T
    assert scores[0, 0] > scores[0, 1]
    assert scores[1, 1] > scores[1, 0]


def test_a_batch_gives_the_same_words_as_one_at_a_time():
    texts = ["an astronaut", "people talking inside a space station with lots of cables on the walls"]
    together = lk.text_vectors(texts)
    alone = np.concatenate([lk.text_vectors([t]) for t in texts])
    assert np.allclose(together, alone, atol=1e-4)


def test_very_long_words_are_cut_not_refused():
    vectors = lk.text_vectors(["space " * 200])
    assert vectors.shape == (1, 512)


def test_a_video_is_looked_at_frame_by_frame():
    result = lk.look(SPEAKER, None, ["three astronauts inside a space station", "a dog running on a beach"])
    media = result["media"]
    assert result["model"] == "clip-vit-b-32"
    assert media["duration"] == pytest.approx(8.0, abs=0.1)
    assert [f["time"] for f in media["frames"]] == [1.0, 3.0, 5.0, 7.0]
    assert all(len(f["vector"]) == 512 and 0 <= f["face"] <= 1 for f in media["frames"])
    station, dog = (t["vector"] for t in result["texts"])
    frame = media["frames"][0]["vector"]
    assert similarity(frame, station) > similarity(frame, dog) + 0.05


def test_a_close_face_is_counted():
    import cv2

    rgb = lk.read_frame(SPEAKER, 2.0)
    h, w = rgb.shape[:2]
    # The astronaut on the left, cut out so their face fills much more of the picture.
    close = cv2.resize(rgb[int(h * 0.15) : int(h * 0.75), 0 : int(w * 0.35)], (400, 400))
    assert lk.face_share(close) > lk.face_share(rgb)
    assert lk.face_share(drawing("red circle")) == 0


# --- The request -------------------------------------------------------------------------------

@pytest.fixture
def file_server(tmp_path):
    import cv2

    cv2.imwrite(str(tmp_path / "circle.png"), cv2.cvtColor(drawing("red circle"), cv2.COLOR_RGB2BGR))
    for name in ("reframe_speaker.mp4", "jfk.ogg"):
        os.symlink(os.path.join(DATA, name), tmp_path / name)
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


def test_request_gives_frames_pictures_and_words(client, file_server):
    res = client.post(
        "/v1/media/look",
        json={
            "media_url": f"{file_server}/reframe_speaker.mp4",
            "image_urls": [f"{file_server}/circle.png", f"{file_server}/missing.png"],
            "texts": ["a red circle"],
            "id": "look-1",
        },
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["id"] == "look-1"
    r = body["response"]
    assert len(r["media"]["frames"]) == 4
    circle, missing = r["pictures"]
    assert len(circle["vector"]) == 512 and "error" not in circle
    assert "vector" not in missing and "download" in missing["error"]
    assert similarity(circle["vector"], r["texts"][0]["vector"]) > 0.25


def test_request_with_only_words(client):
    res = client.post("/v1/media/look", json={"texts": ["a garage", "a coffee cup"]}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 200, res.get_json()
    assert [t["text"] for t in res.get_json()["response"]["texts"]] == ["a garage", "a coffee cup"]


def test_request_needs_the_api_key_and_something_to_look_at(client):
    assert client.post("/v1/media/look", json={"texts": ["a"]}).status_code == 401
    assert client.post("/v1/media/look", json={"every": 2}, headers={"X-API-Key": API_KEY}).status_code == 400
    assert client.post("/v1/media/look", json={"texts": ["a"] * 201}, headers={"X-API-Key": API_KEY}).status_code == 400


def test_request_says_when_the_file_has_no_picture(client, file_server):
    res = client.post("/v1/media/look", json={"media_url": f"{file_server}/jfk.ogg"}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 400
    assert "no picture" in res.get_json()["message"]
