"""
/v1/audio/beats: a song's tempo, beats and bar starts, found without AI. Each test makes
its own track, so where every beat falls is known exactly.
"""

import os
import subprocess
import threading
import wave
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from services.v1.audio import beats as bt

from conftest import API_KEY

RATE = 22050


def drum_track(bpm, seconds=20.0, start=0.25, per_bar=4, chords=True, seed=0):
    """A kick on the first beat of each bar and a hi-hat on the others, over held chords."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * RATE), dtype=np.float32)
    t = np.arange(int(0.12 * RATE)) / RATE
    kick = (0.9 * np.sin(2 * np.pi * 55 * t) * np.exp(-t * 25)).astype(np.float32)
    h = np.arange(int(0.03 * RATE)) / RATE
    hat = (0.3 * rng.standard_normal(h.size) * np.exp(-h * 150)).astype(np.float32)
    beats = []
    at = start
    while at < seconds - 0.2:
        i = int(at * RATE)
        hit = kick if len(beats) % per_bar == 0 else hat
        out[i:i + hit.size] += hit[: out.size - i]
        beats.append(at)
        at += 60.0 / bpm
    if chords:
        tt = np.arange(out.size) / RATE
        for f in (220, 277.2, 329.6):
            out += (0.03 * np.sin(2 * np.pi * f * tt)).astype(np.float32)
    return out, np.array(beats)


def write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def ffmpeg(*args):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", *args], check=True)


@pytest.fixture(scope="module")
def songs(tmp_path_factory):
    folder = tmp_path_factory.mktemp("beats")
    made = {}
    for bpm in (72, 95, 120, 140, 175):
        samples, beats = drum_track(bpm)
        write_wav(folder / f"{bpm}.wav", samples)
        made[bpm] = (folder / f"{bpm}.wav", beats)
    waltz, beats = drum_track(90, per_bar=3)
    write_wav(folder / "waltz.wav", waltz)
    made["waltz"] = (folder / "waltz.wav", beats)
    write_wav(folder / "silence.wav", np.zeros(RATE * 3, dtype=np.float32))
    # A song as an MP3 inside a video, the way a clip with music arrives.
    ffmpeg("-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=20", "-i", str(folder / "120.wav"),
           "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "libmp3lame", str(folder / "clip.mp4"))
    ffmpeg("-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=1", "-c:v", "libx264", "-pix_fmt", "yuv420p",
           str(folder / "mute.mp4"))
    made["folder"] = folder
    return made


def misses(found, truth):
    got = np.array(found["beats"])
    return np.array([np.min(np.abs(got - beat)) for beat in truth])


@pytest.mark.parametrize("bpm", [72, 95, 120, 140, 175])
def test_finds_the_tempo_and_every_beat(songs, bpm):
    path, truth = songs[bpm]
    found = bt.find_beats(str(path))
    assert abs(found["tempo"] - bpm) < bpm * 0.01
    assert abs(found["beat_seconds"] - 60 / bpm) < 0.01
    assert len(found["beats"]) == len(truth)
    assert np.median(misses(found, truth)) < 0.02
    assert misses(found, truth).max() < 0.05
    assert found["steadiness"] > 0.5
    assert len(found["strengths"]) == len(found["beats"])
    assert all(0 <= s <= 1 for s in found["strengths"])


@pytest.mark.parametrize("bpm", [95, 120, 140])
def test_bars_start_on_the_kick(songs, bpm):
    path, truth = songs[bpm]
    found = bt.find_beats(str(path))
    bars = np.array(found["downbeats"])
    for start in truth[::4]:
        assert np.min(np.abs(bars - start)) < 0.05
    assert len(bars) == len(truth[::4])


def test_three_beats_to_a_bar(songs):
    path, truth = songs["waltz"]
    found = bt.find_beats(str(path), beats_per_bar=3)
    assert found["beats_per_bar"] == 3
    bars = np.array(found["downbeats"])
    for start in truth[::3]:
        assert np.min(np.abs(bars - start)) < 0.05


def test_beats_only_while_the_music_plays(tmp_path):
    song, truth = drum_track(120, seconds=12)
    quiet = np.zeros(RATE * 3, dtype=np.float32)
    write_wav(tmp_path / "gaps.wav", np.concatenate([quiet, song, quiet]))
    found = bt.find_beats(str(tmp_path / "gaps.wav"))
    assert 3.1 < found["beats"][0] < 3.4 and found["beats"][-1] < 15.1
    assert abs(len(found["beats"]) - len(truth)) <= 1


def test_reads_the_sound_of_a_video(songs):
    _, truth = songs[120]
    found = bt.find_beats(str(songs["folder"] / "clip.mp4"))
    assert abs(found["tempo"] - 120) < 1.5
    # MP3 adds a little silence at the start; beats still land within a frame or two.
    assert np.median(misses(found, truth)) < 0.04


def test_max_seconds_reads_only_the_start(songs):
    path, _ = songs[120]
    found = bt.find_beats(str(path), max_seconds=8)
    assert found["duration"] == pytest.approx(8, abs=0.05)
    assert max(found["beats"]) < 8


def test_silence_has_no_beat(songs):
    found = bt.find_beats(str(songs["folder"] / "silence.wav"))
    assert found["tempo"] is None and found["beats"] == [] and found["downbeats"] == []


def test_no_sound_at_all_says_so(songs):
    with pytest.raises(ValueError, match="no sound"):
        bt.find_beats(str(songs["folder"] / "mute.mp4"))


def test_speech_is_not_steady():
    rng = np.random.default_rng(3)
    # Syllable-like bursts at uneven gaps, as in someone talking.
    out = np.zeros(RATE * 12, dtype=np.float32)
    at = 0.2
    while at < 11.5:
        n = int(rng.uniform(0.08, 0.3) * RATE)
        t = np.arange(n) / RATE
        i = int(at * RATE)
        out[i:i + n] += (0.3 * np.sin(2 * np.pi * rng.uniform(120, 220) * t) * np.hanning(n)).astype(np.float32)
        at += rng.uniform(0.12, 0.7)
    onsets, _ = bt.onset_strength(out)
    guess = bt.tempo_of(onsets)
    assert guess is None or guess[1] < 0.5


@pytest.fixture
def file_server(songs):
    handler = partial(SimpleHTTPRequestHandler, directory=str(songs["folder"]))
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
    res = client.post("/v1/audio/beats", json={"media_url": f"{file_server}/95.wav"},
                      headers={"X-API-Key": API_KEY})
    assert res.status_code == 200, res.get_json()
    found = res.get_json()["response"]
    assert abs(found["tempo"] - 95) < 1 and len(found["downbeats"]) >= 7


def test_endpoint_without_sound_is_a_bad_request(client, file_server):
    res = client.post("/v1/audio/beats", json={"media_url": f"{file_server}/mute.mp4"},
                      headers={"X-API-Key": API_KEY})
    assert res.status_code == 400


def test_endpoint_checks_its_settings(client, file_server):
    for body in ({}, {"media_url": f"{file_server}/95.wav", "beats_per_bar": 5},
                 {"media_url": f"{file_server}/95.wav", "min_bpm": 150, "max_bpm": 100}):
        res = client.post("/v1/audio/beats", json=body, headers={"X-API-Key": API_KEY})
        assert res.status_code == 400, body
