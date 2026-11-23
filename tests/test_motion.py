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

from services.v1.video import cutout
from services.v1.video import motion as mo
from services.v1.video.motion.pieces import DOODLES, PIECES, sheet_html

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


def test_pieces_sit_in_front_unless_they_say_behind_and_new_pieces_need_their_words():
    pieces, _ = mo.plan([
        {"type": "big_text", "start": 1, "title": "Takewon", "layer": "behind"},
        {"type": "keyword", "start": 2, "title": "Wow"},
    ], [], 30)
    assert [p["layer"] for p in pieces] == ["behind", "front"]
    with pytest.raises(mo.MotionError, match="big text piece needs its words"):
        mo.plan([{"type": "big_text", "start": 1}], [], 30)
    with pytest.raises(mo.MotionError, match="checklist piece needs its items"):
        mo.plan([{"type": "checklist", "start": 1}], [], 30)
    pieces, _ = mo.plan([{"type": "doodle", "start": 1, "shape": "crown"}, {"type": "graph", "start": 3}], [], 30)
    assert len(pieces) == 2


def test_outlines_and_punch_ins_stay_inside_the_video_and_punch_ins_cannot_overlap():
    outlines, zooms = mo.plan_effects(
        [{"start": 2, "duration": 50, "color": "#ff0000", "width": 9, "style": "sticker"}, {"start": 40}, {"start": 3, "duration": 2}],
        [{"start": 5, "duration": 1, "scale": 4}, {"start": 1, "duration": 1}],
        30,
    )
    # A sketch line is thin unless asked otherwise; a sticker ring is thicker.
    assert outlines == [
        {"start": 2.0, "end": 30.0, "style": "sticker", "color": "#ff0000", "width": 4.0},
        {"start": 3.0, "end": 5.0, "style": "sketch", "color": "#ffffff", "width": 0.45},
    ]
    assert [(z["start"], z["end"], z["scale"]) for z in zooms] == [(1.0, 2.0, 1.3), (5.0, 6.0, 2.5)]
    with pytest.raises(mo.MotionError, match="overlap"):
        mo.plan_effects([], [{"start": 1, "duration": 2}, {"start": 2, "duration": 2}], 30)


def test_behind_pieces_and_outlines_need_the_mask(tmp_path):
    request = {"pieces": [{"type": "big_text", "start": 1, "title": "Hi", "layer": "behind"}]}
    assert mo.needs_mask(request) and mo.needs_mask({"outlines": [{"start": 1}]})
    assert not mo.needs_mask({"pieces": [{"type": "title", "start": 1, "title": "x"}], "zooms": [{"start": 1, "duration": 1}]})
    with pytest.raises(mo.MotionError, match="cut out first"):
        mo.render_motion(CLIP, str(tmp_path / "o.mp4"), str(tmp_path), request)


def test_new_pieces_draw_their_items_and_drawings():
    pieces = [
        {"type": "checklist", "duration": 4, "title": "Ready", "items": ["Cut <the> pauses", "Add captions"]},
        {"type": "graph", "duration": 3, "title": "10X", "items": ["Jan", "Apr"]},
        {"type": "doodle", "duration": 2, "shape": "crown", "x": 40, "y": 10},
        {"type": "big_text", "duration": 2, "title": "Takewon", "y": 22},
    ]
    html = sheet_html(pieces, 1080, 1920, {"accent": "#ffcf3f"}, 11)
    assert "Cut &lt;the&gt; pauses" in html and html.count('class="ck-row"') == 2
    assert html.count("data:image/svg+xml") == 2 + 1 + 1  # two ticks, the graph line, the doodle
    assert "%23ffcf3f" in html  # the accent colour reaches the drawings
    assert "left:40.0%;top:10.0%" in html and "top:22.0%" in html
    # HyperFrames draws inline <svg> only in a sheet's first piece, so drawings are pictures.
    assert "<svg" not in html
    assert set(DOODLES) >= {"crown", "star", "heart", "arrow"}


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


def test_graph_lays_behind_pieces_then_outlines_then_the_person_then_front_pieces():
    pieces = [
        {"type": "big_text", "start": 1.0, "duration": 2.0, "layer": "behind"},
        {"type": "keyword", "start": 1.5, "duration": 1.0, "layer": "front"},
    ]
    outlines = [
        {"start": 0.0, "end": 4.0, "style": "sticker", "color": "#ffffff", "width": 1.5},
        {"start": 5.0, "end": 7.0, "style": "sketch", "color": "#ffffff", "width": 0.45, "_input": 3},
    ]
    zooms = [{"start": 5.0, "end": 6.0, "scale": 1.3, "focus_y": 0.35}]
    graph, video, _ = mo.filter_graph(pieces, [], True, 8, mask_input=2, outlines=outlines, zooms=zooms, size=(640, 360), fps=25)
    assert "split=2[base][keep]" in graph and "[2:v]format=gray,scale=640:360" in graph and "split=2[m0][m1]" in graph
    assert "[keep][m0]alphamerge[person]" in graph
    behind = graph.index("[p0]overlay")
    ring = graph.index("[ring0]overlay")
    person = graph.index("[person]overlay")
    front = graph.index("[p1]overlay")
    assert behind < ring < person < front
    assert "lutrgb=r=255:g=255:b=255" in graph and "gblur=sigma=2.70" in graph  # 1.5% of 360 px, halved
    # The sketch line is its own drawn video (input 3), shifted to its time, under the person.
    assert "[3:v]setpts=PTS-STARTPTS+5.000/TB[sk1]" in graph
    assert ring < graph.index("[sk1]overlay") < person
    assert "zoompan=z='1+0.3000*between(it,5.000,6.000)" in graph and "s=640x360:fps=25.000000" in graph
    assert video == "[vout]"


def test_graph_without_a_mask_lays_every_piece_in_front():
    graph, _, _ = mo.filter_graph([{"type": "title", "start": 1.0, "duration": 2.0}], [], True, 8)
    assert "alphamerge" not in graph and "[keep]" not in graph


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


@pytest.fixture(scope="module")
def mask(tmp_path_factory):
    out = str(tmp_path_factory.mktemp("mask") / "mask.mp4")
    cutout.make_mask(CLIP, out)
    return out


def red(img):
    return (img[..., 0] > 170) & (img[..., 1] < 90) & (img[..., 2] < 90)


def test_an_outline_rings_the_people_and_a_punch_in_fills_the_frame(tmp_path, mask):
    out = str(tmp_path / "out.mp4")
    request = {
        "outlines": [{"start": 1.0, "duration": 2.0, "color": "#ff0000", "width": 2}],
        "zooms": [{"start": 5.0, "duration": 1.5, "scale": 1.5}],
    }
    result = mo.render_motion(CLIP, out, str(tmp_path), request, mask=mask)
    assert result["outlines"] == 1 and result["zooms"] == 1 and result["pieces"] == 0
    assert result["width"] == 640 and result["height"] == 360
    assert abs(length_of(out) - length_of(CLIP)) < 0.15
    size = (320, 180)
    m = frame(mask, 2.0, size).mean(axis=2)
    ring = red(frame(out, 2.0, size)) & ~red(frame(CLIP, 2.0, size))
    assert ring.sum() > 100
    assert m[ring].mean() < 128  # the ring sits outside the person, not on them
    assert red(frame(out, 4.0, size)).sum() <= red(frame(CLIP, 4.0, size)).sum() + 5  # gone once it ends
    # The punch-in changes the picture while it lasts and not after.
    assert np.abs(frame(out, 6.0) - frame(CLIP, 6.0)).mean() > 15
    assert np.abs(frame(out, 7.5) - frame(CLIP, 7.5)).mean() < 4


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


@pytest.mark.skipif(not HAS_HYPERFRAMES and not os.environ.get("REQUIRE_HYPERFRAMES"), reason="HyperFrames is not installed")
def test_big_words_behind_the_speaker_are_hidden_where_the_person_is(tmp_path, mask):
    out = str(tmp_path / "out.mp4")
    request = {
        "style": {"text": "#ff0000"},
        "pieces": [{"type": "big_text", "start": 1.0, "duration": 3.0, "title": "WOW", "layer": "behind", "y": 25, "sound": None}],
    }
    result = mo.render_motion(CLIP, out, str(tmp_path), request, mask=mask)
    assert result["behind"] == 1
    size = (320, 180)
    m = frame(mask, 2.5, size).mean(axis=2)
    after, before = frame(out, 2.5, size), frame(CLIP, 2.5, size)
    words = red(after) & ~red(before)
    assert words.sum() > 200  # the words show around the people
    person = m > 245
    assert np.abs(after[person] - before[person]).mean() < 8  # and not over them
    assert (words & person).sum() < words.sum() * 0.02


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


def test_request_with_an_outline_cuts_the_speaker_out_and_hands_back_the_mask(client, file_server):
    res = client.post(
        "/v1/video/motion",
        json={"video_url": f"{file_server}/reframe_cut.mp4", "outlines": [{"start": 1, "duration": 2}]},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    body = res.get_json()["response"]
    assert body["mask_url"].endswith("_mask.mp4") and body["outlines"] == 1
    assert len(client.uploads) == 2


def test_request_checks_the_new_fields(client):
    headers = {"X-API-Key": API_KEY}
    url = "https://x.example/a.mp4"
    bad = [
        {"video_url": url, "pieces": [{"type": "big_text", "start": 1, "title": "x", "layer": "middle"}]},
        {"video_url": url, "pieces": [{"type": "doodle", "start": 1, "shape": "dragon"}]},
        {"video_url": url, "pieces": [{"type": "checklist", "start": 1, "items": ["a"] * 7}]},
        {"video_url": url, "pieces": [{"type": "doodle", "start": 1, "x": 120}]},
        {"video_url": url, "zooms": [{"start": 1, "duration": 1, "scale": 3}]},
        {"video_url": url, "zooms": [{"start": 1}]},
        {"video_url": url, "outlines": [{"start": 1, "color": "white"}]},
    ]
    for payload in bad:
        assert client.post("/v1/video/motion", json=payload, headers=headers).status_code == 400, payload


def test_catalog_lists_pieces_looks_and_sounds(client):
    assert client.get("/v1/video/motion/catalog").status_code == 401
    res = client.get("/v1/video/motion/catalog", headers={"X-API-Key": API_KEY})
    assert res.status_code == 200
    body = res.get_json()
    assert set(body["pieces"]) == set(PIECES) and "bold" in body["looks"] and "whoosh" in body["sounds"]
    assert body["pieces"]["lower_third"]["sound"] == "slide"
    assert body["layers"] == ["front", "behind"] and "crown" in body["doodles"]


def test_sketch_outline_traces_a_wobbly_line_outside_the_mask_and_redraws_it(tmp_path):
    from services.v1.video.motion import sketch

    # A white disc on black, as a speaker's mask would be.
    mask = str(tmp_path / "mask.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=160x160:d=1:r=25",
         "-vf", "geq=lum='if(lt(hypot(X-80,Y-80),40),255,0)',format=gray", "-c:v", "libx264", "-pix_fmt", "yuv420p", mask],
        check=True,
    )
    disc = np.zeros((160, 160), bool)
    yy, xx = np.mgrid[:160, :160]
    disc[np.hypot(xx - 80, yy - 80) < 40] = True
    a = sketch.trace(disc, offset=6, wobble=1.5, seed=1)
    b = sketch.trace(disc, offset=6, wobble=1.5, seed=2)
    assert len(a) == 1
    radius = np.hypot(a[0][:, 0] - 80, a[0][:, 1] - 80)
    assert 42 < radius.mean() < 50  # a little way outside the disc
    assert not np.allclose(a[0], b[0])  # each redraw wobbles differently

    out = str(tmp_path / "line.mov")
    assert sketch.render(mask, out, 0.0, 1.0, 160, 160, 25) == 25
    frame = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", "0.8", "-i", out, "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgba", "-"],
        capture_output=True, check=True,
    ).stdout
    rgba = np.frombuffer(frame, np.uint8).reshape(160, 160, 4)
    ink = rgba[..., 3] > 128
    assert ink.any() and not ink[80, 80]  # a line, not a filled shape
    assert (rgba[ink][:, :3] == 255).all()  # the line keeps its colour to the edges


def test_magazine_graph_and_tick_list_look():
    pieces = [
        {"type": "big_text", "duration": 3, "title": "Opus"},
        {"type": "graph", "duration": 3.5, "title": "10X"},
        {"type": "checklist", "duration": 4, "title": "What it does", "items": ["Run an agent", "Ship it"]},
    ]
    page = sheet_html(pieces, 1080, 1920, {}, 10.5)
    assert '"Playfair Display"' in page and "<span>Opus</span>" in page  # a serif masthead
    for month in ("JAN", "FEB", "MAR", "APR", "MAY", "JUN"):  # months along the top by default
        assert f">{month}<" in page
    assert page.count('class="ck-skel"') == 2 and page.count('class="ck-seg"') == 2
    assert ">0 of 2<" in page and ">1 of 2<" in page and ">2 of 2<" in page


def test_camera_moves_dolly_pop_and_through():
    zooms = mo.plan_effects([], [
        {"start": 1, "duration": 2, "style": "dolly"},
        {"start": 4, "duration": 2, "style": "pop"},
        {"start": 7, "duration": 0.5, "style": "through"},
        {"start": 9, "duration": 1, "style": "wobble"},
    ], 20)[1]
    assert [(z["style"], z["scale"]) for z in zooms] == [("dolly", 1.3), ("pop", 1.0), ("through", 2.4), ("punch", 1.3)]
    assert mo.needs_mask({"zooms": [{"start": 1, "style": "dolly"}]})
    assert not mo.needs_mask({"zooms": [{"start": 1, "style": "through"}]})
    zooms[0]["_input"], zooms[1]["_input"], zooms[1]["_head"] = 3, 4, 0.25
    graph, video, _ = mo.filter_graph([], [], True, 20, mask_input=2, zooms=zooms, size=(360, 640), fps=25)
    # The dolly pushes into the still of the room, under the speaker.
    dolly = graph.index("[3:v]scale=360:640,setsar=1,zoompan=z='1+0.3000*min(1,it/2.000)'")
    person = graph.index("[person]overlay")
    assert dolly < person
    # The pop: blurred room, then the card, then the speaker scaled so the head clears the card.
    assert "[pc1]scale" not in graph and "[pcard0]overlay" in graph and person < graph.index("[pbg0]overlay") < graph.index("[pcard0]overlay") < graph.index("[pp0]overlay")
    assert "alphamerge,scale=" in graph
    # The through push speeds up and blurs at its end; the plain punch-in stays eased.
    assert "pow((it-7.000)/0.500,2.2)" in graph and "gblur=sigma=9.0:enable='between(t,7.350,7.500)'" in graph
    assert "min(1,(it-9.000)/0.12)" in graph


def test_clean_plate_paints_the_speaker_out(tmp_path):
    clip = str(tmp_path / "c.mp4")
    mask = str(tmp_path / "m.mp4")
    # Grey room with a red "speaker" square in the middle; the mask covers the square.
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=gray:s=160x160:d=1:r=25",
                    "-vf", "drawbox=x=60:y=60:w=40:h=40:color=red:t=fill", "-pix_fmt", "yuv420p", clip], check=True)
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=black:s=160x160:d=1:r=25",
                    "-vf", "drawbox=x=60:y=60:w=40:h=40:color=white:t=fill,format=gray", "-pix_fmt", "yuv420p", mask], check=True)
    out = mo.clean_plate(clip, mask, 0.2, str(tmp_path / "room.png"))
    import cv2
    img = cv2.imread(out)
    b, g, r = img[80, 80].astype(int)
    assert abs(r - g) < 25 and abs(r - b) < 25  # the red square is gone, filled with grey
