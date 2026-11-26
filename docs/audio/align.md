# Line up two recordings

`POST /v1/audio/align` finds where one recording sits against another of the same moment, such as a separate mic recording against the camera's own sound, from the sound alone. No AI, no clapperboard, and nothing to download:

1. FFmpeg decodes both to one channel.
2. Rough: how the loudness rises and falls every 10 ms is cross-correlated over every shift, so a lav mic and a camera mic that sound different still line up on the same words. `confidence` says how far the best shift stands above the next best one.
3. Exact: around that shift the sound itself is compared with GCC-PHAT, to well under a millisecond.
4. Drift: the exact step is done again near the end of what both recorded. Two devices' clocks run at slightly different speeds (tens of parts per million), which adds up to a frame or more over a long take.

A few minutes of sound takes a second or two.

## Request

| Field | Type | Notes |
|---|---|---|
| `reference_url` | URL | The recording to line up against, such as the camera video. |
| `media_url` | URL | The recording to line up, such as the mic's own file. |
| `max_seconds` | number | Read at most this much of each, 10 to 7200 seconds. Default 1800. |
| `max_shift` | number | The furthest apart the two may have started, 1 to 3600 seconds. Default 600. |
| `webhook_url`, `id` | | As on every endpoint. |

```json
{ "reference_url": "https://example.com/camera.mp4", "media_url": "https://example.com/mic.wav" }
```

## Response

```json
{
  "matched": true,
  "offset": 3.21704,
  "drift": 0.00004987,
  "confidence": 0.82,
  "overlap": 612.4,
  "reference_duration": 612.4,
  "other_duration": 640.1
}
```

- The sound at `t` seconds into the reference is at `t * (1 + drift) + offset` seconds into the other recording.
- `offset` is positive when the other recording started first. `drift` is 0 when the two recorded together for under 80 seconds, or no drift was found.
- `matched` is false (and `offset` null) when the two do not share enough sound to be lined up with any trust, such as two different takes.
- `overlap` is how long both recorded together, in seconds.
- A file with no sound at all is answered with 400.
