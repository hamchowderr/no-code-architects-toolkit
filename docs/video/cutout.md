# Cut out the speaker (`/v1/video/cutout`)

Only in this fork. Finds the person in every frame of a video, with no green screen, and hands back their **mask**: a video the same length and frame rate, white where the person is and black elsewhere, soft at the edge.

Make the mask once per video and keep it. Every effect that needs the person cut out takes it as `mask_url`:

- [`/v1/video/motion`](motion.md): pieces behind the speaker (`"layer": "behind"`) and sticker outlines.
- [`/v1/video/background`](background.md): a new background behind the speaker.

Both also make the mask themselves when none is given, and send it back.

## How it works

The person is found by Google's [MediaPipe Selfie Segmenter](https://ai.google.dev/edge/mediapipe/solutions/vision/image_segmenter#selfie-model) (Apache-2.0, in `models/cutout`), run with LiteRT on the toolkit's CPUs. It looks at each frame at 256 x 256, so it is fast but the edge is soft: fine hair and fingers can blur, and things the person holds or leans on may be counted as part of them. Each frame's mask carries a little of the last one's, so the edge does not flicker.

Phone videos filmed on their side are turned the right way up first. The mask is stored no larger than 960 pixels on its long side; FFmpeg scales it up to the video when it is used.

## Request

`POST /v1/video/cutout` with the `X-API-Key` header.

| Field | Type | Meaning |
|---|---|---|
| `video_url` | string (required) | The video |
| `webhook_url` | string | Send the answer here when done (recommended) |
| `id` | string | Your own id, sent back |

## Answer

```json
{"mask_url": "https://.../<job>_mask.mp4", "frames": 1740, "fps": 29.998, "width": 540, "height": 960, "duration": 58.003, "person_share": 0.553, "seconds": 20.0}
```

`person_share` is how much of the picture the person fills, on average. Close to 0 means nobody was found.

A file with no picture, or longer than 3 hours, answers 400 with a plain message.

## Speed

On 4 CPUs a 58-second 1080 x 1920 phone video is cut out in about 20 seconds, roughly 3 times faster than real time.
