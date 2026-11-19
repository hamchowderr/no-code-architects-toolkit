# Models for `/v1/media/look`

| File | What it does | Where it comes from | Licence |
|---|---|---|---|
| `vision_model_quantized.onnx` (89 MB) | Turns a picture into a vector | [openai/CLIP](https://github.com/openai/CLIP) ViT-B/32, as converted to ONNX and quantized by [Xenova/clip-vit-base-patch32](https://huggingface.co/Xenova/clip-vit-base-patch32) | MIT, see `LICENSE-CLIP` |
| `text_model_quantized.onnx` (65 MB) | Turns words into a vector in the same space | same | MIT |
| `tokenizer.json` (2 MB) | CLIP's word pieces | same | MIT |

They are not kept in git. `fetch.py` downloads them at a fixed revision and checks each file's SHA-256; the Docker build runs it, and so do the tests. A picture and the words that describe it end up close together, which is how the dashboard finds B-Roll that shows what is being said.

The model runs on CPU through ONNX Runtime, which the toolkit already has. No PyTorch.
