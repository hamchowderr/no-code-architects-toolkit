"""
/v1/ffmpeg/compose may not make FFmpeg read, write or load a file on this server
(services/v1/ffmpeg/compose_guard.py). drawtext's textfile alone would print the server's
own keys into a video.
"""

import os

import pytest

from conftest import API_KEY
from services.v1.ffmpeg import ffmpeg_compose
from services.v1.ffmpeg.compose_guard import compose_problem, filter_problem

VIDEO = "https://files.example.com/a.mp4"


def request(**extra):
    body = {
        "inputs": [{"file_url": VIDEO}],
        "outputs": [{"options": [{"option": "-c:v", "argument": "libx264"}]}],
    }
    body.update(extra)
    return body


REFUSED_FILTERS = [
    "[0:v]drawtext=textfile=/proc/1/environ:fontsize=20[v]",
    "drawtext=fontsize=20:textfile=/etc/passwd",
    "drawtext=textfile='/proc/self/environ'",
    "drawtext=/text=/proc/1/environ",
    "movie=/etc/passwd[m];[0:v][m]overlay",
    "amovie=/app/config.py",
    "sendcmd=f=/tmp/cmds,drawtext=text=x",
    "ladspa=file=/tmp/evil.so",
    "lut3d=file=/etc/passwd",
    "subtitles=/etc/passwd",
    "subtitles=filename=/etc/passwd",
    "drawtext=fontfile=file\\:/x:text=a",
    "drawtext=fontfile=/etc/passwd:text=a",
    "drawtext=fontfile=/usr/share/fonts/../../../etc/passwd:text=a",
]


@pytest.mark.parametrize("graph", REFUSED_FILTERS)
def test_filters_that_read_files_are_refused(graph):
    assert filter_problem(graph) is not None
    assert compose_problem(request(filters=[{"filter": graph}])) is not None


@pytest.mark.parametrize(
    "graph",
    [
        "[0:v]scale=1080:-2[a];[a][1:v]overlay=10:10[v]",
        "drawtext=font='Sans':text='It\\'s 1/2 done... a=b /etc':fontsize=40",
        "subtitles='https://files.example.com/c.ass'",
        "subtitles=https://files.example.com/c.srt",
        "highpass=f=80,afftdn=nf=-25",
        "drawtext=fontfile=/usr/share/fonts/custom/Inter.ttf:text=hi",
        "subtitles='https://files.example.com/c.ass':fontsdir=/usr/share/fonts/truetype/noto",
    ],
)
def test_ordinary_filters_are_allowed(graph):
    assert filter_problem(graph) is None


@pytest.mark.parametrize(
    "extra",
    [
        {"outputs": [{"options": [{"option": "/app/app.py"}]}]},
        {"global_options": [{"option": "-i", "argument": "/etc/passwd"}]},
        {"global_options": [{"option": "-filter_complex_script", "argument": "/tmp/x"}]},
        {"outputs": [{"options": [{"option": "-hls_segment_filename", "argument": "/app/x%d.ts"}]}]},
        {"outputs": [{"options": [{"option": "-segment_list", "argument": "../../app/x"}]}]},
        {"inputs": [{"file_url": VIDEO, "options": [{"option": "-f", "argument": "concat"}]}]},
        {"inputs": [{"file_url": VIDEO, "options": [{"option": "-f", "argument": "hls"}]}]},
        {"inputs": [{"file_url": "/etc/passwd"}]},
        {"inputs": [{"file_url": "file:///etc/passwd"}]},
        {"outputs": [{"options": [{"option": "-vf", "argument": "drawtext=textfile=/etc/passwd"}]}]},
        {"global_options": [{"option": "-vstats_file", "argument": "x"}]},
    ],
)
def test_settings_that_touch_files_are_refused(extra):
    assert compose_problem(request(**extra)) is not None


def test_ordinary_settings_are_allowed():
    body = request(
        global_options=[{"option": "-y"}],
        outputs=[
            {
                "options": [
                    {"option": "-map", "argument": "[v]"},
                    {"option": "-c:v", "argument": "libx264"},
                    {"option": "-crf", "argument": 20},
                    {"option": "-metadata", "argument": "title=Wait... it works"},
                    {"option": "-f", "argument": "mp4"},
                ]
            }
        ],
    )
    assert compose_problem(body) is None


@pytest.fixture
def client(monkeypatch):
    import app as app_module

    monkeypatch.delenv("REDIS_URL", raising=False)
    application = app_module.create_app()
    application.testing = True
    return application.test_client()


def test_the_route_refuses_before_downloading_anything(client, monkeypatch):
    called = []
    monkeypatch.setattr(ffmpeg_compose, "download_file", lambda *a, **k: called.append(a))
    res = client.post(
        "/v1/ffmpeg/compose",
        json=request(filters=[{"filter": "[0:v]drawtext=textfile=/proc/1/environ[v]"}]),
        headers={"X-API-Key": API_KEY},
    )
    assert res.status_code == 400
    assert "file" in res.get_json()["message"]
    assert called == []


def test_the_job_refuses_too(monkeypatch):
    monkeypatch.setattr(ffmpeg_compose, "download_file", lambda *a, **k: pytest.fail("downloaded"))
    with pytest.raises(ValueError):
        ffmpeg_compose.process_ffmpeg_compose(
            request(filters=[{"filter": "movie=/etc/passwd"}]), "job-1"
        )


def test_ffmpeg_runs_without_the_servers_keys(monkeypatch):
    monkeypatch.setenv("API_KEY", "secret")
    monkeypatch.setenv("S3_SECRET_KEY", "secret")
    env = ffmpeg_compose.ffmpeg_env()
    assert "API_KEY" not in env and "S3_SECRET_KEY" not in env
    assert env.get("PATH") == os.environ["PATH"]


def test_a_wrong_key_is_refused(client):
    res = client.post("/v1/ffmpeg/compose", json=request(), headers={"X-API-Key": API_KEY + "x"})
    assert res.status_code == 401
    assert client.post("/v1/ffmpeg/compose", json=request()).status_code == 401
