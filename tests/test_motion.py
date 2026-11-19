"""
/v1/video/motion: lay motion pieces (lower thirds, titles, stat cards...) and sound effects
over a video.

The plan, the HTML of the pieces and the FFmpeg graph are checked as data. The sound mix
runs for real with FFmpeg. The full render (HyperFrames draws the pieces in Chromium) runs
when the `hyperframes` command is installed, as it is in the Docker image and in CI.
"""

import json
import os
import shutil
import subprocess
import threading
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from services.v1.video import motion as mo
from services.v1.video.motion.pieces import PIECES, sheet_html

from conftest import API_KEY, ROOT

DATA = os.path.join(ROOT, "tests", "data")
CLIP = os.path.join(DATA, "reframe_cut.mp4")  # 640x360, 8 s, with sound
HAS_HYPERFRAMES = shutil.which(mo.HYPERFRAMES) is not None


def frame(path, at, size=(64, 36)):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{at}", "-i", path, "-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]}", "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(size[1], size[0], 3).astype(np.int16)


def loudness(path, start, length):
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{start}", "-t", f"{length}", "-i", path, "-ac", "1", "-ar", "16000", "-f", "s16le", "-"],
        capture_output=True, check=True,
    ).stdout
    samples = np.frombuffer(raw, dtype=np.int16).astype(np.float64)
    return float(np.sqrt(np.mean(samples ** 2))) if len(samples) else 0.0


def length_of(path):
    return mo.probe(path)["duration"]


# --- The plan --------------------------------------------------------------------------------

def test_a_piece_gets_its_default_length_and_sound():
    pieces, cues = mo.plan([{"type": "lower_third", "start": 2, "title": "Ana"}], [], 30)
    assert pieces[0]["duration"] == PIECES["lower_third"][0]
    assert cues == [{"sound": "slide", "at": 2.0, "volume": 1.0}]


def test_lengths_are_kept_between_shortest_and_longest_and_inside_the_video():
    pieces, _ = mo.plan(
        [
            {"type": "keyword", "start": 1, "title": "Big", "duration": 30},
            {"type": "title", "start": 2, "title": "Hi", "duration": 0.1},
            {"type": "follow", "start": 9, "title": "@me"},
        ],
        [], 10,
    )
    assert [p["duration"] for p in pieces] == [PIECES["keyword"][3], PIECES["title"][2], 1.0]


def test_pieces_past_the_end_are_left_out_and_the_rest_are_sorted():
    pieces, cues = mo.plan(
        [{"type": "title", "start": 5, "title": "B"}, {"type": "title", "start": 1, "title": "A"}, {"type": "title", "start": 40, "title": "C"}],
        [{"sound": "riser", "at": 50}], 30,
    )
    assert [p["title"] for p in pieces] == ["A", "B"]
    assert [c["at"] for c in cues] == [1, 5]


def test_a_piece_can_have_no_sound_or_another_one():
    _, cues = mo.plan(
        [{"type": "title", "start": 1, "title": "A", "sound": None}, {"type": "title", "start": 3, "title": "B", "sound": "riser"}],
        [], 30,
    )
    assert [c["sound"] for c in cues] == ["riser"]


def test_piece_sounds_can_be_switched_off_and_volume_scales_every_sound():
    _, cues = mo.plan(
        [{"type": "title", "start": 1, "title": "A"}],
        [{"sound": "pop", "at": 2, "volume": 1.5}], 30, sound_volume=0.5, piece_sounds=False,
    )
    assert cues == [{"sound": "pop", "at": 2.0, "volume": 0.75}]
    _, silent = mo.plan([{"type": "title", "start": 1, "title": "A"}], [], 30, sound_volume=0)
    assert silent == []


def test_words_are_needed_and_sounds_must_exist():
    with pytest.raises(mo.MotionError, match="needs its words"):
        mo.plan([{"type": "stat", "start": 1, "title": "  "}], [], 30)
    with pytest.raises(mo.MotionError, match="no sound called"):
        mo.plan([], [{"sound": "airhorn", "at": 1}], 30)
    with pytest.raises(mo.MotionError, match="logo's link"):
        mo.plan([{"type": "logo", "start": 1}], [], 30)


def test_every_listed_sound_is_a_file_with_a_licence_note():
    assert {"whoosh", "pop", "ding", "impact", "tick", "riser"} <= set(mo.SOUNDS)
    for path in mo.SOUNDS.values():
        assert os.path.getsize(path) > 500
    with open(os.path.join(mo.SOUND_DIR, "LICENSE.md")) as f:
        note = f.read()
    for name in mo.SOUNDS:
        assert name in note


def test_every_piece_default_sound_exists():
    for name, (_, sound, shortest, longest) in PIECES.items():
        assert sound in mo.SOUNDS, name
        assert 0 < shortest < longest


# --- The pieces' HTML ---------------------------------------------------------------------------

def test_sheet_lays_pieces_end_to_end_and_escapes_words():
    pieces = [
        {"type": "title", "start": 4, "duration": 2.0, "title": "<b>Hi</b> & bye"},
        {"type": "lower_third", "start": 9, "duration": 3.0, "title": "Ana", "subtitle": "Host"},
    ]
    page = sheet_html(pieces, 1920, 1080, {"accent": "#ff0055", "look": "clean"}, 5.0)
    assert 'data-start="0.000" data-duration="2.000"' in page
    assert 'data-start="2.000" data-duration="3.000"' in page
    assert "&lt;b&gt;Hi&lt;/b&gt; &amp; bye" in page and "<b>Hi</b>" not in page
    assert "--accent:#ff0055" in page and "look-clean" in page and " tall" not in page
    assert 'data-duration="5.000" data-width="1920" data-height="1080"' in page
    assert "gsap" not in page.lower()


def test_bad_colours_and_fonts_fall_back_to_safe_ones():
    page = sheet_html([], 1080, 1920, {"accent": "red;}</style>", "font": "x\";}", "look": "nope"}, 1.0)
    assert "--accent:#46e5b7" in page and '--font:"Roboto"' in page and "look-bold" in page and " tall" in page


def test_a_plain_number_counts_up_and_other_stats_just_appear():
    counting = sheet_html([{"type": "stat", "start": 0, "duration": 3, "title": "$40k", "subtitle": "a month"}], 1080, 1920, {}, 3)
    assert 'class="st-n" style="--to:40"' in counting and "$" in counting and ">k<" in counting
    plain = sheet_html([{"type": "stat", "start": 0, "duration": 3, "title": "1,200 people"}], 1080, 1920, {}, 3)
    assert "st-n" not in plain.split("</style>")[1] and "1,200 people" in plain


# --- The FFmpeg graph ----------------------------------------------------------------------------

def test_graph_cuts_each_piece_from_the_sheet_and_places_it_at_its_time():
    pieces = [{"type": "title", "start": 4.0, "duration": 2.0}, {"type": "keyword", "start": 10.5, "duration": 1.0}]
    graph, video, audio = mo.filter_graph(pieces, [], True, 30)
    assert "[1:v]split=2[s0][s1]" in graph
    assert "[s0]trim=start=0.000:end=2.000,setpts=PTS-STARTPTS+4.000/TB[p0]" in graph
    assert "[s1]trim=start=2.000:end=3.000,setpts=PTS-STARTPTS+10.500/TB[p1]" in graph
    assert "enable='between(t,10.500,11.500)'" in graph
    assert video == "[vout]" and audio is None


def test_graph_mixes_sounds_over_the_video_sound_or_over_silence():
    cues = [{"sound": "pop", "at": 1.25, "volume": 0.8}]
    graph, video, audio = mo.filter_graph([], cues, True, 30)
    assert "adelay=1250|1250" in graph and "volume=0.800" in graph
    assert "[0:a][a0]amix=inputs=2:duration=first" in graph and video == "[0:v]" and audio == "[aout]"
    silent, _, _ = mo.filter_graph([], cues, False, 30)
    assert "anullsrc" in silent and "[base][a0]amix=inputs=2" in silent


# --- For real ------------------------------------------------------------------------------------

def test_sounds_alone_are_mixed_in_and_the_picture_is_untouched(tmp_path):
    out = str(tmp_path / "out.mp4")
    result = mo.render_motion(CLIP, out, str(tmp_path), {"sounds": [{"sound": "impact", "at": 3.0, "volume": 2}]})
    assert result["pieces"] == 0 and result["sounds"] == 1
    assert abs(length_of(out) - length_of(CLIP)) < 0.1
    assert loudness(out, 3.0, 0.3) > loudness(CLIP, 3.0, 0.3) * 1.2
    assert np.abs(frame(out, 2.0) - frame(CLIP, 2.0)).mean() < 3


def test_nothing_to_add_is_refused(tmp_path):
    with pytest.raises(mo.MotionError, match="Nothing to add"):
        mo.render_motion(CLIP, str(tmp_path / "o.mp4"), str(tmp_path), {"pieces": [{"type": "title", "start": 99, "title": "late"}]})


def test_a_file_with_no_picture_is_refused(tmp_path):
    with pytest.raises(mo.MotionError, match="no picture"):
        mo.render_motion(os.path.join(DATA, "jfk.ogg"), str(tmp_path / "o.mp4"), str(tmp_path), {"sounds": [{"sound": "pop", "at": 1}]})


# CI sets REQUIRE_HYPERFRAMES so this can never be skipped there by mistake.
@pytest.mark.skipif(not HAS_HYPERFRAMES and not os.environ.get("REQUIRE_HYPERFRAMES"), reason="HyperFrames is not installed")
def test_pieces_are_drawn_over_the_video_only_while_they_show(tmp_path):
    out = str(tmp_path / "out.mp4")
    request = {
        "style": {"accent": "#ff2a6d", "look": "bold"},
        "pieces": [
            {"type": "keyword", "start": 1.0, "duration": 1.5, "title": "Wow"},
            {"type": "chapter", "start": 4.5, "duration": 2.0, "title": "Part two", "subtitle": "Chapter 2"},
        ],
    }
    result = mo.render_motion(CLIP, out, str(tmp_path), request)
    assert result["pieces"] == 2 and result["sounds"] == 2
    assert result["width"] == 640 and result["height"] == 360
    assert abs(length_of(out) - length_of(CLIP)) < 0.1
    # Nothing on screen before the first piece or between the two.
    assert np.abs(frame(out, 0.5) - frame(CLIP, 0.5)).mean() < 4
    assert np.abs(frame(out, 3.6) - frame(CLIP, 3.6)).mean() < 4
    # The word, then the chapter card's accent band, are there while they show.
    word = np.abs(frame(out, 1.8) - frame(CLIP, 1.8))[14:22, 22:42]  # the middle of the frame
    assert word.mean() > 20
    chapter = frame(out, 5.5)
    assert np.abs(chapter - frame(CLIP, 5.5)).mean() > 8
    band = chapter[16:22].reshape(-1, 3).mean(axis=0)  # the band sits at about 40-55% down
    assert band[0] > 180 and band[1] < 120  # #ff2a6d
    # Each piece brought its sound.
    assert loudness(out, 1.0, 0.3) > loudness(CLIP, 1.0, 0.3)


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
    from routes.v1.video import motion as route

    uploads = []

    def fake_upload(path):
        kept = tmp_path / os.path.basename(path)
        shutil.copy(path, kept)
        uploads.append(str(kept))
        return f"https://files.example.com/{os.path.basename(path)}"

    monkeypatch.setattr(route, "upload_file", fake_upload)
    monkeypatch.delenv("REDIS_URL", raising=False)
    application = app_module.create_app()
    application.testing = True
    c = application.test_client()
    c.uploads = uploads
    return c


def test_request_mixes_sounds_and_uploads(client, file_server):
    res = client.post(
        "/v1/video/motion",
        json={"video_url": f"{file_server}/reframe_cut.mp4", "sounds": [{"sound": "ding", "at": 2}], "id": "m-1"},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()
    assert body["id"] == "m-1"
    assert body["response"]["file_url"].endswith("_motion.mp4") and body["response"]["sounds"] == 1
    assert abs(length_of(client.uploads[0]) - length_of(CLIP)) < 0.1


def test_request_checks_its_fields(client):
    headers = {"X-API-Key": API_KEY}
    assert client.post("/v1/video/motion", json={"video_url": "https://x.example/a.mp4"}).status_code == 401
    bad = [
        {"pieces": []},
        {"video_url": "https://x.example/a.mp4", "pieces": [{"type": "explosion", "start": 1}]},
        {"video_url": "https://x.example/a.mp4", "pieces": [{"type": "title", "start": -1, "title": "x"}]},
        {"video_url": "https://x.example/a.mp4", "sounds": [{"sound": "airhorn", "at": 1}]},
        {"video_url": "https://x.example/a.mp4", "style": {"accent": "red"}},
        {"video_url": "https://x.example/a.mp4", "pieces": [{"type": "title", "start": 1, "title": "x" * 201}]},
    ]
    for payload in bad:
        assert client.post("/v1/video/motion", json=payload, headers=headers).status_code == 400, payload


def test_request_with_nothing_inside_the_video_says_so(client, file_server):
    res = client.post(
        "/v1/video/motion",
        json={"video_url": f"{file_server}/reframe_cut.mp4", "pieces": [{"type": "title", "start": 60, "title": "late"}]},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 400 and "Nothing to add" in res.get_json()["message"]


def test_catalog_lists_pieces_looks_and_sounds(client):
    assert client.get("/v1/video/motion/catalog").status_code == 401
    res = client.get("/v1/video/motion/catalog", headers={"X-API-Key": API_KEY})
    assert res.status_code == 200
    body = res.get_json()
    assert set(body["pieces"]) == set(PIECES) and "bold" in body["looks"] and "whoosh" in body["sounds"]
    assert body["pieces"]["lower_third"]["sound"] == "slide"
