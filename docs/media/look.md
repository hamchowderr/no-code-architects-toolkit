# Look at media with CLIP

`POST /v1/media/look` turns frames of a video, pictures and words into CLIP vectors (OpenAI CLIP ViT-B/32, MIT, run on CPU with ONNX Runtime). A picture and the words that describe it land close together, so comparing vectors finds footage by what it shows. This is what the dashboard uses to find B-Roll.

## Request

| Field | Type | Notes |
|---|---|---|
| `media_url` | URL | A video or a picture. Frames are sampled every `every` seconds (default 2), at most 40. |
| `every` | number | 0.5 to 60 seconds between frames. A long video is spread over 40 frames instead. |
| `cuts` | boolean | Also find the video's camera cuts (`media.cuts`, in seconds), as `/v1/video/reframe` does, and slow dissolves from one shot to the next (counted halfway through). Reads the whole video at 10 frames a second, small; about 3 s for a minute of video. |
| `frames` | boolean | `false` skips the frames, for a request that only wants the cuts. Default `true`. |
| `image_urls` | URL[] | Up to 60 pictures, such as stock thumbnails. A link that fails gets an `error` and no vector. |
| `texts` | string[] | Up to 200 texts. Longer than CLIP's 77 word pieces is cut. |
| `webhook_url`, `id` | | As on every endpoint. |

At least one of `media_url`, `image_urls` or `texts` is needed.

```json
{ "media_url": "https://example.com/garage.mp4", "texts": ["a garage", "a coffee cup"] }
```

## Response

```json
{
  "model": "clip-vit-b-32",
  "media": {
    "duration": 8.0, "width": 1280, "height": 720,
    "frames": [{ "time": 1.0, "face": 0.01, "vector": [0.0185, ...] }],
    "cuts": [5.3, 7.6]
  },
  "pictures": [{ "url": "https://...", "vector": [...] }],
  "texts": [{ "text": "a garage", "vector": [...] }]
}
```

Every vector has 512 numbers and length 1, so the dot product of two vectors is how alike they are (CLIP scores for a good match are usually 0.25 to 0.35). `face` is how much of the frame the largest face covers, 0 to 1 (YuNet, as in `/v1/video/reframe`), which tells a talking head from a cutaway. `cuts` is there only when asked for. The dashboard uses it so a cutaway lines up with the video's own shots instead of landing in the middle of a short one.

The model files are fetched at build by `models/look/fetch.py` (about 150 MB, checked by SHA-256).
