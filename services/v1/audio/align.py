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
Where one recording sits against another of the same moment, such as a separate mic
recording against the camera's own sound, found from the sound alone with no AI and no
clapperboard (the way PluralEyes and editors' "sync by waveform" work).

1. FFmpeg decodes both to one channel.
2. Rough: how loud each 10 ms is, as a rising-and-falling curve (log energy, differenced),
   cross-correlated over every shift, so a lav mic and a camera mic that sound different
   still line up on the same words. The best shift and how far it stands above the next
   best one (the confidence) come from here.
3. Exact: around that shift, the sound itself is compared with GCC-PHAT (each frequency
   weighted alike, so the mics' different tone does not matter), to well under a
   millisecond.
4. Drift: the exact step is done again near the end of what both recorded. Two devices'
   clocks run at slightly different speeds (tens of parts per million), which adds up to
   a frame or more over a long take; the difference between the two shifts gives the speed.
"""

import logging
import subprocess

import numpy as np

logger = logging.getLogger(__name__)

# The rough pass looks at loudness every 10 ms.
ENVELOPE_RATE = 100
DECODE_RATE = 16000
# The exact pass compares this much sound, and looks this far either side of the shift it
# starts from (far enough for drift of a long take, read from the start).
FINE_SECONDS = 20.0
FINE_REACH = 0.25
# How much of each file is read, in seconds. A take longer than this lines up from its start.
DEFAULT_MAX_SECONDS = 1800
# The furthest apart the two may have started, in seconds.
DEFAULT_MAX_SHIFT = 600
# Below this the two recordings do not share enough sound to be lined up with any trust.
MATCH_CONFIDENCE = 0.25
# Clocks of two devices differ by well under this (0.1 %); more means a misread, not drift.
MAX_DRIFT = 0.001
# Drift is only measured when both recorded at least this long together.
DRIFT_SPAN = 60.0


def decode(path, max_seconds=DEFAULT_MAX_SECONDS):
    """The file's sound as one channel of floats at DECODE_RATE, up to `max_seconds` long."""
    out = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-nostdin", "-i", path, "-t", str(max_seconds),
            "-vn", "-ac", "1", "-ar", str(DECODE_RATE), "-f", "f32le", "-",
        ],
        capture_output=True,
        check=False,
    )
    if out.returncode != 0:
        message = out.stderr.decode("utf-8", errors="ignore").strip().splitlines()
        if any("does not contain any stream" in line or "matches no streams" in line for line in message):
            raise ValueError("One of the files has no sound to line up by.")
        raise RuntimeError(message[-1] if message else "FFmpeg could not read the file.")
    samples = np.frombuffer(out.stdout, dtype=np.float32)
    if samples.size == 0:
        raise ValueError("One of the files has no sound to line up by.")
    return samples


def envelope(samples):
    """How the loudness changes, every 10 ms: log energy, differenced, zero mean, unit spread."""
    hop = DECODE_RATE // ENVELOPE_RATE
    frames = samples[: samples.size // hop * hop].reshape(-1, hop).astype(np.float64)
    energy = np.log10(np.mean(frames ** 2, axis=1) + 1e-10)
    rise = np.diff(energy, prepend=energy[:1])
    rise = rise - rise.mean()
    spread = rise.std()
    return rise / spread if spread > 0 else rise


def cross_correlate(a, b):
    """Correlation of b against a at every shift: index k is b shifted k - (len(a) - 1)."""
    size = 1 << int(np.ceil(np.log2(a.size + b.size - 1)))
    spectrum = np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size))
    full = np.fft.irfft(spectrum, size)
    # Shifts from -(len(a) - 1) to len(b) - 1, in order.
    return np.concatenate([full[size - (a.size - 1):], full[: b.size]])


def rough_shift(reference, other, max_shift):
    """
    The shift, in seconds, at which `other` best matches `reference` (other's time minus
    reference's time for the same sound), and how far it stands above any other shift.
    """
    a, b = envelope(reference), envelope(other)
    if a.size < ENVELOPE_RATE or b.size < ENVELOPE_RATE:
        return None, 0.0
    corr = cross_correlate(a, b)
    shifts = np.arange(-(a.size - 1), b.size)
    # Each shift scored by how much sound overlaps there, so a sliver of overlap at the
    # far ends never wins on a lucky match.
    overlap = np.minimum(a.size, shifts + a.size) - np.maximum(0, shifts)
    overlap = np.minimum(overlap, b.size - np.maximum(0, shifts))
    usable = (np.abs(shifts) <= max_shift * ENVELOPE_RATE) & (overlap >= min(a.size, b.size, 10 * ENVELOPE_RATE) * 0.5)
    if not usable.any():
        return None, 0.0
    score = np.where(usable, corr / np.sqrt(np.maximum(overlap, 1)), -np.inf)
    best = int(np.argmax(score))
    peak = score[best]
    if not np.isfinite(peak) or peak <= 0:
        return None, 0.0
    # The best score more than half a second away from the winner.
    away = np.abs(shifts - shifts[best]) > ENVELOPE_RATE // 2
    rest = score[usable & away]
    second = max(float(rest.max()), 0.0) if rest.size else 0.0
    return shifts[best] / ENVELOPE_RATE, float(1 - second / peak)


def gcc_phat(a, b, reach):
    """The shift of b against a in seconds, within +-reach of zero, with each frequency weighted alike."""
    size = 1 << int(np.ceil(np.log2(a.size + b.size)))
    spectrum = np.fft.rfft(b, size) * np.conj(np.fft.rfft(a, size))
    spectrum /= np.abs(spectrum) + 1e-12
    full = np.fft.irfft(spectrum, size)
    steps = int(reach * DECODE_RATE)
    window = np.concatenate([full[-steps:], full[: steps + 1]])
    best = int(np.argmax(window))
    # Between samples: a parabola through the peak and its neighbours.
    if 0 < best < window.size - 1:
        left, mid, right = window[best - 1], window[best], window[best + 1]
        bend = left - 2 * mid + right
        nudge = 0.5 * (left - right) / bend if bend != 0 else 0.0
    else:
        nudge = 0.0
    return (best - steps + nudge) / DECODE_RATE


def loudest_stretch(samples, start, end, length):
    """Where, between `start` and `end` seconds, the `length` seconds with the most sound begin."""
    if end - start <= length:
        return start
    hop = DECODE_RATE // ENVELOPE_RATE
    lo, hi = int(start * DECODE_RATE), int(end * DECODE_RATE)
    piece = samples[lo:hi]
    frames = piece[: piece.size // hop * hop].reshape(-1, hop)
    energy = np.mean(frames.astype(np.float64) ** 2, axis=1)
    span = int(length * ENVELOPE_RATE)
    running = np.convolve(energy, np.ones(span), mode="valid")
    return start + int(np.argmax(running)) / ENVELOPE_RATE


def exact_shift(reference, other, at, rough):
    """The exact shift near reference time `at`, starting from the rough one."""
    length = min(FINE_SECONDS, reference.size / DECODE_RATE - at)
    a0 = int(at * DECODE_RATE)
    a = reference[a0 : a0 + int(length * DECODE_RATE)]
    b0 = int(round((at + rough) * DECODE_RATE))
    lo = max(0, b0)
    b = other[lo : lo + a.size]
    if a.size < DECODE_RATE or b.size < DECODE_RATE:
        return None
    # A shift where `other` would start before its own first sample is counted back in.
    return rough + (lo - b0) / DECODE_RATE + gcc_phat(a, b, FINE_REACH)


def align(reference_path, other_path, max_seconds=DEFAULT_MAX_SECONDS, max_shift=DEFAULT_MAX_SHIFT):
    """
    How `other` lines up with `reference`: other's time = reference's time * (1 + drift) +
    offset. `offset` is in seconds (positive when the other recording started first), and
    `matched` says whether the two share enough sound to trust it.
    """
    reference = decode(reference_path, max_seconds)
    other = decode(other_path, max_seconds)
    ref_length = reference.size / DECODE_RATE
    other_length = other.size / DECODE_RATE
    rough, confidence = rough_shift(reference, other, max_shift)
    answer = {
        "matched": False,
        "offset": None,
        "drift": 0.0,
        "confidence": round(confidence, 3),
        "overlap": 0.0,
        "reference_duration": round(ref_length, 3),
        "other_duration": round(other_length, 3),
    }
    if rough is None or confidence < MATCH_CONFIDENCE:
        return answer
    # Reference times both recorded, from `first` to `last`.
    first = max(0.0, -rough)
    last = min(ref_length, other_length - rough)
    overlap = max(0.0, last - first)
    answer["overlap"] = round(overlap, 3)
    start_at = loudest_stretch(reference, first, min(last, first + 120), FINE_SECONDS)
    early = exact_shift(reference, other, start_at, rough)
    if early is None:
        early = rough
    offset, drift = early, 0.0
    if overlap >= DRIFT_SPAN + FINE_SECONDS:
        end_at = loudest_stretch(reference, max(start_at + DRIFT_SPAN, last - 120), last, FINE_SECONDS)
        late = exact_shift(reference, other, end_at, early)
        if late is not None and end_at > start_at:
            measured = (late - early) / (end_at - start_at)
            if abs(measured) <= MAX_DRIFT:
                drift = measured
                offset = early - drift * start_at
    answer.update(matched=True, offset=round(offset, 5), drift=round(drift, 8))
    return answer
