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

Behind the speaker: a piece with "layer": "behind" is drawn between the background and
the person. The person is cut back out with their mask (services/v1/video/cutout.py) and
laid over it, so words, graphs and doodles sit behind their head. The same mask draws
sticker outlines ("outlines") around the person. "zooms" punch in on the picture last.
"""

import glob
import json
import logging
import os
import shutil
import subprocess
import time

from ..cutout import CutoutError, head_top
from ..cutout import probe as probe_video
from .pieces import NEEDS_TITLE, PIECES, big_text_size, sheet_html

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
MAX_ZOOMS = 200
MAX_OUTLINES = 50
FPS = 30
ZOOM_RAMP = 0.12  # seconds a punch-in takes to reach its size


class MotionError(Exception):
    """A request that cannot be done as asked. The message is shown to the person."""


def probe(path):
    """Upright size (phone videos are turned the right way up), length, frame rate and sound."""
    try:
        info = probe_video(path)
    except CutoutError as e:
        raise MotionError(str(e).replace("cut the speaker out of", "put motion graphics on"))
    return info


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
        if kind in NEEDS_TITLE and not str(raw.get("title") or "").strip():
            raise MotionError(f"A {kind.replace('_', ' ')} piece needs its words.")
        if kind == "checklist" and not (raw.get("items") or str(raw.get("title") or "").strip()):
            raise MotionError("A checklist piece needs its items.")
        piece = dict(raw, start=round(start, 3), duration=round(length, 3), layer=raw.get("layer") or "front")
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


def plan_effects(outlines, zooms, duration):
    """Checks the sticker outlines and punch-ins. Returns both, sorted and kept inside the video."""
    if len(outlines) > MAX_OUTLINES:
        raise MotionError(f"At most {MAX_OUTLINES} sticker outlines can go on one video.")
    if len(zooms) > MAX_ZOOMS:
        raise MotionError(f"At most {MAX_ZOOMS} punch-ins can go on one video.")

    def window(raw):
        start = max(0.0, float(raw.get("start", 0)))
        length = raw.get("duration")
        end = duration if length is None else min(duration, start + float(length))
        return round(start, 3), round(end, 3)

    kept_outlines = []
    for raw in outlines:
        start, end = window(raw)
        if end - start >= 0.1:
            kept_outlines.append({
                "start": start, "end": end,
                "color": raw.get("color") or "#ffffff",
                "width": max(0.3, min(4.0, float(raw.get("width", 1.4)))),
            })
    kept_zooms = []
    for raw in zooms:
        start, end = window(raw)
        if end - start >= 0.1:
            kept_zooms.append({
                "start": start, "end": end,
                "scale": max(1.02, min(2.5, float(raw.get("scale", 1.3)))),
                "focus_y": max(0.0, min(1.0, float(raw.get("focus_y", 0.35)))),
            })
    for earlier, later in zip(sorted(kept_zooms, key=lambda z: z["start"]), sorted(kept_zooms, key=lambda z: z["start"])[1:]):
        if later["start"] < earlier["end"]:
            raise MotionError("Two punch-ins overlap. Give each its own time.")
    kept_outlines.sort(key=lambda o: o["start"])
    kept_zooms.sort(key=lambda z: z["start"])
    return kept_outlines, kept_zooms


def zoom_filter(zooms, width, height, fps):
    """A zoompan filter that punches in for each zoom (eased over ZOOM_RAMP) and back out."""
    z = "1"
    y = "ih/2-(ih/zoom/2)"
    for zm in zooms:
        a, b, gain = zm["start"], zm["end"], zm["scale"] - 1
        z += f"+{gain:.4f}*between(it,{a:.3f},{b:.3f})*min(1,(it-{a:.3f})/{ZOOM_RAMP})"
    if zooms:
        # One focus height for the whole video keeps the expression short; the first zoom's wins.
        fy = zooms[0]["focus_y"]
        y = f"(ih-ih/zoom)*{fy:.3f}"
    return f"zoompan=z='{z}':x='iw/2-(iw/zoom/2)':y='{y}':d=1:s={width}x{height}:fps={fps:.6f}"


def outline_px(width_percent, width, height):
    return max(1.0, width_percent / 100.0 * min(width, height))


def _hex_rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def filter_graph(pieces, cues, has_audio, video_duration, mask_input=None, outlines=(), zooms=(), size=None, fps=FPS):
    """The FFmpeg filter that lays the pieces from the sheet (input 1) over the video (input 0)
    and mixes the sound cues (inputs 2...). With behind pieces or outlines, the speaker's mask
    is input `mask_input`. Returns (graph, video label, audio label or None)."""
    parts = []
    video = "[0:v]"
    width, height = size or (0, 0)
    behind = [i for i, p in enumerate(pieces) if p.get("layer") == "behind"]
    layered = mask_input is not None and (behind or outlines)
    if pieces or outlines or zooms:
        # Even sides, so the result can be H.264.
        crop = "[0:v]crop=trunc(iw/2)*2:trunc(ih/2)*2:0:0,setsar=1"
        parts.append(crop + (",split=2[base][keep]" if layered else "[base]"))
        video = "[base]"
        if layered:
            masks = 1 + len(outlines)
            labels = "".join(f"[m{i}]" for i in range(masks))
            parts.append(
                f"[{mask_input}:v]format=gray,scale={width}:{height}:flags=bicubic,setsar=1"
                + (f",split={masks}{labels}" if masks > 1 else "[m0]")
            )
            parts.append("[keep][m0]alphamerge[person]")

        if pieces:
            parts.append(f"[1:v]split={len(pieces)}" + "".join(f"[s{i}]" for i in range(len(pieces))) if len(pieces) > 1 else "[1:v]null[s0]")
        offsets = []
        offset = 0.0
        for piece in pieces:
            offsets.append(offset)
            offset += piece["duration"]

        step = [0]

        def lay(i):
            nonlocal video
            piece, start, off = pieces[i], pieces[i]["start"], offsets[i]
            parts.append(f"[s{i}]trim=start={off:.3f}:end={off + piece['duration']:.3f},setpts=PTS-STARTPTS+{start:.3f}/TB[p{i}]")
            out = f"[v{step[0]}]"
            step[0] += 1
            parts.append(
                f"{video}[p{i}]overlay=eof_action=pass:format=auto:enable='between(t,{start:.3f},{start + piece['duration']:.3f})'{out}"
            )
            video = out

        front = [i for i in range(len(pieces)) if i not in behind] if layered else list(range(len(pieces)))
        if layered:
            for i in behind:
                lay(i)
            for j, ol in enumerate(outlines):
                # The mask, spread out by the outline's width, filled with its colour.
                px = outline_px(ol["width"], width, height)
                r, g, b = _hex_rgb(ol["color"])
                parts.append(
                    f"[m{j + 1}]gblur=sigma={px / 2:.2f},lut=c0='if(gt(val,6),255,0)',gblur=sigma=1,split=2[oa{j}][ob{j}]"
                )
                parts.append(f"[oa{j}]format=rgb24,lutrgb=r={r}:g={g}:b={b}[oc{j}]")
                parts.append(f"[oc{j}][ob{j}]alphamerge[ring{j}]")
                out = f"[v{step[0]}]"
                step[0] += 1
                parts.append(f"{video}[ring{j}]overlay=format=auto:enable='between(t,{ol['start']:.3f},{ol['end']:.3f})'{out}")
                video = out
            out = f"[v{step[0]}]"
            step[0] += 1
            parts.append(f"{video}[person]overlay=format=auto{out}")
            video = out
        for i in front:
            lay(i)
        if zooms:
            out = f"[v{step[0]}]"
            step[0] += 1
            parts.append(f"{video}{zoom_filter(zooms, width, height, fps)}{out}")
            video = out
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


def needs_mask(request):
    """True when the request draws something that needs the speaker cut out."""
    return bool(request.get("outlines")) or any((p.get("layer") == "behind") for p in request.get("pieces") or [])


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


def render_motion(src, out, work_dir, request, fetch_image=None, mask=None):
    """Lays the request's pieces and sounds over `src` and writes `out`. Returns a summary.

    `mask` is the speaker's mask video (cutout.make_mask), needed for behind pieces and outlines."""
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
    outlines, zooms = plan_effects(request.get("outlines") or [], request.get("zooms") or [], info["duration"])
    if not pieces and not cues and not outlines and not zooms:
        raise MotionError("Nothing to add: no motion pieces, sound effects, outlines or punch-ins fall inside the video.")
    layered = any(p["layer"] == "behind" for p in pieces) or outlines
    if layered and not mask:
        raise MotionError("Pieces behind the speaker and sticker outlines need the speaker cut out first.")

    # With the speaker's mask, a doodle with no place given goes just above their head, and
    # big words behind them drop so the head covers the bottom of the letters.
    short = min(width, height)
    for piece in pieces:
        if not mask or piece.get("x") is not None or piece.get("y") is not None:
            continue
        if piece["type"] == "doodle":
            spot = head_top(mask, piece["start"] + 0.2, width, height)
            if spot:
                piece["x"], piece["y"] = spot[0], max(6.0, spot[1] - 9.0)
        elif piece["type"] == "big_text" and piece["layer"] == "behind":
            spot = head_top(mask, piece["start"] + 0.2, width, height)
            if spot:
                letters = big_text_size(piece.get("title") or "", width / short) * short / 100 * 0.9 / height * 100
                piece["y"] = max(4.0, min(60.0, spot[1] - letters * 0.6))

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
    mask_input = None
    if layered:
        mask_input = 2 + len(cues)
        inputs += ["-i", mask]

    graph, video, audio = filter_graph(
        pieces, cues, info["has_audio"], info["duration"],
        mask_input=mask_input, outlines=outlines, zooms=zooms, size=(width, height), fps=info["fps"],
    )
    cmd = ["ffmpeg", "-y", "-v", "error", *inputs]
    if graph:
        cmd += ["-filter_complex", graph]
    if pieces or outlines or zooms:
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
        "behind": sum(1 for p in pieces if p["layer"] == "behind"),
        "outlines": len(outlines),
        "zooms": len(zooms),
        "sounds": len(cues),
        "duration": round(info["duration"], 3),
        "width": width,
        "height": height,
        "seconds": round(time.time() - started, 1),
    }
