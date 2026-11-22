"""
/v1/media/generate/ass with a transcript the caller already has (`segments`): the captions
are made from it, the video is not transcribed again, and with the canvas size given it is
not downloaded either. The output matches what a fresh transcription of the same words makes.
"""

import os

import pytest

from conftest import API_KEY

from services import ass_toolkit

WHISPER_SHAPED = {
    "segments": [
        {
            "start": 0.0,
            "end": 1.6,
            "text": " Hello there.",
            "words": [
                {"word": " Hello", "start": 0.0, "end": 0.5},
                {"word": " there.", "start": 0.6, "end": 1.6},
            ],
        },
        {
            "start": 2.0,
            "end": 3.0,
            "text": " Bye now.",
            "words": [
                {"word": " Bye", "start": 2.0, "end": 2.4},
                {"word": " now.", "start": 2.5, "end": 3.0},
            ],
        },
    ]
}

# The same transcript as the dashboard keeps it: words without Whisper's leading space,
# and no text.
GIVEN = [
    {"start": 2.0, "end": 3.0, "words": [{"word": "Bye", "start": 2.0, "end": 2.4}, {"word": "now.", "start": 2.5, "end": 3.0}]},
    {"start": 0.0, "end": 1.6, "words": [{"word": "Hello", "start": 0.0, "end": 0.5}, {"word": "there.", "start": 0.6, "end": 1.6}]},
]


@pytest.fixture
def no_side_effects(monkeypatch):
    calls = {"transcribe": 0, "download": 0}

    def transcribe(*args, **kwargs):
        calls["transcribe"] += 1
        return WHISPER_SHAPED

    def download(url, folder):
        calls["download"] += 1
        return os.path.join(folder, "video.mp4")

    monkeypatch.setattr(ass_toolkit, "generate_transcription", transcribe)
    monkeypatch.setattr(ass_toolkit, "download_file", download)
    monkeypatch.setattr(ass_toolkit, "get_available_fonts", lambda: ["Arial"])
    monkeypatch.setattr(ass_toolkit, "get_video_resolution", lambda path: (1080, 1920))
    return calls


def test_given_segments_become_whisper_shaped():
    result = ass_toolkit.transcription_from_segments(GIVEN)
    assert result["segments"][0]["text"] == " Hello there."
    assert [w["word"] for w in result["segments"][0]["words"]] == [" Hello", " there."]
    assert result["segments"][1]["start"] == 2.0


def test_blank_words_dropped_and_end_never_before_start():
    result = ass_toolkit.transcription_from_segments(
        [{"start": 1.0, "end": 0.5, "words": [{"word": " ", "start": 1, "end": 1}, {"word": "Hi", "start": 1.0, "end": 0.9}]}]
    )
    segment = result["segments"][0]
    assert segment["end"] == 1.0
    assert segment["words"] == [{"word": " Hi", "start": 1.0, "end": 1.0}]


@pytest.mark.parametrize("style", ["classic", "karaoke", "highlight", "underline", "word_by_word"])
def test_same_captions_as_a_fresh_transcription(no_side_effects, style):
    settings = {"style": style, "max_words_per_line": 3}
    fresh = ass_toolkit.generate_ass_captions_v1("http://x/v.mp4", None, dict(settings), [], [], "fresh")
    fresh_text = open(fresh, encoding="utf-8").read()
    assert no_side_effects["transcribe"] == 1

    given = ass_toolkit.generate_ass_captions_v1(
        "http://x/v.mp4", None, dict(settings), [], [], "given", segments=GIVEN
    )
    assert open(given, encoding="utf-8").read() == fresh_text
    assert no_side_effects["transcribe"] == 1  # not heard again


def test_video_not_downloaded_with_transcript_and_canvas(no_side_effects):
    path = ass_toolkit.generate_ass_captions_v1(
        "http://x/v.mp4", None, {"style": "highlight"}, [], [], "nodl",
        PlayResX=1080, PlayResY=1920, segments=GIVEN,
    )
    assert "PlayResX: 1080" in open(path, encoding="utf-8").read()
    assert no_side_effects == {"transcribe": 0, "download": 0}


def test_video_still_downloaded_for_its_size(no_side_effects):
    ass_toolkit.generate_ass_captions_v1(
        "http://x/v.mp4", None, {"style": "highlight"}, [], [], "size", segments=GIVEN
    )
    assert no_side_effects == {"transcribe": 0, "download": 1}


@pytest.fixture
def client(monkeypatch):
    import app as app_module
    from services import job_queue

    monkeypatch.delenv("REDIS_URL", raising=False)
    job_queue.set_connection(None)
    application = app_module.create_app()
    application.testing = True
    yield application.test_client()
    job_queue.set_connection(None)


def test_route_makes_captions_from_given_segments(client, no_side_effects, monkeypatch):
    from routes.v1.media import generate_ass as route

    uploaded = []
    monkeypatch.setattr(route, "upload_file", lambda path: uploaded.append(open(path).read()) or "https://bucket/c.ass")
    res = client.post(
        "/v1/media/generate/ass",
        json={
            "media_url": "https://x/v.mp4",
            "canvas_width": 1080,
            "canvas_height": 1920,
            "settings": {"style": "highlight"},
            "segments": GIVEN,
        },
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 200, res.get_json()
    assert res.get_json()["response"] == "https://bucket/c.ass"
    assert "Hello" in uploaded[0]
    assert no_side_effects == {"transcribe": 0, "download": 0}


def test_route_refuses_a_word_without_times(client):
    res = client.post(
        "/v1/media/generate/ass",
        json={"media_url": "https://x/v.mp4", "segments": [{"start": 0, "end": 1, "words": [{"word": "Hi"}]}]},
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 400
