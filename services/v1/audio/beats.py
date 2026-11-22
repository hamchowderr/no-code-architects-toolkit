# Copyright (c) 2026 Otaku Solutions
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License along
# with this program; if not, write to the Free Software Foundation, Inc.,
# 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301 USA.

"""
Where the beats of a song fall, found with plain signal processing and no AI, so an edit
can cut on them.

1. FFmpeg decodes the sound to one channel.
2. How suddenly the sound gets louder, frame by frame (spectral flux), gives an onset
   strength curve; the same curve for the bass alone helps find where bars start.
3. The tempo is the beat length at which that curve repeats best (autocorrelation),
   leaning towards common tempos around 120 beats a minute.
4. Beats are placed by dynamic programming (Ellis, 2007): each lands on a strong onset
   while the gaps stay close to the tempo.
5. Bars start on the beat in each group of four (or three) where the bass hits hardest.
6. How loud each beat is (RMS from it to the next), in dB below the loudest beat, so an
   edit can cut faster when the music gets louder.
"""

import logging
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

SAMPLE_RATE = 22050
HOP = 512
WINDOW = 2048
FRAMES_PER_SECOND = SAMPLE_RATE / HOP

# The longest stretch read, in seconds: longer than almost any song.
DEFAULT_MAX_SECONDS = 600
MIN_BPM = 50
MAX_BPM = 210
# The tempo most music sits near, and how far (in octaves) the guess may lean from it.
CENTRE_BPM = 120
SPREAD_OCTAVES = 1.0
# How strongly beats keep an even spacing against landing on the loudest onsets.
TIGHTNESS = 100
# Frequencies up to this many hertz count as bass, where kick drums and bar starts sit.
BASS_HZ = 150
# Frames analysed at once, so a long song never needs all its spectrum in memory.
CHUNK_FRAMES = 1024
# A sound shows up most sharply a quarter window before the frame it is centred in, so
# each beat is moved this much later to land where it is heard.
HEARD_LATER = WINDOW / 4 / SAMPLE_RATE


def decode(path, max_seconds=DEFAULT_MAX_SECONDS):
    """The file's sound as one channel of floats at SAMPLE_RATE, up to `max_seconds` long."""
    out = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin", "-i", path, "-t", str(max_seconds),
            "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "f32le", "-",
        ],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        message = out.stderr.decode("utf-8", errors="ignore").strip().splitlines()
        if any("does not contain any stream" in line or "matches no streams" in line for line in message):
            raise ValueError("This file has no sound to find a beat in.")
        raise RuntimeError(message[-1] if message else "FFmpeg could not read the file.")
    samples = np.frombuffer(out.stdout, dtype=np.float32)
    if samples.size == 0:
        raise ValueError("This file has no sound to find a beat in.")
    return samples


def onset_strength(samples):
    """
    How suddenly the sound gets louder in each frame, for the whole sound and for the bass
    alone. Frames are HOP samples apart, centred on their time.
    """
    padded = np.pad(samples.astype(np.float32), WINDOW // 2)
    count = 1 + max(0, (padded.size - WINDOW) // HOP)
    window = np.hanning(WINDOW).astype(np.float32)
    bass_bins = max(2, int(round(BASS_HZ * WINDOW / SAMPLE_RATE)) + 1)

    flux = np.zeros(count, dtype=np.float64)
    bass = np.zeros(count, dtype=np.float64)
    previous = None
    for first in range(0, count, CHUNK_FRAMES):
        last = min(count, first + CHUNK_FRAMES)
        starts = np.arange(first, last) * HOP
        frames = padded[starts[:, None] + np.arange(WINDOW)[None, :]]
        spectrum = np.log1p(100.0 * np.abs(np.fft.rfft(frames * window, axis=1)))
        if previous is None:
            previous = spectrum[:1]
        rises = np.maximum(0.0, np.diff(np.vstack([previous, spectrum]), axis=0))
        flux[first:last] = rises.mean(axis=1)
        bass[first:last] = rises[:, 1:bass_bins].mean(axis=1)
        previous = spectrum[-1:]
    bass = _detrend(bass)
    # Bass counts twice: a kick drum barely moves the whole spectrum but is often the beat.
    return _detrend(flux) + bass, bass


def _detrend(curve):
    """Only the rises above the curve's own slow level count, scaled so its spread is 1."""
    width = max(1, int(round(FRAMES_PER_SECOND)))
    kernel = np.ones(width) / width
    level = np.convolve(curve, kernel, mode="same")
    rises = np.maximum(0.0, curve - level)
    spread = rises.std()
    return rises / spread if spread > 0 else rises


def tempo_of(onsets, min_bpm=MIN_BPM, max_bpm=MAX_BPM):
    """
    Beats a minute, from how the onset curve repeats, and how steadily it repeats at that
    length (0 to 1). None when nothing repeats.
    """
    if onsets.size < 4 or not onsets.any():
        return None
    # Softened first, so a beat length that falls between two frames still scores as one.
    reach = np.arange(-3, 4)
    soft = np.convolve(onsets, np.exp(-0.5 * reach ** 2), mode="same")
    centred = soft - soft.mean()
    size = 1 << int(np.ceil(np.log2(2 * centred.size)))
    spectrum = np.fft.rfft(centred, size)
    repeats = np.fft.irfft(spectrum * np.conj(spectrum), size)[: centred.size]
    if repeats[0] <= 0:
        return None
    repeats = repeats / repeats[0]

    shortest = max(1, int(np.floor(60 * FRAMES_PER_SECOND / max_bpm)))
    longest = min(centred.size - 2, int(np.ceil(60 * FRAMES_PER_SECOND / min_bpm)))
    if longest <= shortest:
        return None
    lags = np.arange(shortest, longest + 1)
    bpm = 60 * FRAMES_PER_SECOND / lags
    lean = np.exp(-0.5 * (np.log2(bpm / CENTRE_BPM) / SPREAD_OCTAVES) ** 2)
    scores = np.maximum(0.0, repeats[lags]) * lean
    best = int(np.argmax(scores))
    if scores[best] <= 0:
        return None
    steadiness = float(min(1.0, max(0.0, repeats[lags[best]])))
    lag = float(lags[best])
    # A finer beat length between frames, from the two scores either side.
    if 0 < best < len(lags) - 1:
        left, middle, right = repeats[lags[best] - 1], repeats[lags[best]], repeats[lags[best] + 1]
        bend = left - 2 * middle + right
        if bend < 0:
            lag += 0.5 * (left - right) / bend
    return 60 * FRAMES_PER_SECOND / lag, steadiness


def track_beats(onsets, bpm, tightness=TIGHTNESS):
    """The frames beats fall on, evenly spaced at about `bpm` and on strong onsets."""
    period = 60 * FRAMES_PER_SECOND / bpm
    reach = np.arange(-period, period + 1)
    smooth = np.exp(-0.5 * (reach * 32.0 / period) ** 2)
    local = np.convolve(onsets, smooth, mode="same")
    if not local.any():
        return np.array([], dtype=int)

    near = int(round(period / 2))
    far = int(round(2 * period))
    gaps = np.arange(far, near - 1, -1)  # how far back the beat before may be, far first
    cost = -tightness * np.log(gaps / period) ** 2

    count = local.size
    total = np.zeros(count)
    before = np.full(count, -1, dtype=int)
    started = False
    floor = 0.01 * local.max()
    for frame in range(count):
        lo = frame - far
        hi = frame - near
        if hi < 0:
            total[frame] = local[frame]
            continue
        offset = max(0, -lo)
        options = total[lo + offset: hi + 1] + cost[offset:]
        pick = int(np.argmax(options))
        best = options[pick]
        # Before the music starts, a beat does not need one before it.
        if not started and best <= local[frame]:
            total[frame] = local[frame]
        else:
            total[frame] = local[frame] + best
            before[frame] = lo + offset + pick
        if local[frame] > floor:
            started = True

    # The last beat is where the running score is highest: past the music's end it only falls.
    end = int(np.argmax(total))

    frames = [end]
    while before[frames[-1]] >= 0:
        frames.append(int(before[frames[-1]]))
    frames = np.array(frames[::-1], dtype=int)
    return _trim(frames, local, max(1, int(round(period / 8))))


def _trim(frames, local, near):
    """Drops weak beats at the very start and end, where the music has not begun or has ended."""
    if frames.size == 0:
        return frames
    # The strongest onset just around each beat, so a hit a frame off still counts.
    heights = np.array([local[max(0, f - near): f + near + 1].max() for f in frames])
    keep = 0.5 * np.sqrt(np.mean(heights ** 2))
    strong = np.flatnonzero(heights >= keep)
    if strong.size == 0:
        return frames
    return frames[strong[0]: strong[-1] + 1]


def bar_start(beats, bass, onsets, beats_per_bar):
    """Which beat in each group of `beats_per_bar` begins a bar: the one the bass hits hardest."""
    if beats.size < beats_per_bar * 2:
        return 0
    scores = []
    for phase in range(beats_per_bar):
        group = beats[phase::beats_per_bar]
        scores.append(bass[group].mean() + 0.25 * onsets[group].mean())
    return int(np.argmax(scores))


def beat_loudness(samples, times, beat_seconds):
    """How loud each beat is, from it to the next beat, in dB below the loudest (0 to -60)."""
    edges = np.append(times, times[-1] + beat_seconds)
    starts = np.clip((edges[:-1] * SAMPLE_RATE).astype(int), 0, samples.size)
    ends = np.clip((edges[1:] * SAMPLE_RATE).astype(int), 0, samples.size)
    levels = np.array([
        np.sqrt(np.mean(np.square(samples[a:b], dtype=np.float64))) if b > a else 0.0
        for a, b in zip(starts, ends)
    ])
    db = 20 * np.log10(np.maximum(levels, 1e-9))
    return [round(float(max(-60.0, d)), 1) for d in db - db.max()]


def find_beats(path, max_seconds=DEFAULT_MAX_SECONDS, beats_per_bar=4,
               min_bpm=MIN_BPM, max_bpm=MAX_BPM):
    """Tempo, beats, how strong each is, and where bars start, in seconds."""
    samples = decode(path, max_seconds)
    duration = round(samples.size / SAMPLE_RATE, 3)
    found = {
        "duration": duration,
        "tempo": None,
        "beat_seconds": None,
        "steadiness": 0.0,
        "beats_per_bar": beats_per_bar,
        "beats": [],
        "strengths": [],
        "downbeats": [],
        "loudness": [],
    }
    if float(np.max(np.abs(samples))) < 1e-4:
        return found  # silent: no beat to find

    onsets, bass = onset_strength(samples)
    guess = tempo_of(onsets, min_bpm, max_bpm)
    if guess is None:
        return found
    bpm, steadiness = guess
    frames = track_beats(onsets, bpm)
    if frames.size < 2:
        return found

    times = frames / FRAMES_PER_SECOND + HEARD_LATER
    # The tempo the beats keep over the whole song, finer than one frame.
    beat_seconds = float((times[-1] - times[0]) / (times.size - 1))
    heights = onsets[frames]
    top = heights.max() or 1.0
    phase = bar_start(frames, bass, onsets, beats_per_bar)
    found.update({
        "tempo": round(60.0 / beat_seconds, 2),
        "beat_seconds": round(beat_seconds, 4),
        "steadiness": round(steadiness, 3),
        "beats": [round(float(t), 3) for t in times],
        "strengths": [round(float(h / top), 3) for h in heights],
        "downbeats": [round(float(t), 3) for t in times[phase::beats_per_bar]],
        "loudness": beat_loudness(samples, times, beat_seconds),
    })
    logger.info(f"Found {len(frames)} beats at {found['tempo']} BPM in {duration} s")
    return found
