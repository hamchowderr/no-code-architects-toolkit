"""Downloads the CLIP model files used by /v1/media/look and checks them.

The files are too big to keep in git (about 150 MB), so the Docker build runs this once
and tests run it before the look tests. Run: python models/look/fetch.py
"""

import hashlib
import os
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
# Xenova/clip-vit-base-patch32 on Hugging Face (OpenAI CLIP ViT-B/32, MIT), quantized ONNX, at a fixed revision.
REVISION = "6b7c523703ae26e39be4b03774966f8571c7f523"
BASE = f"https://huggingface.co/Xenova/clip-vit-base-patch32/resolve/{REVISION}/"
FILES = {
    "vision_model_quantized.onnx": ("onnx/vision_model_quantized.onnx", "583fd1110a514667812fee7d684952aaf82a99b959760c8d7dca7e0ab9839299"),
    "text_model_quantized.onnx": ("onnx/text_model_quantized.onnx", "73baab855d406190da9faa498cfedf65f15cf309f4cc7385b7b032e6d08e5c3a"),
    "tokenizer.json": ("tokenizer.json", "f7f3b7af117d467b58374797691a6438d3e6b9e9cef800dfd5dced7f697a90cd"),
}


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch(target_dir=HERE):
    os.makedirs(target_dir, exist_ok=True)
    for name, (remote, expected) in FILES.items():
        path = os.path.join(target_dir, name)
        if os.path.exists(path) and sha256(path) == expected:
            continue
        tmp = path + ".part"
        urllib.request.urlretrieve(BASE + remote, tmp)
        got = sha256(tmp)
        if got != expected:
            os.remove(tmp)
            raise RuntimeError(f"{name}: checksum {got} does not match {expected}")
        os.replace(tmp, path)
    return target_dir


if __name__ == "__main__":
    print(fetch(sys.argv[1] if len(sys.argv) > 1 else HERE))
