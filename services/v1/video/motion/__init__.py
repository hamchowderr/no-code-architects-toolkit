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

"""Lay motion pieces (lower thirds, titles, stat cards...) and sound effects over a video.

1. Every piece is laid end to end on one see-through "sheet" (pieces.py), rendered once
   with HyperFrames (Apache-2.0, open source) to a ProRes 4444 file with alpha.
2. FFmpeg cuts each piece back out of the sheet, shifts it to its time in the video and
   lays it on top. Only the seconds that have a piece are drawn in the browser, so a long
   video costs no more than a short one with the same pieces.
3. Sound effects (sounds/, CC0) are mixed under the video's own sound at their times.

A request with no pieces only mixes in sounds, and a request with no sounds keeps the
video's sound untouched.
"""

import glob
import json
import logging
import os
import shutil
import subprocess
import time

from .pieces import PIECES, sheet_html

logger = logging.getLogger(__name__)

SOUND_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sounds")
SOUNDS = {
    os.path.splitext(name)[0]: os.path.join(SOUND_DIR, name)
    for name in sorted(os.listdir(SOUND_DIR))
    if name.endswith((".mp3", ".wav", ".ogg"))
}

HYPERFRAMES = os.environ.get("HYPERFRAMES_BIN", "hyperframes")
RENDER_TIMEOUT = int(os.environ.get("MOTION_RENDER_TIMEOUT", "1800"))
MAX_PIECES = 120
MAX_SOUNDS = 400
FPS = 30


class MotionError(Exception):
    """A request that cannot be done as asked. The message is shown to the person."""


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height,r_frame_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True, check=True,
    ).stdout
    info = json.loads(out)
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise MotionError("This file has no picture to put motion graphics on.")
    has_audio = any(s.get("codec_type") == "audio" for s in info.get("streams", []))
    duration = float(info.get("format", {}).get("duration") or 0)
    if duration <= 0:
        raise MotionError("Could not read how long this video is.")
    return {"width": int(video["width"]), "height": int(video["height"]), "duration": duration, "has_audio": has_audio}


def plan(pieces, sounds, duration, sound_volume=1.0, piece_sounds=True):
    """Checks and tidies the request. Returns (pieces, sound cues), both sorted by time.

    A piece keeps inside the video, gets its type's default length when none is given and is
    clamped to that type's shortest and longest. A piece brings its default sound unless it
    says otherwise ("sound": null for none).
    """
    if len(pieces) > MAX_PIECES:
        raise MotionError(f"At most {MAX_PIECES} motion pieces can go on one video.")
    if len(sounds) > MAX_SOUNDS:
        raise MotionError(f"At most {MAX_SOUNDS} sound effects can go on one video.")

    placed = []
    cues = []
    for raw in pieces:
        kind = raw["type"]
        default, default_sound, shortest, longest = PIECES[kind]
        start = max(0.0, float(raw.get("start", 0)))
        if start >= duration:
            continue  # past the end of the video
        length = float(raw.get("duration") or default)
        length = min(max(shortest, min(longest, length)), duration - start)
        if length < 0.2:
            continue
        if kind == "logo" and not raw.get("image"):
            raise MotionError("A logo piece needs the logo's link.")
        if kind in ("lower_third", "title", "keyword", "stat", "chapter", "follow", "quote") and not str(raw.get("title") or "").strip():
            raise MotionError(f"A {kind.replace('_', ' ')} piece needs its words.")
        piece = dict(raw, start=round(start, 3), duration=round(length, 3))
        placed.append(piece)
        sound = raw.get("sound", default_sound) if piece_sounds else raw.get("sound")
        if sound:
            cues.append({"sound": sound, "at": start, "volume": 1.0})

    for raw in sounds:
        at = float(raw.get("at", 0))
        if 0 <= at < duration:
            cues.append({"sound": raw["sound"], "at": at, "volume": float(raw.get("volume", 1.0))})

    for cue in cues:
        if cue["sound"] not in SOUNDS:
            raise MotionError(f"There is no sound called {cue['sound']}. Pick one of: {', '.join(SOUNDS)}.")
        cue["volume"] = max(0.0, min(2.0, cue["volume"] * sound_volume))
    cues = [c for c in cues if c["volume"] > 0]

    placed.sort(key=lambda p: p["start"])
    cues.sort(key=lambda c: c["at"])
    return placed, cues


def filter_graph(pieces, cues, has_audio, video_duration):
    """The FFmpeg filter that lays the pieces from the sheet (input 1) over the video (input 0)
    and mixes the sound cues (inputs 2...). Returns (graph, video label, audio label or None)."""
    parts = []
    video = "[0:v]"
    if pieces:
        # Even sides, so the result can be H.264.
        parts.append("[0:v]crop=trunc(iw/2)*2:trunc(ih/2)*2:0:0[base]")
        video = "[base]"
        parts.append(f"[1:v]split={len(pieces)}" + "".join(f"[s{i}]" for i in range(len(pieces))) if len(pieces) > 1 else "[1:v]null[s0]")
        offset = 0.0
        for i, piece in enumerate(pieces):
            end = offset + piece["duration"]
            start = piece["start"]
            parts.append(
                f"[s{i}]trim=start={offset:.3f}:end={end:.3f},setpts=PTS-STARTPTS+{start:.3f}/TB[p{i}]"
            )
            out = f"[v{i}]"
            parts.append(
                f"{video}[p{i}]overlay=eof_action=pass:format=auto:enable='between(t,{start:.3f},{start + piece['duration']:.3f})'{out}"
            )
            video = out
            offset = end
        parts.append(f"{video}format=yuv420p[vout]")
        video = "[vout]"

    audio = None
    if cues:
        mix = []
        if has_audio:
            mix.append("[0:a]")
        for i, cue in enumerate(cues):
            delay = int(round(cue["at"] * 1000))
            parts.append(
                f"[{i + 2}:a]aformat=sample_rates=48000:channel_layouts=stereo,volume={cue['volume']:.3f},adelay={delay}|{delay}[a{i}]"
            )
            mix.append(f"[a{i}]")
        if not has_audio:
            parts.append(f"anullsrc=r=48000:cl=stereo,atrim=duration={video_duration:.3f}[base]")
            mix.insert(0, "[base]")
        # duration=first keeps the video's own length; normalize=0 keeps its loudness.
        parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:duration=first:dropout_transition=0:normalize=0[aout]")
        audio = "[aout]"
    return ";".join(parts), video, audio


def find_browser():
    """Chromium for HyperFrames: an explicit path, else the one Playwright installed."""
    for name in ("HYPERFRAMES_BROWSER_PATH", "PUPPETEER_EXECUTABLE_PATH"):
        if os.environ.get(name) and os.path.exists(os.environ[name]):
            return os.environ[name]
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", os.path.expanduser("~/.cache/ms-playwright")]
    for root in roots:
        for pattern in ("chromium_headless_shell-*/chrome-*/headless_shell", "chromium-*/chrome-linux*/chrome"):
            found = sorted(glob.glob(os.path.join(root, pattern)))
            if found:
                return found[-1]
    return None


def render_sheet(pieces, width, height, style, work_dir, image_files):
    """Renders every piece end to end into one see-through MOV. Returns its path."""
    project = os.path.join(work_dir, "sheet")
    os.makedirs(project, exist_ok=True)
    for source, name in image_files.items():
        shutil.copy(source, os.path.join(project, name))
    total = sum(p["duration"] for p in pieces)
    with open(os.path.join(project, "index.html"), "w", encoding="utf-8") as f:
        f.write(sheet_html(pieces, width, height, style, total))
    with open(os.path.join(project, "hyperframes.json"), "w") as f:
        f.write("{}")
    out = os.path.join(work_dir, "sheet.mov")
    env = dict(os.environ, HYPERFRAMES_NO_TELEMETRY="1", HYPERFRAMES_TELEMETRY_DISABLED="1", DO_NOT_TRACK="1", HYPERFRAMES_SKIP_SKILLS="1")
    browser = find_browser()
    if browser:
        env["HYPERFRAMES_BROWSER_PATH"] = browser
        env["PUPPETEER_EXECUTABLE_PATH"] = browser
    cmd = [HYPERFRAMES, "render", project, "--format", "mov", "--fps", str(FPS), "-o", out, "--quiet"]
    started = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=RENDER_TIMEOUT)
    except FileNotFoundError:
        raise MotionError("This toolkit cannot draw motion graphics yet: HyperFrames is not installed on it.")
    except subprocess.TimeoutExpired:
        raise MotionError("Drawing the motion graphics took too long.")
    if result.returncode != 0 or not os.path.exists(out):
        logger.error("hyperframes render failed: %s\n%s", result.stdout[-3000:], result.stderr[-3000:])
        raise RuntimeError("HyperFrames could not draw the motion graphics.")
    logger.info("Motion sheet: %d pieces, %.1f s of graphics, drawn in %.1f s", len(pieces), total, time.time() - started)
    return out


def render_motion(src, out, work_dir, request, fetch_image=None):
    """Lays the request's pieces and sounds over `src` and writes `out`. Returns a summary."""
    started = time.time()
    info = probe(src)
    width, height = info["width"] - info["width"] % 2, info["height"] - info["height"] % 2
    pieces, cues = plan(
        request.get("pieces") or [],
        request.get("sounds") or [],
        info["duration"],
        sound_volume=float(request.get("sound_volume", 1.0)),
        piece_sounds=request.get("piece_sounds", True),
    )
    if not pieces and not cues:
        raise MotionError("Nothing to add: no motion pieces or sound effects fall inside the video.")

    # Logos are fetched once and handed to the sheet as local files.
    image_files = {}
    for i, piece in enumerate(pieces):
        if piece["type"] == "logo":
            if fetch_image is None:
                raise MotionError("Logos cannot be fetched here.")
            local = fetch_image(piece["image"], work_dir)
            name = f"image_{i}{os.path.splitext(local)[1] or '.png'}"
            image_files[local] = name
            piece["image"] = name

    inputs = ["-i", src]
    if pieces:
        sheet = render_sheet(pieces, width, height, request.get("style") or {}, work_dir, image_files)
        inputs += ["-i", sheet]
    else:
        # Keep input numbering the same: sounds always start at input 2.
        inputs += ["-f", "lavfi", "-t", "0.1", "-i", "color=c=black@0:s=16x16"]
    for cue in cues:
        inputs += ["-i", SOUNDS[cue["sound"]]]

    graph, video, audio = filter_graph(pieces, cues, info["has_audio"], info["duration"])
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs]
    if graph:
        cmd += ["-filter_complex", graph]
    if pieces:
        cmd += ["-map", video, "-c:v", "libx264", "-preset", "veryfast", "-crf", "18", "-pix_fmt", "yuv420p"]
    else:
        cmd += ["-map", "0:v", "-c:v", "copy"]
    if audio:
        cmd += ["-map", audio, "-c:a", "aac", "-b:a", "192k"]
    elif info["has_audio"]:
        cmd += ["-map", "0:a", "-c:a", "copy"]
    cmd += ["-movflags", "+faststart", "-t", f"{info['duration']:.3f}", out]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        logger.error("ffmpeg motion compose failed: %s", result.stderr[-3000:])
        raise RuntimeError("FFmpeg could not lay the motion graphics over the video.")

    return {
        "pieces": len(pieces),
        "sounds": len(cues),
        "duration": round(info["duration"], 3),
        "width": width,
        "height": height,
        "seconds": round(time.time() - started, 1),
    }
