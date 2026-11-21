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
What a piece of footage is like before anyone edits it, read by FFmpeg alone in one pass:
its shape as it plays (rotation counted), frame rate and length, how loud its sound is and
whether it clips, and where its picture is black or frozen. No AI. The caller decides what
counts as a problem; this only measures.
"""

import json
import logging
import re
import subprocess

logger = logging.getLogger(__name__)

# The longest stretch read, in seconds: enough for any creator take or ad.
DEFAULT_MAX_SECONDS = 1200
# The picture is scaled down this wide to look for black and frozen frames: same answer, faster.
LOOK_WIDTH = 320


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number else None  # NaN is no number


def _fps(rate):
    if not rate or rate == "0/0":
        return None
    if "/" in str(rate):
        top, bottom = str(rate).split("/", 1)
        top, bottom = _number(top), _number(bottom)
        return round(top / bottom, 3) if top and bottom else None
    return _number(rate)


def _rotation(stream):
    for side in stream.get("side_data_list") or []:
        if "rotation" in side:
            return int(_number(side["rotation"]) or 0)
    rotate = (stream.get("tags") or {}).get("rotate")
    return int(_number(rotate) or 0)


def probe(path):
    """The first picture and sound streams, and the file's length."""
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True,
        text=True,
        check=True,
    )
    info = json.loads(out.stdout or "{}")
    streams = info.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video" and not (s.get("disposition") or {}).get("attached_pic")), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    duration = _number((info.get("format") or {}).get("duration"))
    if duration is None and video:
        duration = _number(video.get("duration"))
    found = {
        "duration": round(duration, 3) if duration is not None else None,
        "has_video": video is not None,
        "has_audio": audio is not None,
        "width": None,
        "height": None,
        "rotation": 0,
        "fps": None,
    }
    if video:
        width, height = int(video.get("width") or 0), int(video.get("height") or 0)
        rotation = _rotation(video)
        # A phone filming upright stores the picture sideways with a rotation: count it.
        if abs(rotation) % 180 == 90:
            width, height = height, width
        found.update(
            width=width or None,
            height=height or None,
            rotation=rotation,
            fps=_fps(video.get("avg_frame_rate")) or _fps(video.get("r_frame_rate")),
        )
    if audio:
        found["audio_channels"] = int(audio.get("channels") or 0) or None
    return found


_BLACK = re.compile(r"black_start:(-?[\d.]+)\s+black_end:(-?[\d.]+)")
_FREEZE_START = re.compile(r"lavfi\.freezedetect\.freeze_start:\s*(-?[\d.]+)")
_FREEZE_END = re.compile(r"lavfi\.freezedetect\.freeze_end:\s*(-?[\d.]+)")
_LOUDNESS = re.compile(r"Integrated loudness:\s*\n\s*I:\s*(-?[\d.]+|-inf)\s*LUFS")
_PEAK_LEVEL = re.compile(r"Peak level dB:\s*(-?[\d.]+|-inf)")
_PEAK_COUNT = re.compile(r"Peak count:\s*([\d.]+)")
_SAMPLES = re.compile(r"Number of samples:\s*(\d+)")


def _stretches(starts, ends, until):
    found = []
    for at, start in enumerate(starts):
        end = ends[at] if at < len(ends) else until
        if end is None:
            continue
        if end > start:
            found.append({"start": round(start, 3), "end": round(end, 3)})
    return found


def _overall(stderr):
    """astats prints each channel, then "Overall": read the last of each figure."""
    part = stderr.split("Overall", 1)[1] if "Overall" in stderr else stderr

    def last(pattern):
        values = pattern.findall(part)
        return values[-1] if values else None

    return last(_PEAK_LEVEL), last(_PEAK_COUNT), last(_SAMPLES)


def measure(path, found, max_seconds=DEFAULT_MAX_SECONDS):
    """One FFmpeg pass for the black and frozen picture and the sound's loudness and peaks."""
    filters, maps = [], []
    if found["has_video"]:
        filters.append(
            f"[0:v:0]scale={LOOK_WIDTH}:-2,"
            "blackdetect=d=0.5:pix_th=0.10:pic_th=0.98,"
            "freezedetect=n=-60dB:d=2,metadata=mode=print[v]"
        )
        maps += ["-map", "[v]"]
    if found["has_audio"]:
        filters.append("[0:a:0]ebur128=peak=sample,astats[a]")
        maps += ["-map", "[a]"]
    if not filters:
        return {}
    cmd = [
        "ffmpeg", "-hide_banner", "-nostats", "-t", str(max_seconds), "-i", path,
        "-filter_complex", ";".join(filters), *maps, "-f", "null", "-",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        tail = (result.stderr or "").strip().splitlines()[-3:]
        raise RuntimeError("FFmpeg could not read the footage: " + " ".join(tail))
    stderr = result.stderr or ""
    checked = min(found["duration"] or max_seconds, max_seconds)
    measured = {"checked_seconds": round(checked, 3)}
    if found["has_video"]:
        black = [
            {"start": round(float(a), 3), "end": round(float(b), 3)}
            for a, b in _BLACK.findall(stderr)
        ]
        starts = [float(x) for x in _FREEZE_START.findall(stderr)]
        ends = [float(x) for x in _FREEZE_END.findall(stderr)]
        measured["black"] = black
        measured["frozen"] = _stretches(starts, ends, checked)
    if found["has_audio"]:
        loud = _LOUDNESS.findall(stderr)
        peak, count, samples = _overall(stderr)
        loudness = _number(loud[-1]) if loud and loud[-1] != "-inf" else None
        measured["loudness_lufs"] = loudness if loudness is not None and loudness > -70 else None
        measured["peak_db"] = _number(peak) if peak and peak != "-inf" else None
        measured["peak_count"] = int(_number(count) or 0)
        measured["samples"] = int(samples) if samples else None
    return measured


def check_footage(path, max_seconds=DEFAULT_MAX_SECONDS):
    """Everything this reads about one file."""
    found = probe(path)
    if not found["has_video"] and not found["has_audio"]:
        raise RuntimeError("The file has no picture or sound to check.")
    found.update(measure(path, found, max_seconds))
    return found
