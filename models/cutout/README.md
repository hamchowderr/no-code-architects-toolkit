# Model for `/v1/video/cutout`

| File | What it does | Where it comes from | Licence |
|---|---|---|---|
| `selfie_segmenter.tflite` (244 KB) | Says, for each pixel of a 256 x 256 picture, how likely it is to be part of a person | [MediaPipe Selfie Segmenter](https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter#selfie-model), float16, `storage.googleapis.com/mediapipe-models/image_segmenter/selfie_segmenter/float16/latest/selfie_segmenter.tflite` (published 2023-05-07), unchanged. SHA-256 `191ac9529ae506ee0beefa6b2c945a172dab9d07d1e802a290a4e4038226658b` | Apache-2.0, see `LICENSE-MediaPipe` |

It runs with LiteRT (`ai-edge-litert`, Apache-2.0), Google's runtime for `.tflite` files, without the rest of MediaPipe. On 4 CPU cores it cuts out a 1080 x 1920 phone video at about 1.4 times real time.

Models that were looked at and left out: Robust Video Matting (GPL-3.0, cannot ship in this GPL-2.0-or-later toolkit) and MatAnyone (its own non-commercial licence). MODNet (Apache-2.0) gives finer hair and could replace this file later.
