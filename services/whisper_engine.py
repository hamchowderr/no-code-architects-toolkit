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

"""
Speech to text with faster-whisper (CTranslate2).

faster-whisper runs the same Whisper models as openai-whisper, several times faster and
with less memory, and without PyTorch. `transcribe()` returns the same dictionary shape
openai-whisper's `model.transcribe()` did ({"text", "segments", "language"}, with
"words" on each segment when word timestamps are asked for), so every caller and every
API response keeps its shape.

Settings (environment variables):
  WHISPER_MODEL         Model size or path. Default "base" (what the toolkit always used).
  WHISPER_DEVICE        "auto" (default: GPU when CUDA is present, else CPU), "cpu" or "cuda".
  WHISPER_COMPUTE_TYPE  Default "int8" on CPU and "float16" on GPU.
  WHISPER_CPU_THREADS   Threads per transcription. Default 0 (CTranslate2 decides).
  WHISPER_BEAM_SIZE     Default 1 (greedy, the decoding openai-whisper used). 5 is a little
                        more accurate and about twice as slow.
  WHISPER_VAD_FILTER    "true" skips long silences before decoding. Default off.
  WHISPER_CACHE_DIR     Where models are downloaded and kept.

Models are loaded once per process and reused, so only the first job in a process pays
the load time.
"""

import logging
import os
import threading

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "base"

_models = {}
_models_lock = threading.Lock()


def _env_bool(name, default=False):
    value = os.environ.get(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _cuda_available():
    try:
        import ctranslate2

        return ctranslate2.get_cuda_device_count() > 0
    except Exception:
        return False


def resolve_settings(model_size=None):
    """Returns (model_size, device, compute_type) from the arguments and environment."""
    size = model_size or os.environ.get("WHISPER_MODEL") or DEFAULT_MODEL
    device = (os.environ.get("WHISPER_DEVICE") or "auto").lower()
    if device == "auto":
        device = "cuda" if _cuda_available() else "cpu"
    compute_type = os.environ.get("WHISPER_COMPUTE_TYPE") or ("float16" if device == "cuda" else "int8")
    return size, device, compute_type


def get_model(model_size=None):
    """Loads a model once per process and hands back the same one afterwards."""
    size, device, compute_type = resolve_settings(model_size)
    key = (size, device, compute_type)
    model = _models.get(key)
    if model is not None:
        return model
    with _models_lock:
        model = _models.get(key)
        if model is None:
            from faster_whisper import WhisperModel

            logger.info(f"Loading faster-whisper model {size} on {device} ({compute_type})")
            model = WhisperModel(
                size,
                device=device,
                compute_type=compute_type,
                cpu_threads=int(os.environ.get("WHISPER_CPU_THREADS") or 0),
                download_root=os.environ.get("WHISPER_CACHE_DIR") or None,
            )
            _models[key] = model
    return model


def _word_dict(word):
    return {
        "word": word.word,
        "start": round(float(word.start), 3),
        "end": round(float(word.end), 3),
        "probability": float(word.probability),
    }


def _segment_dict(segment, word_timestamps):
    data = {
        "id": segment.id,
        "seek": segment.seek,
        "start": round(float(segment.start), 3),
        "end": round(float(segment.end), 3),
        "text": segment.text,
        "tokens": list(segment.tokens),
        "temperature": segment.temperature,
        "avg_logprob": segment.avg_logprob,
        "compression_ratio": segment.compression_ratio,
        "no_speech_prob": segment.no_speech_prob,
    }
    if word_timestamps:
        data["words"] = [_word_dict(w) for w in (segment.words or [])]
    return data


def transcribe(media_path, task="transcribe", language=None, word_timestamps=False, model_size=None):
    """
    Transcribes (or translates to English) an audio or video file.

    Returns {"text": str, "segments": [dict], "language": str}, the shape openai-whisper's
    model.transcribe() returned.
    """
    model = get_model(model_size)
    options = {
        "task": task or "transcribe",
        "word_timestamps": bool(word_timestamps),
        "beam_size": int(os.environ.get("WHISPER_BEAM_SIZE") or 1),
        "vad_filter": _env_bool("WHISPER_VAD_FILTER"),
    }
    if language and language != "auto":
        options["language"] = language

    segments_iter, info = model.transcribe(media_path, **options)
    # The segments come from a generator: decoding happens while it is read.
    segments = [_segment_dict(s, word_timestamps) for s in segments_iter]
    return {
        "text": "".join(s["text"] for s in segments),
        "segments": segments,
        "language": info.language,
    }
