"""
/v1/video/reframe: follow the person talking when making a tall video from a wide one.

The camera rules are checked on made-up analysis data. The face finding, the speaker model
and the FFmpeg render run for real on two short clips of NASA footage (public domain):
reframe_speaker.mp4 is three astronauts, the one on the left holding the microphone and
talking; reframe_cut.mp4 is four seconds of that, then a camera cut to one astronaut in the
middle of a different room.
"""

import json
import os
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from services.v1.video import reframe as rf

from conftest import API_KEY, ROOT

DATA = os.path.join(ROOT, "tests", "data")
SPEAKER = os.path.join(DATA, "reframe_speaker.mp4")
CUT = os.path.join(DATA, "reframe_cut.mp4")
SETTINGS = dict(rf.DEFAULTS)


def size_of(path):
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height", "-of", "json", path], capture_output=True, text=True)
    s = json.loads(out.stdout)["streams"][0]
    return s["width"], s["height"]


# --- Sound features ----------------------------------------------------------------------

def test_mfcc_matches_python_speech_features():
    ref = np.load(os.path.join(DATA, "mfcc_reference.npz"))
    got = rf.mfcc(ref["audio"])
    assert got.shape == ref["mfcc"].shape
    assert np.abs(got - ref["mfcc"]).max() < 1e-6


def test_mfcc_of_nothing_is_empty():
    assert rf.mfcc(np.zeros(0)).shape == (0, 13)


# --- Shapes ------------------------------------------------------------------------------

def test_tall_crop_of_a_wide_video_moves_sideways():
    assert rf.crop_box(1920, 1080, "9:16") == {"width": 608, "height": 1080, "moves": True, "y": 0}


def test_wide_crop_of_a_tall_video_sits_in_the_middle():
    box = rf.crop_box(1080, 1920, "1:1")
    assert box["width"] == 1080 and box["height"] == 1080 and box["moves"] is False and box["y"] == 420


def test_output_sizes():
    assert rf.output_size("9:16", 1080) == (1080, 1920)
    assert rf.output_size("4:5", 1080) == (1080, 1350)
    assert rf.output_size("1:1", 720) == (720, 720)


def test_a_given_path_is_sorted_and_kept_inside_the_frame():
    crop = {"width": 400}
    pts = rf.clean_path([{"time": 3, "x": 5000}, {"time": 1, "x": -20}], crop, 1280)
    assert pts == [{"time": 0.0, "x": 0}, {"time": 1.0, "x": 0}, {"time": 3.0, "x": 880}]


# --- Camera rules (made-up analysis) -------------------------------------------------------

def analysis(n, tracks, cuts=(0,), scored=True):
    out = []
    for i, t in enumerate(tracks):
        cx = np.full(n, np.nan)
        score = np.full(n, np.nan)
        a, b = t.get("span", (0, n))
        cx[a:b] = t["x"]
        score[a:b] = t.get("talk", np.full(n, -5.0))[a:b]
        area = np.where(np.isfinite(cx), t.get("area", 100.0), np.nan)
        out.append({"id": i, "cx": cx, "area": area, "score": score, "shot": 0})
    return {"frames": n, "tracks": out, "cuts": list(cuts), "scored": scored}


def talk(n, *ranges):
    s = np.full(n, -5.0)
    for a, b in ranges:
        s[a:b] = 3.0
    return s


def test_follows_whoever_talks_and_cuts_when_they_change():
    n = 250  # 10 s
    a = analysis(n, [{"x": 200, "talk": talk(n, (0, 100))}, {"x": 1000, "talk": talk(n, (100, 250))}])
    who = rf.choose_shots(a, "speaker", SETTINGS)
    assert who[50] == 0 and who[200] == 1
    switch = int(np.flatnonzero(np.diff(who))[0]) + 1
    assert 100 <= switch <= 125  # after the new speaker has talked a little, not before


def test_a_short_interjection_does_not_move_the_camera():
    n = 250
    a = analysis(n, [{"x": 200, "talk": talk(n, (0, 250))}, {"x": 1000, "talk": talk(n, (100, 108))}])
    who = rf.choose_shots(a, "speaker", SETTINGS)
    assert set(who.tolist()) == {0}


def test_never_cuts_more_often_than_the_hold_time():
    n = 300
    a = analysis(n, [
        {"x": 200, "talk": talk(n, (0, 60), (90, 120))},
        {"x": 1000, "talk": talk(n, (60, 90), (120, 300))},
    ])
    who = rf.choose_shots(a, "speaker", dict(SETTINGS, min_hold=3.0))
    switches = np.flatnonzero(np.diff(who)) + 1
    assert all(b - a >= 75 for a, b in zip(switches[:-1], switches[1:]))


def test_holds_on_the_last_speaker_when_nobody_on_screen_talks():
    n = 250
    a = analysis(n, [{"x": 200, "talk": talk(n, (0, 50))}, {"x": 1000}])
    assert set(rf.choose_shots(a, "speaker", SETTINGS).tolist()) == {0}


def test_a_camera_cut_moves_at_once_to_the_new_shot():
    n = 200
    a = analysis(n, [
        {"x": 200, "talk": talk(n, (0, 100)), "span": (0, 100)},
        {"x": 1000, "talk": talk(n, (100, 200)), "span": (100, 200)},
    ], cuts=(0, 100))
    who = rf.choose_shots(a, "speaker", dict(SETTINGS, min_hold=10))
    assert who[99] == 0 and who[100] == 1


def test_largest_face_mode_ignores_who_is_talking():
    n = 100
    a = analysis(n, [{"x": 200, "talk": talk(n, (0, 100)), "area": 50.0}, {"x": 1000, "area": 400.0}])
    assert set(rf.choose_shots(a, "largest_face", SETTINGS).tolist()) == {1}


def test_camera_path_jumps_between_speakers_and_stays_in_frame():
    n = 250
    a = analysis(n, [{"x": 30, "talk": talk(n, (0, 125))}, {"x": 1250, "talk": talk(n, (125, 250))}])
    who = rf.choose_shots(a, "speaker", SETTINGS)
    left = rf.camera_path(a, who, {"width": 1280}, {"width": 404}, SETTINGS)
    assert left[10] == 0 and left[-1] == 1280 - 404
    switch = int(np.flatnonzero(np.diff(who))[0]) + 1
    assert left[switch - 1] == 0 and left[switch] == 876  # a cut, not a pan

    points = rf.path_points(left)
    assert points[0] == {"time": 0.0, "x": 0} and len(points) == 2


# --- Real footage ----------------------------------------------------------------------------

def test_follows_the_astronaut_who_is_talking(tmp_path):
    out = str(tmp_path / "out.mp4")
    result = rf.reframe_video(SPEAKER, out, str(tmp_path), {})
    assert result["faces"] == 3
    assert size_of(out) == (1080, 1920)
    # the one talking stands on the left: the crop sits on the left third of the 640 wide frame
    xs = [p["x"] for p in result["path"]]
    assert max(xs) < 160, xs
    assert result["switches"] == 0 and result["camera_cuts"] == 0
    assert result["crop"] == {"width": 202, "height": 360}


def test_finds_the_camera_cut_and_follows_the_new_shot(tmp_path):
    out = str(tmp_path / "out.mp4")
    result = rf.reframe_video(CUT, out, str(tmp_path), {})
    assert result["camera_cuts"] == 1
    before = [p["x"] for p in result["path"] if p["time"] < 3.9]
    after = [p["x"] for p in result["path"] if p["time"] >= 4.1]
    assert max(before) < 160
    assert all(180 < x < 260 for x in after), after  # the astronaut in the middle


def test_a_given_path_skips_the_analysis(tmp_path, monkeypatch):
    def no_analysis(*a, **k):
        raise AssertionError("should not analyse")

    monkeypatch.setattr(rf, "analyse", no_analysis)
    out = str(tmp_path / "out.mp4")
    result = rf.reframe_video(SPEAKER, out, str(tmp_path), {"path": [{"time": 0, "x": 0}, {"time": 4, "x": 438}], "aspect": "1:1", "output_width": 360})
    assert size_of(out) == (360, 360)
    assert result["path"] == [{"time": 0.0, "x": 0}, {"time": 4.0, "x": 280}]


def test_center_mode_does_not_look_for_faces(tmp_path, monkeypatch):
    monkeypatch.setattr(rf, "analyse", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no")))
    result = rf.reframe_video(SPEAKER, str(tmp_path / "o.mp4"), str(tmp_path), {"follow": "center"})
    assert result["path"] == [{"time": 0.0, "x": 219}]


def test_a_file_with_no_video_is_refused(tmp_path):
    audio = os.path.join(DATA, "jfk.ogg")
    with pytest.raises(rf.ReframeError):
        rf.reframe_video(audio, str(tmp_path / "o.mp4"), str(tmp_path), {})


# --- The request -----------------------------------------------------------------------------

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
    from routes.v1.video import reframe as route

    uploads = []

    def fake_upload(path):
        kept = tmp_path / os.path.basename(path)
        with open(path, "rb") as src, open(kept, "wb") as dst:
            dst.write(src.read())
        uploads.append(str(kept))
        return f"https://files.example.com/{os.path.basename(path)}"

    monkeypatch.setattr(route, "upload_file", fake_upload)
    monkeypatch.delenv("REDIS_URL", raising=False)
    application = app_module.create_app()
    application.testing = True
    c = application.test_client()
    c.uploads = uploads
    return c


def test_request_reframes_and_uploads(client, file_server):
    res = client.post("/v1/video/reframe", json={"video_url": f"{file_server}/reframe_speaker.mp4", "id": "clip-1"}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["id"] == "clip-1"
    r = body["response"]
    assert r["file_url"].startswith("https://files.example.com/") and r["file_url"].endswith("_reframed.mp4")
    assert r["width"] == 1080 and r["height"] == 1920 and r["faces"] == 3
    assert size_of(client.uploads[0]) == (1080, 1920)


def test_request_needs_the_api_key_and_a_video(client):
    assert client.post("/v1/video/reframe", json={"video_url": "https://x.example/a.mp4"}).status_code == 401
    res = client.post("/v1/video/reframe", json={"aspect": "9:16"}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 400
    res = client.post("/v1/video/reframe", json={"video_url": "https://x.example/a.mp4", "follow": "dog"}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 400


def test_request_says_when_the_file_is_not_a_video(client, file_server):
    res = client.post("/v1/video/reframe", json={"video_url": f"{file_server}/jfk.ogg"}, headers={"X-API-Key": API_KEY})
    assert res.status_code == 400
    assert "no video" in res.get_json()["message"]
