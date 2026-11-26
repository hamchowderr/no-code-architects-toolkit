"""
/v1/audio/align: where a separate mic recording sits against the camera's own sound,
found from the sound alone. Each test makes its own recordings from one "performance",
so the true offset and clock drift are known exactly.
"""

import subprocess
import threading
import wave
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

import numpy as np
import pytest

from services.v1.audio import align as al

from conftest import API_KEY

RATE = 48000


def talking(seconds, seed=0):
    """Something like a person talking: voiced syllables at uneven pitches and gaps."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * RATE))
    at = 0.3
    while at < seconds - 0.5:
        n = int(rng.uniform(0.08, 0.35) * RATE)
        t = np.arange(n) / RATE
        pitch = rng.uniform(100, 240)
        voice = sum(np.sin(2 * np.pi * pitch * k * t) / k for k in range(1, 8))
        voice += 0.3 * rng.standard_normal(n)
        i = int(at * RATE)
        out[i:i + n] += 0.2 * voice * np.hanning(n)
        at += n / RATE + rng.uniform(0.05, 0.6)
    return out


def at_speed(samples, speed):
    """The same sound recorded by a clock running `speed` times as fast."""
    times = np.arange(int(samples.size * speed)) / speed
    return np.interp(times, np.arange(samples.size), samples)


def camera_mic(performance, seed=1):
    """A camera's own mic: dull, with an echo and the room's hum and hiss."""
    rng = np.random.default_rng(seed)
    dull = np.convolve(performance, np.ones(12) / 12, mode="same")
    echo = np.zeros_like(dull)
    echo[int(0.03 * RATE):] = 0.4 * dull[: -int(0.03 * RATE)]
    t = np.arange(dull.size) / RATE
    return 0.5 * (dull + echo) + 0.01 * np.sin(2 * np.pi * 60 * t) + 0.01 * rng.standard_normal(dull.size)


def lav_mic(performance, seed=2):
    """A lav mic: bright and close, with a little hiss."""
    rng = np.random.default_rng(seed)
    bright = performance - 0.6 * np.concatenate([[0], performance[:-1]])
    return bright + 0.003 * rng.standard_normal(bright.size)


def write_wav(path, samples):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes((np.clip(samples, -1, 1) * 32767).astype(np.int16).tobytes())


def take(folder, name, seconds=40, mic_early=0.0, camera_early=0.0, speed=1.0, seed=0):
    """
    One performance recorded twice: the mic starts `mic_early` seconds before the camera (or
    `camera_early` after), and its clock runs `speed` times as fast. Returns the two paths.
    """
    performance = talking(seconds + 10, seed)
    cam = camera_mic(performance[int((5 + mic_early) * RATE): int((5 + mic_early + seconds) * RATE)])
    mic_start = int((5 + camera_early) * RATE)
    mic = lav_mic(at_speed(performance[mic_start: mic_start + int((seconds + 4) * RATE)], speed))
    write_wav(folder / f"{name}-camera.wav", cam)
    write_wav(folder / f"{name}-mic.wav", mic)
    return str(folder / f"{name}-camera.wav"), str(folder / f"{name}-mic.wav")


def test_mic_started_first(tmp_path):
    camera, mic = take(tmp_path, "early", mic_early=3.217)
    found = al.align(camera, mic)
    assert found["matched"]
    assert found["offset"] == pytest.approx(3.217, abs=0.001)
    assert abs(found["drift"]) < 2e-5
    assert found["confidence"] > 0.5
    assert found["overlap"] == pytest.approx(40, abs=0.1)


def test_mic_started_after_the_camera(tmp_path):
    camera, mic = take(tmp_path, "late", camera_early=1.5)
    found = al.align(camera, mic)
    assert found["matched"]
    assert found["offset"] == pytest.approx(-1.5, abs=0.001)


def test_clock_drift_over_a_long_take(tmp_path):
    # 50 parts per million: about 7 ms out by the end of two and a half minutes.
    camera, mic = take(tmp_path, "drift", seconds=150, mic_early=2.0, speed=1.00005, seed=4)
    found = al.align(camera, mic)
    assert found["matched"]
    assert found["drift"] == pytest.approx(5e-5, abs=1e-5)
    # Where the camera's last sound is heard on the mic, to within a millisecond.
    end = 140.0
    assert end * (1 + found["drift"]) + found["offset"] == pytest.approx(2.0 * 1.00005 + end * 1.00005, abs=0.001)


def test_different_recordings_do_not_match(tmp_path):
    a, _ = take(tmp_path, "one", seed=7)
    _, b = take(tmp_path, "two", seed=8)
    found = al.align(a, b)
    assert not found["matched"] and found["offset"] is None
    assert found["confidence"] < al.MATCH_CONFIDENCE


def test_reads_the_sound_of_a_video(tmp_path):
    camera, mic = take(tmp_path, "video", mic_early=0.75)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=40",
                    "-i", camera, "-shortest", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
                    str(tmp_path / "camera.mp4")], check=True)
    found = al.align(str(tmp_path / "camera.mp4"), mic)
    assert found["matched"]
    # AAC adds a little delay at the start; still well within a frame.
    assert found["offset"] == pytest.approx(0.75, abs=0.025)


def test_no_sound_at_all_says_so(tmp_path):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=gray:s=320x180:r=30:d=1",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(tmp_path / "mute.mp4")], check=True)
    _, mic = take(tmp_path, "x")
    with pytest.raises(ValueError, match="no sound"):
        al.align(str(tmp_path / "mute.mp4"), mic)


@pytest.fixture
def served(tmp_path):
    take(tmp_path, "served", mic_early=1.25)
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


def test_endpoint(client, served):
    res = client.post("/v1/audio/align", json={"reference_url": f"{served}/served-camera.wav",
                                               "media_url": f"{served}/served-mic.wav"},
                      headers={"X-API-Key": API_KEY})
    assert res.status_code == 200, res.get_json()
    found = res.get_json()["response"]
    assert found["matched"] and found["offset"] == pytest.approx(1.25, abs=0.001)


def test_endpoint_checks_its_settings(client, served):
    for body in ({}, {"media_url": f"{served}/served-mic.wav"},
                 {"reference_url": f"{served}/served-camera.wav", "media_url": f"{served}/served-mic.wav", "max_shift": 0}):
        res = client.post("/v1/audio/align", json=body, headers={"X-API-Key": API_KEY})
        assert res.status_code == 400, body
