# Models for `/v1/video/reframe`

| File | What it does | Where it comes from | Licence |
|---|---|---|---|
| `face_detection_yunet_2023mar.onnx` (232 KB) | Finds faces | [opencv/face_detection_yunet](https://huggingface.co/opencv/face_detection_yunet) (OpenCV Zoo), unchanged | MIT, see `LICENSE-YuNet` |
| `lr_asd_pretrain_ava.onnx` (3.4 MB) | Says which face is talking, from the face and the sound | [airmeet/LR-ASD](https://github.com/airmeet/LR-ASD) at commit `8893a2f`, weights `weight/pretrain_AVA.model`, converted to ONNX with `export_lr_asd.py` | MIT, see `LICENSE-LR-ASD` |

The conversion lets the toolkit run the speaker model with ONNX Runtime instead of PyTorch. The ONNX file gives the same scores as the PyTorch model (largest difference 3e-6 on random inputs of 7 to 100 frames). The only change is that the audio encoder's two `MaxPool3d` layers, which LR-ASD applies to a 4-dimensional tensor, are written as the equivalent `MaxPool2d` so they export.

LR-ASD's other weights file, `finetuning_TalkSet.model`, is deliberately not used: TalkSet is built from VoxCeleb2 and LRS3, whose terms have not been checked for commercial use.

To convert again: clone LR-ASD to `/tmp/ft/lrasd`, `pip install torch onnx onnxruntime`, then `python export_lr_asd.py`.
