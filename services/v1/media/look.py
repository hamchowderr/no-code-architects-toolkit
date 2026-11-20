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

"""Look at a video or pictures with CLIP, so footage can be found by what it shows.

CLIP (github.com/openai/CLIP, MIT) puts pictures and words in the same space: a frame of
a garage and the words "a garage" end up close together. This samples frames from a
video (or reads pictures), turns each into a vector, and turns words into vectors too.
The dashboard keeps the vectors and compares them; nothing here decides what matches.

Each frame also says how much of it the largest face covers (YuNet, as in reframe), which
tells a talking head apart from a cutaway shot.

Asked for, it also finds the video's camera cuts (as reframe does), so a cutaway laid over
the video can line up with them instead of landing in the middle of a short shot.

Runs on CPU through ONNX Runtime with the quantized model (models/look, fetched at build).
"""

import json
import logging
import os
import subprocess
import threading

import numpy as np

from services.cpu_limits import available_cpus

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
# The Docker image keeps the files outside the code (LOOK_MODEL_DIR); a checkout keeps them in models/look.
MODEL_DIR = os.environ.get("LOOK_MODEL_DIR") or os.path.join(ROOT, "models", "look")
FACE_MODEL = os.path.join(ROOT, "models", "reframe", "face_detection_yunet_2023mar.onnx")
MODEL_NAME = "clip-vit-b-32"

SIZE = 224
MEAN = np.array([0.48145466, 0.4578275, 0.40821073], dtype=np.float32)
STD = np.array([0.26862954, 0.26130258, 0.27577711], dtype=np.float32)
CONTEXT = 77  # CLIP reads at most this many word pieces

MAX_FRAMES = 40
MAX_IMAGES = 60
MAX_TEXTS = 200
DEFAULT_EVERY = 2.0  # seconds between sampled frames, stretched so a long video stays under MAX_FRAMES
DECIMALS = 4

CUT_FPS = 10  # frames a second read when finding camera cuts
CUT_WIDTH = 160  # ...at this width: enough to tell one shot from the next
CUT_THRESHOLD = 0.12  # histogram distance that counts as a camera cut (as in reframe)
CUT_RATIO = 6  # ...and this many times the usual change over the last second
MAX_CUTS = 2000

_lock = threading.Lock()
_sessions = {}


class LookError(Exception):
    pass


def _session(name):
    with _lock:
        if name not in _sessions:
            import onnxruntime as ort

            path = os.path.join(MODEL_DIR, name)
            if not os.path.exists(path):
                raise LookError(f"The picture model is not installed ({name}). Run python models/look/fetch.py.")
            options = ort.SessionOptions()
            options.intra_op_num_threads = max(1, available_cpus())
            _sessions[name] = ort.InferenceSession(path, options, providers=["CPUExecutionProvider"])
        return _sessions[name]


def _tokenizer():
    with _lock:
        if "tokenizer" not in _sessions:
            from tokenizers import Tokenizer

            path = os.path.join(MODEL_DIR, "tokenizer.json")
            if not os.path.exists(path):
                raise LookError("The picture model is not installed (tokenizer.json). Run python models/look/fetch.py.")
            _sessions["tokenizer"] = Tokenizer.from_file(path)
        return _sessions["tokenizer"]


def _normalise(vectors):
    vectors = np.asarray(vectors, dtype=np.float32)
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.maximum(norms, 1e-8)


def _rounded(vector):
    return [round(float(x), DECIMALS) for x in vector]


# --------------------------------------------------------------------------- pictures


def prepare(rgb):
    """CLIP's own preparation: shortest side to 224 (bicubic), centre crop, normalise."""
    import cv2

    h, w = rgb.shape[:2]
    scale = SIZE / min(h, w)
    resized = cv2.resize(rgb, (max(SIZE, round(w * scale)), max(SIZE, round(h * scale))), interpolation=cv2.INTER_CUBIC)
    h, w = resized.shape[:2]
    top, left = (h - SIZE) // 2, (w - SIZE) // 2
    crop = resized[top : top + SIZE, left : left + SIZE].astype(np.float32) / 255.0
    return ((crop - MEAN) / STD).transpose(2, 0, 1)


def picture_vectors(rgbs):
    """One unit vector per RGB picture (H x W x 3, uint8)."""
    if not rgbs:
        return np.zeros((0, 512), dtype=np.float32)
    session = _session("vision_model_quantized.onnx")
    out = []
    for start in range(0, len(rgbs), 16):
        batch = np.stack([prepare(rgb) for rgb in rgbs[start : start + 16]])
        out.append(session.run(None, {"pixel_values": batch})[0])
    return _normalise(np.concatenate(out))


def text_vectors(texts):
    """One unit vector per text. Longer texts are cut at CLIP's 77 word pieces."""
    if not texts:
        return np.zeros((0, 512), dtype=np.float32)
    tokenizer = _tokenizer()
    session = _session("text_model_quantized.onnx")
    rows = []
    for text in texts:
        ids = tokenizer.encode(text or "").ids
        if len(ids) > CONTEXT:
            ids = ids[: CONTEXT - 1] + [ids[-1]]  # keep the end marker CLIP reads from
        rows.append(ids)
    # Texts of the same length go together, so none needs padding (the exported model has no
    # attention mask, and padding changes its answer slightly).
    out = np.zeros((len(rows), 512), dtype=np.float32)
    by_length = {}
    for index, ids in enumerate(rows):
        by_length.setdefault(len(ids), []).append(index)
    for indexes in by_length.values():
        for start in range(0, len(indexes), 32):
            chunk = indexes[start : start + 32]
            batch = np.array([rows[i] for i in chunk], dtype=np.int64)
            out[chunk] = session.run(None, {"input_ids": batch})[0]
    return _normalise(out)


_face_detector = None


def face_share(rgb):
    """How much of the picture the largest face covers, 0 to 1."""
    global _face_detector
    import cv2

    h, w = rgb.shape[:2]
    scale = 320 / max(h, w)
    small = cv2.resize(rgb, (max(1, round(w * scale)), max(1, round(h * scale))))
    sh, sw = small.shape[:2]
    with _lock:
        if _face_detector is None:
            _face_detector = cv2.FaceDetectorYN.create(FACE_MODEL, "", (sw, sh), 0.7, 0.3, 50)
        _face_detector.setInputSize((sw, sh))
        _, faces = _face_detector.detect(cv2.cvtColor(small, cv2.COLOR_RGB2BGR))
    if faces is None or len(faces) == 0:
        return 0.0
    largest = max(float(f[2]) * float(f[3]) for f in faces)
    return round(min(1.0, largest / (sw * sh)), 4)


# --------------------------------------------------------------------------- files


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:format=duration", "-of", "json", path],
        capture_output=True,
        text=True,
    )
    if out.returncode != 0:
        raise LookError(f"Could not read the file: {out.stderr.strip()[:300]}")
    info = json.loads(out.stdout or "{}")
    video = next((s for s in info.get("streams", []) if s.get("codec_type") == "video"), None)
    if not video:
        raise LookError("The file has no picture in it.")
    return {
        "width": int(video.get("width") or 0),
        "height": int(video.get("height") or 0),
        "duration": float(info.get("format", {}).get("duration") or 0),
    }


def frame_times(duration, every=None):
    """When to sample: every `every` seconds from the middle of the first gap, at most MAX_FRAMES."""
    every = float(every or DEFAULT_EVERY)
    if duration <= 0:
        return [0.0]
    if duration < every:
        return [round(duration / 2, 3)]
    count = int(duration // every)
    if count > MAX_FRAMES:
        every = duration / MAX_FRAMES
        count = MAX_FRAMES
    return [round(every / 2 + i * every, 3) for i in range(count)]


def read_frame(path, time, max_side=448):
    """One RGB frame at `time` seconds, no bigger than `max_side`."""
    out = subprocess.run(
        [
            "ffmpeg", "-v", "error", "-ss", f"{max(0.0, time):.3f}", "-i", path, "-frames:v", "1",
            "-vf", f"scale='if(gt(iw,ih),min({max_side},iw),-2)':'if(gt(iw,ih),-2,min({max_side},ih))'",
            "-f", "image2pipe", "-vcodec", "png", "-",
        ],
        capture_output=True,
    )
    if out.returncode != 0 or not out.stdout:
        return None
    import cv2

    bgr = cv2.imdecode(np.frombuffer(out.stdout, dtype=np.uint8), cv2.IMREAD_COLOR)
    return None if bgr is None else cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def read_picture(path):
    import cv2

    bgr = cv2.imread(path, cv2.IMREAD_COLOR)
    if bgr is None:
        rgb = read_frame(path, 0)  # formats OpenCV cannot open, such as WebP from some sites or a GIF
        if rgb is None:
            raise LookError("Could not read the picture.")
        return rgb
    return cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)


def look_at_media(path, every=None):
    """Frames sampled from a video (or the one picture), each with its vector and face share."""
    info = probe(path)
    is_still = info["duration"] <= 0.05
    times = [0.0] if is_still else frame_times(info["duration"], every)
    frames, rgbs = [], []
    for time in times:
        rgb = read_frame(path, time)
        if rgb is None:
            continue
        rgbs.append(rgb)
        frames.append({"time": time, "face": face_share(rgb)})
    if not frames:
        raise LookError("No frame could be read from the file.")
    for frame, vector in zip(frames, picture_vectors(rgbs)):
        frame["vector"] = _rounded(vector)
    return {
        "duration": round(info["duration"], 3),
        "width": info["width"],
        "height": info["height"],
        "frames": frames,
    }


def camera_cuts(path):
    """When the picture cuts from one shot to another, in seconds.

    The same test as reframe: the colour histogram of a frame against the one before, a cut
    when the change is large both by itself and next to the usual change over the last
    second, and never two within half a second.
    """
    import cv2

    info = probe(path)
    if info["duration"] <= 0.05 or not info["width"] or not info["height"]:
        return []
    height = max(2, round(info["height"] * CUT_WIDTH / info["width"] / 2) * 2)
    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-an", "-vf", f"fps={CUT_FPS},scale={CUT_WIDTH}:{height}",
         "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    size = CUT_WIDTH * height * 3
    cuts, recent, before, n = [], [], None, 0
    try:
        while len(cuts) < MAX_CUTS:
            buf = proc.stdout.read(size)
            if len(buf) < size:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(height, CUT_WIDTH, 3)
            hist = cv2.calcHist([cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            if before is not None:
                change = cv2.compareHist(before, hist, cv2.HISTCMP_BHATTACHARYYA)
                usual = float(np.median(recent)) if recent else 0.0
                last = round(cuts[-1] * CUT_FPS) if cuts else -CUT_FPS
                if change > CUT_THRESHOLD and change > CUT_RATIO * usual and n - last > CUT_FPS // 2:
                    cuts.append(round(n / CUT_FPS, 2))
                recent = (recent + [change])[-CUT_FPS:]
            before = hist
            n += 1
    finally:
        proc.stdout.close()
        proc.kill()
        proc.wait()
    return cuts


def look(media_path=None, picture_paths=None, texts=None, every=None, cuts=False, frames=True):
    """Everything asked for in one answer. `picture_paths` is a list of (url, path or None).

    For a file: its frames (unless `frames` is False) and, when `cuts` is True, its camera
    cuts.
    """
    result = {"model": MODEL_NAME}
    if media_path:
        if frames:
            result["media"] = look_at_media(media_path, every)
        else:
            info = probe(media_path)
            result["media"] = {"duration": round(info["duration"], 3), "width": info["width"], "height": info["height"]}
        if cuts:
            result["media"]["cuts"] = camera_cuts(media_path)
    if picture_paths is not None:
        pictures, rgbs, where = [], [], []
        for url, path in picture_paths:
            entry = {"url": url}
            try:
                if not path:
                    raise LookError("Could not download the picture.")
                rgbs.append(read_picture(path))
                where.append(len(pictures))
            except LookError as e:
                entry["error"] = str(e)
            pictures.append(entry)
        for index, vector in zip(where, picture_vectors(rgbs)):
            pictures[index]["vector"] = _rounded(vector)
        result["pictures"] = pictures
    if texts is not None:
        result["texts"] = [{"text": text, "vector": _rounded(vector)} for text, vector in zip(texts, text_vectors(texts))]
    return result
