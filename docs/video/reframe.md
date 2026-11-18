# Reframe to tall, following the speaker (`/v1/video/reframe`)

Only in this fork. Turns a wide video into a tall (9:16), square (1:1) or 4:5 one. The frame follows whoever is talking: it holds on them, drifts gently if they move, and cuts across like a camera when someone else starts talking.

Everything runs on the toolkit's CPU. No GPU and no outside service.

## How it decides where to look

1. Faces are found 25 times a second with OpenCV's YuNet and followed from frame to frame. Tracking restarts at camera cuts in the source, so edited footage works.
2. For each face, a small speaker model (LR-ASD) checks whether the mouth matches the sound.
3. The camera switches to a new speaker once they have talked for `switch_after` seconds, never more often than every `min_hold` seconds. When nobody on screen is talking (an off-camera voice, music, silence) it stays where it is.
4. FFmpeg crops along that path and scales to the output size.

With `follow: "largest_face"` the speaker model is skipped and the camera stays on the biggest face. `follow: "center"` just crops the middle. A video with no sound follows the largest face.

## Request

`POST /v1/video/reframe` with the `X-API-Key` header.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `video_url` | string (required) | | The video to reframe |
| `aspect` | `"9:16"`, `"1:1"`, `"4:5"`, `"16:9"` | `"9:16"` | Shape of the result |
| `follow` | `"speaker"`, `"largest_face"`, `"center"` | `"speaker"` | What the frame follows |
| `switch_after` | number, 0 to 10 | `1.0` | Seconds someone must talk before the camera cuts to them |
| `min_hold` | number, 0 to 30 | `1.5` | Never cut more often than this many seconds |
| `smoothing` | number, 0 to 10 | `1.5` | Seconds over which movement within one shot is smoothed |
| `output_width` | integer, 144 to 4320 | `1080` | Width of the result; height follows from `aspect` |
| `path` | array of `{time, x}` | | A camera path to use instead of working one out (for example the `path` from an earlier answer, corrected). `x` is the left edge of the crop in source pixels. |
| `webhook_url` | string | | Send the answer here when done (recommended: analysis takes about 0.5 to 0.8 times the video's length on 4 CPUs) |
| `id` | string | | Your own id, sent back |

```json
{
  "video_url": "https://example.com/interview-clip.mp4",
  "aspect": "9:16",
  "follow": "speaker",
  "webhook_url": "https://example.com/hook"
}
```

## Answer

```json
{
  "code": 200,
  "response": {
    "file_url": "https://bucket.example.com/1b2c..._reframed.mp4",
    "width": 1080, "height": 1920,
    "aspect": "9:16", "follow": "speaker",
    "source": {"width": 1280, "height": 720},
    "crop": {"width": 404, "height": 720},
    "duration": 120.1,
    "faces": 3,
    "switches": 1,
    "camera_cuts": 0,
    "speakers": [{"start": 0.0, "end": 60.28, "face": 2}, {"start": 60.28, "end": 120.12, "face": 0}],
    "path": [{"time": 0.0, "x": 811}, {"time": 60.28, "x": 92}],
    "note": null,
    "analysis_seconds": 65.6,
    "render_seconds": 32.3
  }
}
```

- `speakers`: who the camera was on and when. `face` numbers the faces in the order they first appeared (`-1` means no face yet).
- `path`: the crop's left edge over time, only where it changes. Pass it back as `path` to render again without analysing.
- `note`: says when something simpler was done, such as no faces found (stays in the middle) or no sound (follows the largest face).

A source that is already as tall as the requested shape is trimmed in the middle and not tracked.

## Errors

`400` when the request is not valid or the file has no video in it; `500` for anything else, with the reason in `message`.

## Measured speed

On a 4-core cloud CPU, a 120-second 720p clip with three people: analysis 66 s, render 32 s.
