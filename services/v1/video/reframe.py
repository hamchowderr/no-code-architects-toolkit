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

"""Reframe a wide video to a tall (or square) one that follows the person talking.

How it works:

1. Decode the video at 25 frames a second and find faces with OpenCV's YuNet
   detector (model: MIT). Faces are linked into tracks frame to frame.
   Tracks are cut at camera cuts in the source.
2. Score each track with LR-ASD (github.com/airmeet/LR-ASD, MIT), a small
   audio-and-video model that says whether that face is the one talking.
   It runs on CPU through ONNX Runtime (already installed with faster-whisper).
3. Turn the scores into a camera path: hold on the speaker, drift gently when
   they move, cut across when the speaker changes.
4. FFmpeg crops along that path (sendcmd drives the crop filter's x) and scales.

A camera path can also be passed in, which skips steps 1 to 3.
"""

import json
import logging
import math
import os
import subprocess
import threading
import time

import numpy as np

from services.cpu_limits import available_cpus

logger = logging.getLogger(__name__)

MODEL_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))), "models", "reframe")
FACE_MODEL = os.path.join(MODEL_DIR, "face_detection_yunet_2023mar.onnx")
SPEAKER_MODEL = os.path.join(MODEL_DIR, "lr_asd_pretrain_ava.onnx")

FPS = 25  # analysis frame rate; LR-ASD expects 25 video frames and 100 audio frames a second
SAMPLE_RATE = 16000
DETECT_WIDTH = 640  # faces are found on a frame this wide
DECODE_MAX_WIDTH = 1280  # frames are decoded no wider than this; face crops come from it
MIN_FACE_PX = 40  # faces narrower than this (in decoded pixels) are ignored
TRACK_GAP = 10  # frames a face may go missing before its track ends
MIN_TRACK = FPS  # tracks shorter than a second are ignored
SPEAKING = 0.0  # LR-ASD score (after 1 s smoothing) above which a face counts as talking; below 0 the model leans "silent", and a silent listener can drift to -1 while someone off camera talks
CUT_THRESHOLD = 0.12  # histogram distance between frames that counts as a camera cut (normal motion is about 0.01)
CUT_RATIO = 6  # ...and it must also be this many times the usual change over the last second
SCORE_WINDOWS = (1, 2, 4)  # LR-ASD runs over chunks of these lengths (seconds), averaged

ASPECTS = {"9:16": (9, 16), "1:1": (1, 1), "4:5": (4, 5), "16:9": (16, 9)}

DEFAULTS = {
    "aspect": "9:16",
    "follow": "speaker",  # speaker | largest_face | center
    "switch_after": 1.0,  # seconds a new speaker must talk before the camera cuts to them
    "min_hold": 1.5,  # never cut more often than this (seconds)
    "smoothing": 1.5,  # seconds over which movement within one shot is smoothed
    "output_width": 1080,
}


class ReframeError(Exception):
    pass


# --------------------------------------------------------------------------- audio


def _hz_to_mel(hz):
    return 2595 * np.log10(1 + hz / 700.0)


def _mel_to_hz(mel):
    return 700 * (10 ** (mel / 2595.0) - 1)


def mfcc(signal, samplerate=SAMPLE_RATE, winlen=0.025, winstep=0.01, numcep=13, nfilt=26, nfft=512, preemph=0.97, ceplifter=22):
    """MFCC features, numerically the same as python_speech_features.mfcc with these defaults
    (which is what LR-ASD was trained with). Written here so the toolkit needs no extra package."""
    signal = np.asarray(signal, dtype=np.float64)
    if signal.size == 0:
        return np.zeros((0, numcep))
    signal = np.append(signal[0], signal[1:] - preemph * signal[:-1])
    frame_len = int(round(winlen * samplerate))
    frame_step = int(round(winstep * samplerate))
    slen = len(signal)
    numframes = 1 if slen <= frame_len else 1 + int(math.ceil((slen - frame_len) / frame_step))
    padlen = (numframes - 1) * frame_step + frame_len
    padded = np.concatenate((signal, np.zeros(padlen - slen)))
    idx = np.arange(frame_len)[None, :] + (np.arange(numframes) * frame_step)[:, None]
    frames = padded[idx]
    pspec = (1.0 / nfft) * np.square(np.abs(np.fft.rfft(frames, nfft)))
    energy = np.sum(pspec, 1)
    energy = np.where(energy == 0, np.finfo(float).eps, energy)

    melpoints = np.linspace(_hz_to_mel(0), _hz_to_mel(samplerate / 2), nfilt + 2)
    bins = np.floor((nfft + 1) * _mel_to_hz(melpoints) / samplerate)
    fbank = np.zeros([nfilt, nfft // 2 + 1])
    for j in range(nfilt):
        for i in range(int(bins[j]), int(bins[j + 1])):
            fbank[j, i] = (i - bins[j]) / (bins[j + 1] - bins[j])
        for i in range(int(bins[j + 1]), int(bins[j + 2])):
            fbank[j, i] = (bins[j + 2] - i) / (bins[j + 2] - bins[j + 1])
    feat = pspec @ fbank.T
    feat = np.log(np.where(feat == 0, np.finfo(float).eps, feat))

    # DCT type II, orthonormal (as scipy.fftpack.dct(norm="ortho"))
    n = feat.shape[1]
    k = np.arange(numcep)[:, None]
    basis = np.cos(np.pi * k * (2 * np.arange(n)[None, :] + 1) / (2 * n)) * 2
    scale = np.full((numcep, 1), math.sqrt(1 / (2 * n)))
    scale[0] = math.sqrt(1 / (4 * n))
    feat = feat @ (basis * scale).T

    lift = 1 + (ceplifter / 2.0) * np.sin(np.pi * np.arange(numcep) / ceplifter)
    feat = feat * lift
    feat[:, 0] = np.log(energy)
    return feat


# --------------------------------------------------------------------------- video helpers


def probe(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type,width,height:stream_tags=rotate:format=duration", "-of", "json", path],
        capture_output=True, text=True,
    )
    if out.returncode != 0:
        raise ReframeError(f"Could not read the video: {out.stderr.strip()[:300]}")
    info = json.loads(out.stdout or "{}")
    streams = info.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    if not video:
        raise ReframeError("The file has no video in it.")
    duration = float(info.get("format", {}).get("duration") or 0)
    return {
        "width": int(video["width"]),
        "height": int(video["height"]),
        "duration": duration,
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def read_audio(path):
    out = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    if out.returncode != 0:
        return np.zeros(0, dtype=np.int16)
    return np.frombuffer(out.stdout, dtype=np.int16)


def even(v):
    return max(2, int(v) // 2 * 2)


def crop_box(width, height, aspect):
    """Largest crop of the given aspect. Moves left/right when the source is wider,
    otherwise sits centred (top and bottom trimmed) and does not move."""
    aw, ah = ASPECTS[aspect]
    crop_w = even(round(height * aw / ah))
    if crop_w <= width:
        return {"width": crop_w, "height": even(height), "moves": crop_w < width, "y": 0}
    crop_h = even(round(width * ah / aw))
    return {"width": even(width), "height": crop_h, "moves": False, "y": (height - crop_h) // 2 // 2 * 2}


# --------------------------------------------------------------------------- face finding and tracking


_sessions = {}
_sessions_lock = threading.Lock()


def _speaker_session():
    import onnxruntime as ort

    with _sessions_lock:
        if "asd" not in _sessions:
            opts = ort.SessionOptions()
            opts.intra_op_num_threads = min(4, available_cpus())
            opts.inter_op_num_threads = 1
            _sessions["asd"] = ort.InferenceSession(SPEAKER_MODEL, sess_options=opts, providers=["CPUExecutionProvider"])
        return _sessions["asd"]


def _iou(a, b):
    x1, y1 = max(a[0], b[0]), max(a[1], b[1])
    x2, y2 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / (union + 1e-9)


def _face_crop(frame, box, cv2):
    """The 112x112 grey mouth-and-face crop LR-ASD was trained on (same geometry as its demo)."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    bs = max(w, h) / 2
    cs = 0.40
    cx, cy = x0 + w / 2, y0 + h / 2
    pad = int(bs * 3) + 2
    padded = cv2.copyMakeBorder(frame, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=(110, 110, 110))
    ya, yb = int(cy - bs) + pad, int(cy + bs * (1 + 2 * cs)) + pad
    xa, xb = int(cx - bs * (1 + cs)) + pad, int(cx + bs * (1 + cs)) + pad
    face = cv2.resize(padded[ya:yb, xa:xb], (224, 224))
    return cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)[56:168, 56:168]


class Track:
    def __init__(self, tid, shot):
        self.id = tid
        self.shot = shot
        self.frames = []
        self.boxes = []
        self.crops = []

    def add(self, frame_no, box, crop):
        if self.frames and crop is not None:
            # fill short gaps by repeating the last crop so crops line up with frames
            for _ in range(frame_no - self.frames[-1] - 1):
                self.crops.append(self.crops[-1])
        self.frames.append(frame_no)
        self.boxes.append(box)
        if crop is not None:
            self.crops.append(crop)

    @property
    def first(self):
        return self.frames[0]

    @property
    def last(self):
        return self.frames[-1]


def score_track(track, audio):
    """LR-ASD score for every frame from the track's first to last frame (higher = talking)."""
    length = track.last - track.first + 1
    video = np.stack(track.crops[:length]).astype(np.float32)
    a0 = int(track.first / FPS * SAMPLE_RATE)
    a1 = int((track.last + 1) / FPS * SAMPLE_RATE)
    feats = mfcc(audio[a0:a1])
    seconds = min((feats.shape[0] - feats.shape[0] % 4) / 100, video.shape[0] / FPS)
    feats = feats[: int(round(seconds * 100))].astype(np.float32)
    video = video[: int(round(seconds * FPS))]
    if video.shape[0] == 0:
        return np.full(length, -5.0)
    session = _speaker_session()
    runs = []
    for d in SCORE_WINDOWS:
        scores = []
        for i in range(int(math.ceil(seconds / d))):
            a = feats[i * d * 100:(i + 1) * d * 100]
            v = video[i * d * FPS:(i + 1) * d * FPS]
            if v.shape[0] == 0 or a.shape[0] < 4:
                continue
            a = a[: v.shape[0] * 4]
            v = v[: a.shape[0] // 4]
            out = session.run(None, {"audio": a[None], "video": v[None]})[0]
            scores.extend(out.tolist())
        runs.append(scores)
    m = min(len(r) for r in runs)
    result = np.full(length, -5.0)
    result[:m] = np.mean([r[:m] for r in runs], axis=0)
    return result


def _shares_screen(track, done, open_tracks):
    """
    Whether another face is on screen at any time this one is. Only then does it matter
    who is talking: a face that is alone whenever it shows is the one the camera is on
    either way, so scoring it (about a third of the analysis time) is skipped.

    This is complete when the track finishes: a finished face that overlapped it was
    still open when that one finished, so it was scored then, and any face still open
    that started before this one ended overlaps it (counted even if it later turns out
    not to, which only means scoring more than needed).
    """
    for other in done:
        if other is not track and other.first <= track.last and other.last >= track.first:
            return True
    return any(other is not track and other.first <= track.last for other in open_tracks)


def analyse(path, info, follow, has_audio):
    """Find faces, follow them and (for follow=speaker) score who is talking.

    Returns per-track arrays aligned to the analysis frames, plus the frames where the shot changes."""
    import cv2

    cv2.setNumThreads(min(4, available_cpus()))
    width, height = info["width"], info["height"]
    dec_w = min(width, DECODE_MAX_WIDTH) // 2 * 2
    dec_h = even(round(height * dec_w / width))
    scale_back = width / dec_w
    det_w = min(DETECT_WIDTH, dec_w)
    det_h = even(round(dec_h * det_w / dec_w))
    det_scale = dec_w / det_w

    detector = cv2.FaceDetectorYN.create(FACE_MODEL, "", (det_w, det_h), 0.6, 0.3, 50)
    audio = read_audio(path) if (follow == "speaker" and has_audio) else np.zeros(0, dtype=np.int16)
    want_crops = follow == "speaker" and audio.size > 0

    proc = subprocess.Popen(
        ["ffmpeg", "-v", "error", "-i", path, "-vf", f"fps={FPS},scale={dec_w}:{dec_h}", "-f", "rawvideo", "-pix_fmt", "bgr24", "-"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=dec_w * dec_h * 3 * 4,
    )
    frame_bytes = dec_w * dec_h * 3
    open_tracks, done = [], []
    cuts = [0]
    prev_hist = None
    recent = []
    n = 0
    shot = 0

    def finish(track):
        if len(track.frames) >= MIN_TRACK:
            if want_crops and _shares_screen(track, done, open_tracks):
                track.scores = score_track(track, audio)
            track.crops = []
            done.append(track)

    try:
        while True:
            buf = proc.stdout.read(frame_bytes)
            if len(buf) < frame_bytes:
                break
            frame = np.frombuffer(buf, np.uint8).reshape(dec_h, dec_w, 3)
            small = cv2.resize(frame, (det_w, det_h))

            hsv = cv2.cvtColor(small, cv2.COLOR_BGR2HSV)
            hist = cv2.calcHist([hsv], [0, 1], None, [16, 8], [0, 180, 0, 256])
            cv2.normalize(hist, hist)
            if prev_hist is not None:
                change = cv2.compareHist(prev_hist, hist, cv2.HISTCMP_BHATTACHARYYA)
                usual = float(np.median(recent)) if recent else 0.0
                if change > CUT_THRESHOLD and change > CUT_RATIO * usual and n - cuts[-1] > FPS // 2:
                    cuts.append(n)
                    shot += 1
                    for t in open_tracks:
                        finish(t)
                    open_tracks = []
                recent = (recent + [change])[-FPS:]
            prev_hist = hist

            _, faces = detector.detect(small)
            used = set()
            for d in ([] if faces is None else faces):
                x, y, w, h = [float(v) * det_scale for v in d[:4]]
                if w < MIN_FACE_PX * dec_w / 1280:
                    continue
                box = (x, y, x + w, y + h)
                best, best_iou = None, 0.3
                for t in open_tracks:
                    if id(t) in used or n - t.last > TRACK_GAP:
                        continue
                    o = _iou(box, t.boxes[-1])
                    if o > best_iou:
                        best, best_iou = t, o
                if best is None:
                    best = Track(len(open_tracks) + len(done), shot)
                    open_tracks.append(best)
                used.add(id(best))
                best.add(n, box, _face_crop(frame, box, cv2) if want_crops else None)

            still = []
            for t in open_tracks:
                (still.append(t) if n - t.last <= TRACK_GAP else finish(t))
            open_tracks = still
            n += 1
    finally:
        proc.stdout.close()
        proc.wait()

    for t in open_tracks:
        finish(t)
    if n == 0:
        raise ReframeError("Could not decode any frames from the video.")

    tracks = []
    for i, t in enumerate(sorted(done, key=lambda t: t.first)):
        cx = np.full(n, np.nan)
        area = np.full(n, np.nan)
        boxes = np.array(t.boxes)
        frames = np.array(t.frames)
        span = np.arange(t.first, t.last + 1)
        centre = (boxes[:, 0] + boxes[:, 2]) / 2 * scale_back
        size = (boxes[:, 2] - boxes[:, 0]) * (boxes[:, 3] - boxes[:, 1])
        cx[span] = np.interp(span, frames, centre)
        area[span] = np.interp(span, frames, size)
        score = np.full(n, np.nan)
        if hasattr(t, "scores"):
            score[span] = t.scores[: len(span)]
        tracks.append({"id": i, "cx": cx, "area": area, "score": score, "shot": t.shot})
    return {"frames": n, "tracks": tracks, "cuts": cuts, "scored": want_crops}


# --------------------------------------------------------------------------- camera path


def _smooth_mean(x, size):
    if size <= 1 or len(x) < 2:
        return x
    half = size // 2
    padded = np.concatenate((np.full(half, x[0]), x, np.full(size - half - 1, x[-1])))
    c = np.cumsum(np.insert(padded, 0, 0.0))
    return (c[size:] - c[:-size]) / size


def _smooth_median(x, size):
    if size <= 1 or len(x) < 2:
        return x
    half = size // 2
    padded = np.concatenate((np.full(half, x[0]), x, np.full(half, x[-1])))
    windows = np.lib.stride_tricks.sliding_window_view(padded, size)
    return np.median(windows, axis=1)


def choose_shots(analysis, follow, settings):
    """Who the camera is on at every analysis frame (-1 = nobody yet), and when it changes."""
    n = analysis["frames"]
    tracks = analysis["tracks"]
    cut_set = set(analysis["cuts"])
    if not tracks or follow == "center":
        return np.full(n, -1)

    present = np.array([np.isfinite(t["cx"]) for t in tracks])
    if follow == "speaker" and analysis["scored"]:
        raw = np.array([np.nan_to_num(t["score"], nan=-10.0) for t in tracks])
        level = np.array([_smooth_mean(r, FPS) for r in raw])
        level = np.where(present, level, -np.inf)
        talking = level > SPEAKING
    else:
        level = np.array([np.nan_to_num(t["area"], nan=0.0) for t in tracks])
        level = np.where(present, level, -np.inf)
        talking = present

    confirm = max(1, int(round(settings["switch_after"] * FPS)))
    hold = int(round(settings["min_hold"] * FPS))
    who = np.full(n, -1)
    cur, last_switch = -1, -10 ** 9
    shot_end = np.empty(n, dtype=int)
    end = n
    for f in range(n - 1, -1, -1):
        shot_end[f] = end
        if f in cut_set:
            end = f
    for f in range(n):
        here = np.flatnonzero(present[:, f])
        if f in cut_set or cur == -1 or not present[cur, f] and here.size:
            # new shot, nothing chosen yet, or the face we were on has gone: take the best face here now
            if here.size:
                cand = [i for i in here if talking[i, f]]
                if not cand:
                    # nobody on screen is talking yet (an off-screen voice, or a pause): start on
                    # whoever in this shot talks first, not on a face that happens to score highest
                    nxt = _next_speaker(f, shot_end[f], here, talking, confirm)
                    cand = [nxt] if nxt is not None else list(here)
                best = max(cand, key=lambda i: level[i, f])
                if best != cur:
                    cur, last_switch = best, f
        else:
            cand = [i for i in here if talking[i, f]]
            if cand:
                best = max(cand, key=lambda i: level[i, f])
                if best != cur and f - last_switch >= hold and talking[best, f:f + confirm].all():
                    cur, last_switch = best, f
        who[f] = cur
    return who


def _next_speaker(start, end, here, talking, confirm):
    """The first of `here` (faces on screen now) to talk for `confirm` frames between start and end."""
    for f in range(start, end):
        for i in here:
            if talking[i, f:min(f + confirm, end)].all():
                return i
    return None


def camera_path(analysis, who, info, crop, settings):
    """Left edge of the crop (source pixels) for every analysis frame."""
    n = analysis["frames"]
    width = info["width"]
    centre = width / 2
    x = np.full(n, centre)
    tracks = analysis["tracks"]
    last = centre
    for f in range(n):
        w = who[f]
        if w >= 0 and np.isfinite(tracks[w]["cx"][f]):
            last = tracks[w]["cx"][f]
        x[f] = last
    breaks = sorted(set([0, n] + list(np.flatnonzero(np.diff(who)) + 1) + [c for c in analysis["cuts"] if 0 < c < n]))
    smooth = max(1, int(round(settings["smoothing"] * FPS)))
    for a, b in zip(breaks[:-1], breaks[1:]):
        x[a:b] = _smooth_mean(_smooth_median(x[a:b], 13), smooth)
    left = np.clip(np.round(x - crop["width"] / 2), 0, width - crop["width"]).astype(int)
    return left


def path_points(left):
    points = []
    prev = None
    for f, v in enumerate(left):
        if v != prev:
            points.append({"time": round(f / FPS, 3), "x": int(v)})
            prev = v
    return points


def speaker_segments(who, cuts):
    segs = []
    n = len(who)
    breaks = sorted(set([0, n] + list(np.flatnonzero(np.diff(who)) + 1)))
    for a, b in zip(breaks[:-1], breaks[1:]):
        segs.append({"start": round(a / FPS, 2), "end": round(b / FPS, 2), "face": int(who[a])})
    return segs


# --------------------------------------------------------------------------- render


def write_sendcmd(points, crop, path):
    with open(path, "w") as fh:
        for p in points:
            fh.write(f"{p['time']:.3f} crop x {int(p['x'])};\n")


def render(src, out, crop, points, out_w, out_h, cmd_path, has_audio):
    x0 = int(points[0]["x"]) if points else 0
    chain = f"crop={crop['width']}:{crop['height']}:{x0}:{crop['y']}"
    if len(points) > 1:
        write_sendcmd(points, crop, cmd_path)
        chain = f"sendcmd=f={cmd_path}," + chain
    chain += f",scale={out_w}:{out_h}:flags=lanczos,setsar=1"
    cmd = ["ffmpeg", "-v", "error", "-y", "-i", src, "-filter_complex", f"[0:v]{chain}[v]", "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "192k"]
    cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-movflags", "+faststart", out]
    run = subprocess.run(cmd, capture_output=True, text=True)
    if run.returncode != 0:
        raise ReframeError(f"FFmpeg could not make the video: {run.stderr.strip()[-500:]}")


def output_size(aspect, output_width):
    aw, ah = ASPECTS[aspect]
    w = even(output_width)
    return w, even(round(w * ah / aw))


def clean_path(path, crop, width):
    """A camera path someone passed in: sorted by time, x kept inside the frame."""
    points = []
    for p in sorted(path, key=lambda p: float(p["time"])):
        x = int(round(float(p["x"])))
        x = max(0, min(width - crop["width"], x))
        t = max(0.0, float(p["time"]))
        if points and abs(points[-1]["time"] - t) < 1e-6:
            points[-1] = {"time": t, "x": x}
        else:
            points.append({"time": round(t, 3), "x": x})
    if points and points[0]["time"] > 0:
        points.insert(0, {"time": 0.0, "x": points[0]["x"]})
    return points


def reframe_video(src, out, work_dir, options=None):
    """Reframe the local video at src into out. Returns what happened (no file upload)."""
    settings = dict(DEFAULTS)
    settings.update({k: v for k, v in (options or {}).items() if v is not None})
    aspect, follow = settings["aspect"], settings["follow"]
    if aspect not in ASPECTS:
        raise ReframeError(f"Unknown shape {aspect}.")
    if follow not in ("speaker", "largest_face", "center"):
        raise ReframeError(f"Unknown follow mode {follow}.")

    started = time.perf_counter()
    info = probe(src)
    crop = crop_box(info["width"], info["height"], aspect)
    out_w, out_h = output_size(aspect, settings["output_width"])
    # The plain numbers first: apps that show a few facts from an answer show these.
    result = {
        "faces": 0,
        "switches": 0,
        "camera_cuts": 0,
        "note": None,
        "duration": round(info["duration"], 3),
        "follow": follow,
        "aspect": aspect,
        "width": out_w,
        "height": out_h,
        "source": {"width": info["width"], "height": info["height"]},
        "crop": {"width": crop["width"], "height": crop["height"]},
        "speakers": [],
    }

    centre_x = (info["width"] - crop["width"]) // 2
    if settings.get("path"):
        points = clean_path(settings["path"], crop, info["width"]) if crop["moves"] else [{"time": 0.0, "x": 0}]
        result["note"] = "Used the camera path that was given."
    elif not crop["moves"] or follow == "center":
        points = [{"time": 0.0, "x": centre_x if crop["moves"] else 0}]
        if not crop["moves"]:
            result["note"] = "The video is already this shape or narrower, so it was trimmed in the middle."
    else:
        analysis = analyse(src, info, follow, info["has_audio"])
        who = choose_shots(analysis, follow, settings)
        left = camera_path(analysis, who, info, crop, settings)
        points = path_points(left)
        result["faces"] = len(analysis["tracks"])
        result["speakers"] = [s for s in speaker_segments(who, analysis["cuts"])]
        result["switches"] = max(0, sum(1 for s in result["speakers"] if s["face"] >= 0) - 1)
        result["camera_cuts"] = len(analysis["cuts"]) - 1
        if not analysis["tracks"]:
            result["note"] = "No faces were found, so the frame stayed in the middle."
        elif follow == "speaker" and not analysis["scored"]:
            result["note"] = "The video has no sound, so it followed the largest face."
    result["analysis_seconds"] = round(time.perf_counter() - started, 1)

    t0 = time.perf_counter()
    render(src, out, crop, points, out_w, out_h, os.path.join(work_dir, "camera.txt"), info["has_audio"])
    result["render_seconds"] = round(time.perf_counter() - t0, 1)
    result["path"] = points
    return result
