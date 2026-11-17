"""
faster-whisper replaces openai-whisper. Callers and API responses must keep the shape
openai-whisper's model.transcribe() gave them.
"""

import json
import os
from types import SimpleNamespace

import pytest

from services import whisper_engine

from conftest import ROOT

SAMPLE = os.path.join(ROOT, "tests", "data", "jfk.ogg")  # 11 s, public-domain JFK speech


def fake_segment(i, start, end, text, words):
    return SimpleNamespace(
        id=i, seek=0, start=start, end=end, text=text, tokens=[1, 2, 3], temperature=0.0,
        avg_logprob=-0.2, compression_ratio=1.1, no_speech_prob=0.01,
        words=[SimpleNamespace(word=w, start=s, end=e, probability=0.9) for w, s, e in words],
    )


class FakeModel:
    instances = 0

    def __init__(self, size, **kwargs):
        FakeModel.instances += 1
        self.size = size
        self.kwargs = kwargs
        self.calls = []

    def transcribe(self, path, **options):
        self.calls.append((path, options))
        segments = [
            fake_segment(0, 0.0, 1.5, " Hello there.", [(" Hello", 0.0, 0.6), (" there.", 0.6, 1.5)]),
            fake_segment(1, 1.5, 3.0, " General Kenobi.", [(" General", 1.5, 2.2), (" Kenobi.", 2.2, 3.0)]),
        ]
        return iter(segments), SimpleNamespace(language="en")


@pytest.fixture
def fake_model(monkeypatch):
    import faster_whisper

    FakeModel.instances = 0
    monkeypatch.setattr(faster_whisper, "WhisperModel", FakeModel)
    monkeypatch.setattr(whisper_engine, "_models", {})
    for name in ["WHISPER_MODEL", "WHISPER_DEVICE", "WHISPER_COMPUTE_TYPE", "WHISPER_BEAM_SIZE", "WHISPER_VAD_FILTER"]:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(whisper_engine, "_cuda_available", lambda: False)
    return FakeModel


def test_result_has_openai_whisper_shape(fake_model):
    result = whisper_engine.transcribe("a.mp3", word_timestamps=True)
    assert result["text"] == " Hello there. General Kenobi."
    assert result["language"] == "en"
    seg = result["segments"][0]
    assert set(seg) == {"id", "seek", "start", "end", "text", "tokens", "temperature", "avg_logprob",
                        "compression_ratio", "no_speech_prob", "words"}
    assert seg["words"][0] == {"word": " Hello", "start": 0.0, "end": 0.6, "probability": 0.9}
    json.dumps(result)  # plain data, ready for a JSON response


def test_words_only_when_asked(fake_model):
    result = whisper_engine.transcribe("a.mp3")
    assert "words" not in result["segments"][0]


def test_options_passed_through(fake_model):
    whisper_engine.transcribe("a.mp3", task="translate", language="fr", word_timestamps=True)
    model = next(iter(whisper_engine._models.values()))
    _, options = model.calls[0]
    assert options == {"task": "translate", "word_timestamps": True, "beam_size": 1, "vad_filter": False, "language": "fr"}


def test_auto_language_is_left_to_detection(fake_model):
    whisper_engine.transcribe("a.mp3", language="auto")
    model = next(iter(whisper_engine._models.values()))
    assert "language" not in model.calls[0][1]


def test_model_loads_once_per_process(fake_model):
    for _ in range(3):
        whisper_engine.transcribe("a.mp3")
    assert fake_model.instances == 1


def test_defaults_to_base_int8_on_cpu(fake_model):
    whisper_engine.transcribe("a.mp3")
    model = next(iter(whisper_engine._models.values()))
    assert model.size == "base"
    assert model.kwargs["device"] == "cpu"
    assert model.kwargs["compute_type"] == "int8"


def test_gpu_uses_float16(fake_model, monkeypatch):
    monkeypatch.setattr(whisper_engine, "_cuda_available", lambda: True)
    assert whisper_engine.resolve_settings() == ("base", "cuda", "float16")


def test_settings_from_environment(fake_model, monkeypatch):
    monkeypatch.setenv("WHISPER_MODEL", "small")
    monkeypatch.setenv("WHISPER_COMPUTE_TYPE", "int8_float32")
    monkeypatch.setenv("WHISPER_BEAM_SIZE", "1")
    monkeypatch.setenv("WHISPER_VAD_FILTER", "true")
    whisper_engine.transcribe("a.mp3")
    model = next(iter(whisper_engine._models.values()))
    assert model.size == "small"
    assert model.kwargs["compute_type"] == "int8_float32"
    assert model.calls[0][1]["beam_size"] == 1
    assert model.calls[0][1]["vad_filter"] is True


# --- The services that call it ---------------------------------------------------------

def test_media_transcribe_service_srt_and_files(fake_model, monkeypatch, tmp_path):
    from services.v1.media import media_transcribe

    src = tmp_path / "in.mp3"
    src.write_bytes(b"x")
    monkeypatch.setattr(media_transcribe, "download_file", lambda url, dest: str(src))
    monkeypatch.setattr(media_transcribe, "LOCAL_STORAGE_PATH", str(tmp_path))

    text, srt_text, segments = media_transcribe.process_transcribe_media(
        "https://x.test/a.mp3", "transcribe", True, True, True, False, "direct", None, "job1", words_per_line=2)
    assert text == " Hello there. General Kenobi."
    assert "Hello there." in srt_text and "General Kenobi." in srt_text
    assert len(segments) == 2

    # Files, without the text file (this used to fail with a NameError).
    src.write_bytes(b"x")
    text_file, srt_file, seg_file = media_transcribe.process_transcribe_media(
        "https://x.test/a.mp3", "transcribe", False, True, True, False, "cloud", None, "job2")
    assert text_file is None
    assert open(srt_file).read().startswith("1\n")
    assert json.load(open(seg_file))[1]["text"] == " General Kenobi."  # real JSON now


def test_caption_service_gets_word_timestamps(fake_model):
    from services import ass_toolkit

    result = ass_toolkit.generate_transcription("a.mp4", language="auto")
    assert result["segments"][0]["words"][1]["word"] == " there."


# --- The real library ------------------------------------------------------------------

@pytest.fixture
def real_engine(monkeypatch):
    monkeypatch.setattr(whisper_engine, "_models", {})
    monkeypatch.setenv("WHISPER_MODEL", os.environ.get("TEST_WHISPER_MODEL", "tiny"))
    monkeypatch.setenv("WHISPER_DEVICE", "cpu")
    try:
        whisper_engine.get_model()
    except Exception as e:  # no network for the model download
        pytest.skip(f"could not load a faster-whisper model: {e}")


def test_real_transcription_of_speech(real_engine):
    result = whisper_engine.transcribe(SAMPLE, word_timestamps=True)
    text = result["text"].lower()
    assert result["language"] == "en"
    assert "ask not what your country can do for you" in text.replace(",", "")
    words = [w for s in result["segments"] for w in s["words"]]
    assert len(words) > 15
    assert all(w["start"] <= w["end"] for w in words)
    assert words[-1]["end"] <= 11.5


def test_real_translation_task_runs(real_engine):
    result = whisper_engine.transcribe(SAMPLE, task="translate", language="en")
    assert "country" in result["text"].lower()
