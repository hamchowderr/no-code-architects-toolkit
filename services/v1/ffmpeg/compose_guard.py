# Copyright (c) 2025 Stephen G. Pope
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

"""What a /v1/ffmpeg/compose request may not ask FFmpeg to do.

Compose hands its filters and settings to FFmpeg as they are. FFmpeg can read files on
this machine (drawtext's textfile, movie, a filter setting read from a file), write files
wherever a bare word names one, and load plugins. Anyone holding an API key could then
print this server's own keys into a video (textfile=/proc/1/environ) or overwrite the
app. Files only ever come in as links (they are downloaded first), so nothing a real edit
needs is refused. Fonts are the one exception: fontfile and fontsdir may point at the
fonts this image ships.
"""

import re

BLOCKED_FILTERS = {
    "movie", "amovie", "sendcmd", "asendcmd", "zmq", "azmq", "frei0r", "frei0r_src",
    "ladspa", "lv2", "lut3d", "lut1d", "lut", "dnn_processing", "dnn_classify",
    "dnn_detect", "sr", "derain", "vidstabdetect", "vidstabtransform", "ocr",
    "signature", "vmafmotion", "libvmaf", "psnr", "ssim", "qrencode", "qrencodesrc",
}

# Filter settings whose value FFmpeg reads as a file on this machine.
FILE_OPTIONS = {"textfile", "fontfile", "filename", "file", "stats_file", "log_path", "model", "fontsdir"}

# Settings that may name one of the fonts this image ships.
FONT_OPTIONS = {"fontfile", "fontsdir"}
FONT_DIRS = ("/usr/share/fonts/", "/usr/local/share/fonts/")

# Filters that may name a file, as long as it is a link (downloaded first).
LINK_FILTERS = {"subtitles", "ass"}

BLOCKED_OPTIONS = {
    "-i", "-filter_script", "-filter_complex_script", "-attach", "-dump_attachment",
    "-protocol_whitelist", "-protocol_blacklist", "-vstats", "-vstats_file",
    "-passlogfile", "-sdp_file", "-progress", "-report", "-init_hw_device",
    "-filter_hw_device", "-hwaccel_device", "-qsv_device", "-vaapi_device",
    "-stats_period", "-benchmark_all", "-dump", "-hex", "-fpre", "-pre", "-apre",
    "-vpre", "-spre",
}

FILTER_OPTION = re.compile(r"^-(vf|af|filter(:[avs](:\d+)?)?|filter_complex|lavfi)$")
BLOCKED_FORMATS = {"concat", "hls", "lavfi", "image2", "image2pipe", "ffconcat"}
OPTION = re.compile(r"^-[A-Za-z0-9][A-Za-z0-9_:.+-]{0,60}$")
LINK = re.compile(r"https?://[^\s'\"\\\]\[;,]+")
PROTOCOL_IN_FILTER = re.compile(r"\b(file|pipe|fd|subfile|concat|tcp|udp|unix):", re.I)
PROTOCOL_VALUE = re.compile(r"^(file|pipe|fd|tcp|udp|unix|data|subfile|concat|tee):", re.I)
PATH_VALUE = re.compile(r"^[/~\\]|\.\.[/\\]|[A-Za-z]:[/\\]")


def _font_path(value):
    value = value.strip().strip("'\"")
    return ".." not in value and value.startswith(FONT_DIRS)


def filter_problem(graph):
    """Why a filter graph is refused, or None."""
    # Fonts this image ships are fine; keep them out of the path checks below.
    graph = re.sub(
        r"(fontfile|fontsdir)=('?)(/usr(?:/local)?/share/fonts/[^:'\\,;\[\]]*)\2",
        lambda m: f"{m.group(1)}=FONT" if _font_path(m.group(3)) else m.group(0),
        graph,
    )
    # Links are downloaded first, so what is inside one is not a path. Then escaped
    # characters and quoted words (a caption's text) are set aside: FFmpeg reads them as
    # plain text, never as a filter, a setting or a file.
    plain = re.sub(r"'(https?://[^']*)'", "LINK", graph)
    plain = LINK.sub("LINK", plain)
    plain = re.sub(r"\\.", "E", plain)
    plain = re.sub(r"'[^']*'", "Q", plain)
    if PROTOCOL_IN_FILTER.search(plain):
        return "A filter names a local file or connection. Use a link instead."
    for raw in re.split(r"[;,]", plain):
        part = re.sub(r"\[[^\]]*\]", "", raw).strip()
        if not part:
            continue
        at = part.find("=")
        name = (part if at == -1 else part[:at]).strip().lower()
        name = re.sub(r"@.*$", "", name)
        if name.startswith("/"):
            return "A filter reads its settings from a file."
        if name in BLOCKED_FILTERS:
            return f"The {name} filter is not available here."
        if at == -1:
            continue
        for index, setting in enumerate(part[at + 1:].split(":")):
            equals = setting.find("=")
            key = None if equals == -1 else setting[:equals].strip().lower()
            value = (setting if equals == -1 else setting[equals + 1:]).strip()
            # FFmpeg 7 reads a setting written "/name=path" from the file at path.
            if key is not None and key.startswith("/"):
                return "A filter reads one of its settings from a file."
            names_file = (key is not None and key in FILE_OPTIONS) or (
                key is None and index == 0 and name in LINK_FILTERS
            )
            if not names_file or value == "LINK":
                continue
            if key in FONT_OPTIONS and value == "FONT":
                continue
            return f"The {name} filter would read a file on this server. Give it a link instead."
    return None


def _option_problem(entry, where):
    option = entry.get("option") if isinstance(entry, dict) else None
    option = option.strip() if isinstance(option, str) else ""
    if not OPTION.match(option):
        return f'"{str(option)[:40]}" is not an FFmpeg setting. Settings start with a dash.'
    name = option.lower()
    if name in BLOCKED_OPTIONS:
        return f"The {option} setting is not available here."
    argument = entry.get("argument")
    if argument is None or isinstance(argument, (int, float)) and not isinstance(argument, bool):
        return None
    if not isinstance(argument, str):
        return f"The {option} setting has a value FFmpeg can't use."
    if FILTER_OPTION.match(name):
        return filter_problem(argument)
    value = argument.strip()
    if name == "-f" and where != "output" and value.lower() in BLOCKED_FORMATS:
        return f"Reading files as {argument} is not available here."
    if PROTOCOL_VALUE.match(value):
        return f"The {option} setting names a local file or connection."
    if PATH_VALUE.search(value):
        return f"The {option} setting names a file on this server."
    return None


def compose_problem(data):
    """Why a compose request is refused, in plain words, or None when it may run."""
    for item in data.get("inputs") or []:
        url = item.get("file_url") if isinstance(item, dict) else None
        if not isinstance(url, str) or not re.match(r"^https?://", url, re.I):
            return "Every input must be a link."
        for option in item.get("options") or []:
            problem = _option_problem(option, "input")
            if problem:
                return problem
    for option in data.get("global_options") or []:
        problem = _option_problem(option, "global")
        if problem:
            return problem
    for item in data.get("filters") or []:
        graph = item.get("filter") if isinstance(item, dict) else None
        if not isinstance(graph, str):
            return "A filter is not text."
        problem = filter_problem(graph)
        if problem:
            return problem
    for output in data.get("outputs") or []:
        for option in (output.get("options") if isinstance(output, dict) else None) or []:
            problem = _option_problem(option, "output")
            if problem:
                return problem
    return None
