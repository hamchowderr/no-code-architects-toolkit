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

"""Hand-drawn outlines around the speaker ("sketch" outlines).

A thin line traced a little way outside the speaker's mask, wobbled like a pen line and
redrawn a few times a second ("boiling"), the way animators make drawings look alive. It
draws itself on at the start and is written as a see-through video that FFmpeg lays
between the background and the person.
"""

import subprocess

import numpy as np

BOILS_PER_SECOND = 8  # how often the line is redrawn with a new wobble
DRAW_ON = 0.4  # seconds the line takes to draw itself
FADE = 0.2  # seconds it takes to fade at the end


def _mask_frames(mask_path, start, end, width, height, fps):
    """The mask's frames from start to end at the given size, as booleans."""
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-ss", f"{start:.3f}", "-t", f"{end - start:.3f}", "-i", mask_path,
         "-vf", f"fps={fps:.6f},scale={width}:{height},format=gray", "-f", "rawvideo", "-"],
        stdout=subprocess.PIPE,
    )
    size = width * height
    try:
        while True:
            raw = proc.stdout.read(size)
            if len(raw) < size:
                break
            yield np.frombuffer(raw, np.uint8).reshape(height, width) > 127
    finally:
        proc.stdout.close()
        proc.wait()


def _chaikin(points, rounds=2):
    """Rounds a closed polygon's corners."""
    for _ in range(rounds):
        nxt = np.roll(points, -1, axis=0)
        points = np.concatenate([0.75 * points + 0.25 * nxt, 0.25 * points + 0.75 * nxt], axis=1).reshape(-1, 2)
    return points


def trace(mask, offset, wobble, seed):
    """The outline's points (closed polygons, in mask pixels) for one frame: the mask spread
    out by `offset` pixels, simplified, then nudged by up to `wobble` pixels."""
    import cv2

    m = mask.astype(np.uint8) * 255
    if offset >= 1:
        k = int(offset) * 2 + 1
        m = cv2.dilate(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
    contours, _ = cv2.findContours(m, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    if not contours:
        return []
    area = mask.shape[0] * mask.shape[1]
    rng = np.random.default_rng(seed)
    lines = []
    for c in sorted(contours, key=cv2.contourArea, reverse=True)[:2]:
        if cv2.contourArea(c) < area * 0.01:
            continue
        # Keep a point every ~2% of the short side, so the line bends like a hand drew it.
        step = max(2, int(min(mask.shape) * 0.02))
        pts = c[::step, 0, :].astype(np.float32)
        if len(pts) < 4:
            continue
        pts += rng.uniform(-wobble, wobble, pts.shape).astype(np.float32)
        lines.append(_chaikin(pts))
    return lines


def render(mask_path, out, start, end, width, height, fps, color=(255, 255, 255), line=0.45, offset=2.2, wobble=0.6):
    """Writes the outline from `start` to `end` seconds as a see-through MOV (qtrle, RGBA) at
    width x height. `line`, `offset` and `wobble` are percent of the short side."""
    import cv2

    short = min(width, height)
    # Traced on a smaller copy (fast), drawn at full size.
    scale = min(1.0, 540 / max(width, height))
    mw, mh = max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)
    off_px = offset / 100 * short * scale
    wob_px = wobble / 100 * short * scale
    thick = max(1, int(round(line / 100 * short)))
    r, g, b = color

    enc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgba", "-s", f"{width}x{height}",
         "-r", f"{fps:.6f}", "-i", "-", "-c:v", "qtrle", out],
        stdin=subprocess.PIPE,
    )
    length = end - start
    frames = 0
    lines = []
    boil = -1
    try:
        for i, mask in enumerate(_mask_frames(mask_path, start, end, mw, mh, fps)):
            t = i / fps
            step = int(t * BOILS_PER_SECOND)
            if step != boil:  # a new wobble a few times a second, not every frame
                boil = step
                lines = [p / scale for p in trace(mask, off_px, wob_px, seed=step)]
            # Colour everywhere, the line only in the alpha, so its soft edges stay the colour.
            canvas = np.empty((height, width, 4), np.uint8)
            canvas[..., 0], canvas[..., 1], canvas[..., 2] = r, g, b
            ink = np.zeros((height, width), np.uint8)
            drawn = min(1.0, t / DRAW_ON) if DRAW_ON else 1.0
            alpha = max(0.0, min(1.0, (length - t) / FADE)) if FADE else 1.0
            for pts in lines:
                n = max(2, int(len(pts) * drawn))
                part = np.round(pts[:n] * 16).astype(np.int32)  # 4 bits of sub-pixel
                cv2.polylines(ink, [part], drawn >= 1.0, int(255 * alpha), thick, cv2.LINE_AA, shift=4)
            canvas[..., 3] = ink
            enc.stdin.write(canvas.tobytes())
            frames += 1
    finally:
        enc.stdin.close()
        enc.wait()
    if enc.returncode != 0 or frames == 0:
        raise RuntimeError("Could not draw the sketch outline.")
    return frames
