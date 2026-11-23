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

"""Cuts the speaker out of a video, with no green screen.

make_mask() writes a "mask" video the same length and frame rate as the source: white
where the person is, black elsewhere, soft at the edge. It is made once per video and
kept, so every effect that needs the person (words behind them, a sticker outline, a new
background) reuses it instead of finding the person again.

The person is found by MediaPipe's Selfie Segmenter (Apache-2.0, models/cutout), run on
every frame at 256 x 256 with LiteRT. FFmpeg decodes the frames (it turns phone videos
the right way up) and encodes the mask.

replace_background() lays the cut-out person over a picture, a video, a colour or a
blurred copy of their own background.
"""

import json
import logging
import os
import subprocess
import time

import numpy as np

from services.cpu_limits import available_cpus

logger = logging.getLogger(__name__)

MODEL = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "models", "cutout", "selfie_segmenter.tflite",
)
SIZE = 256  # the model's input and output side
MASK_MAX_SIDE = 960  # the mask is stored no larger than this; FFmpeg scales it up to the video
SMOOTH = 0.4  # how much of the previous frame's mask carries into this one (stops flicker)
LOW, HIGH = 0.3, 0.7  # model confidence below LOW is background, above HIGH is the person
MAX_SECONDS = 3 * 3600


class CutoutError(Exception):
    """A request that cannot be done as asked. The message is shown to the person."""


def probe(path):
    """Upright width and height, frame rate, length and whether there is sound."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height,avg_frame_rate,r_frame_rate:stream_side_data=rotation:stream_tags=rotate:format=duration",
         "-of", "json", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise CutoutError("This file could not be read as a video.")
    info = json.loads(out.stdout or "{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise CutoutError("This file has no picture to cut the speaker out of.")
    width, height = int(video["width"]), int(video["height"])
    rotation = 0
    for side in video.get("side_data_list") or []:
        if "rotation" in side:
            rotation = int(float(side["rotation"]))
    if not rotation and (video.get("tags") or {}).get("rotate"):
        rotation = int(video["tags"]["rotate"])
    if abs(rotation) % 180 == 90:
        width, height = height, width
    fps = _rate(video.get("avg_frame_rate")) or _rate(video.get("r_frame_rate")) or 30.0
    duration = float((info.get("format") or {}).get("duration") or 0)
    if duration <= 0:
        raise CutoutError("Could not read how long this video is.")
    return {
        "width": width, "height": height, "fps": fps, "duration": duration,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def _rate(text):
    try:
        num, den = (text or "0/0").split("/")
        value = float(num) / float(den)
        return value if 1 <= value <= 240 else None
    except (ValueError, ZeroDivisionError):
        return None


def mask_size(width, height, max_side=MASK_MAX_SIDE):
    """The mask keeps the video's shape, no larger than max_side, with even sides."""
    scale = min(1.0, max_side / max(width, height))
    return max(2, int(width * scale) // 2 * 2), max(2, int(height * scale) // 2 * 2)


def soften(confidence, previous=None):
    """Model confidence (0..1, SIZE x SIZE) to a mask value, carried over from the last frame."""
    if previous is not None:
        confidence = (1 - SMOOTH) * confidence + SMOOTH * previous
    return confidence, np.clip((confidence - LOW) / (HIGH - LOW), 0.0, 1.0)


def _interpreter():
    try:
        from ai_edge_litert.interpreter import Interpreter
    except ImportError:  # pragma: no cover - the image always has it
        raise CutoutError("This toolkit cannot cut out speakers yet: LiteRT (ai-edge-litert) is not installed on it.")
    it = Interpreter(model_path=MODEL, num_threads=max(1, available_cpus()))
    it.allocate_tensors()
    return it


def make_mask(src, out):
    """Writes the speaker's mask video for `src` to `out` (H.264, grey). Returns a summary."""
    import cv2

    started = time.time()
    info = probe(src)
    if info["duration"] > MAX_SECONDS:
        raise CutoutError("Videos longer than 3 hours cannot be cut out.")
    mw, mh = mask_size(info["width"], info["height"])
    fps = info["fps"]

    it = _interpreter()
    inp, outp = it.get_input_details()[0], it.get_output_details()[0]

    # Frames come out upright (FFmpeg applies the phone's rotation) and already 256 x 256.
    decode = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", src, "-map", "0:v:0", "-vf", f"fps={fps:.6f},scale={SIZE}:{SIZE}:flags=area",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    encode = subprocess.Popen(
        ["ffmpeg", "-y", "-v", "error", "-f", "rawvideo", "-pix_fmt", "gray", "-s", f"{mw}x{mh}", "-r", f"{fps:.6f}",
         "-i", "-", "-c:v", "libx264", "-preset", "veryfast", "-crf", "16", "-pix_fmt", "yuv420p",
         "-movflags", "+faststart", out],
        stdin=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    frames = 0
    covered = 0.0
    previous = None
    frame_bytes = SIZE * SIZE * 3
    try:
        while True:
            raw = decode.stdout.read(frame_bytes)
            if len(raw) < frame_bytes:
                break
            x = np.frombuffer(raw, np.uint8).reshape(1, SIZE, SIZE, 3).astype(np.float32) / 255.0
            it.set_tensor(inp["index"], x)
            it.invoke()
            previous, mask = soften(it.get_tensor(outp["index"])[0, :, :, 0], previous)
            big = cv2.GaussianBlur(cv2.resize(mask, (mw, mh), interpolation=cv2.INTER_LINEAR), (5, 5), 0)
            encode.stdin.write((big * 255).astype(np.uint8).tobytes())
            covered += float(mask.mean())
            frames += 1
    finally:
        decode.stdout.close()
        decode.wait()
        encode.stdin.close()
        encode.wait()
    if decode.returncode != 0 or encode.returncode != 0 or frames == 0:
        logger.error("cutout ffmpeg failed: %s | %s", decode.stderr.read()[-2000:], encode.stderr.read()[-2000:])
        raise RuntimeError("FFmpeg could not read the video or write its mask.")

    seconds = time.time() - started
    logger.info("Cut-out mask: %d frames (%.1f s of video) in %.1f s", frames, info["duration"], seconds)
    return {
        "frames": frames,
        "fps": round(fps, 3),
        "width": mw,
        "height": mh,
        "duration": round(info["duration"], 3),
        # Share of the picture the person fills, on average. Near 0 means nobody was found.
        "person_share": round(covered / frames, 3),
        "seconds": round(seconds, 1),
    }


def head_top(mask_path, at, width, height):
    """Where the top of the person's head is at `at` seconds, as (x, y) in percent of the
    frame, or None when nobody is in the shot."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-ss", f"{max(0.0, at):.3f}", "-i", mask_path, "-frames:v", "1",
         "-vf", "scale=100:100,format=gray", "-f", "rawvideo", "-"],
        capture_output=True,
    ).stdout
    if len(raw) < 100 * 100:
        return None
    m = np.frombuffer(raw[: 100 * 100], np.uint8).reshape(100, 100) > 127
    rows = np.where(m.any(axis=1))[0]
    if not len(rows):
        return None
    top = int(rows[0])
    cols = np.where(m[min(99, top + 3)])[0]
    return (float(cols.mean()) if len(cols) else 50.0, float(top))


def background_graph(width, height, kind, blur=24):
    """Filter that lays the person (input 0, with mask input 1) over a new background.

    kind: "image" or "video" (input 2, scaled to cover the frame), "color:#rrggbb", or "blur"
    (the video's own background, blurred). Returns the graph; its output is [vout].
    """
    w, h = width - width % 2, height - height % 2
    parts = [f"[0:v]scale={w}:{h},setsar=1,split=2[fg0][bg0]",
             f"[1:v]format=gray,scale={w}:{h}:flags=bicubic,setsar=1[m]",
             "[fg0][m]alphamerge[person]"]
    if kind == "blur":
        parts.append(f"[bg0]gblur=sigma={blur}[bg]")
    elif kind.startswith("color:"):
        parts.append(f"[bg0]drawbox=c={kind[6:].replace('#', '0x')}:t=fill[bg]")
    else:
        parts.append("[bg0]nullsink")
        parts.append(f"[2:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},setsar=1,format=yuv420p[bg]")
    parts.append("[bg][person]overlay=format=auto:shortest=1,format=yuv420p[vout]")
    return ";".join(parts)


def replace_background(src, mask, out, background=None, kind="blur", blur=24):
    """Lays the person from `src` (cut out by `mask`) over the background and writes `out`."""
    started = time.time()
    info = probe(src)
    inputs = ["-i", src, "-i", mask]
    if kind in ("image", "video"):
        if kind == "image":
            inputs += ["-loop", "1", "-framerate", f"{info['fps']:.6f}", "-i", background]
        else:
            inputs += ["-stream_loop", "-1", "-i", background]
    graph = background_graph(info["width"], info["height"], kind, blur)
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs, "-filter_complex", graph, "-map", "[vout]"]
    if info["has_audio"]:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p",
            "-t", f"{info['duration']:.3f}", "-movflags", "+faststart", out]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg background failed: %s", result.stderr[-3000:])
        raise RuntimeError("FFmpeg could not put the new background behind the speaker.")
    return {"duration": round(info["duration"], 3), "seconds": round(time.time() - started, 1)}
