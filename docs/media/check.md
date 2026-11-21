# Check footage before editing

`POST /v1/media/check` reads a video (or a sound file) with FFmpeg in one pass and says what it is like, so a dashboard can catch problem footage before anyone edits it. No AI: it only measures, and the caller decides what counts as a problem.

## Request

| Field | Type | Notes |
|---|---|---|
| `media_url` | URL | The file to check. |
| `max_seconds` | number | Read at most this much, 1 to 7200 seconds. Default 1200. |
| `webhook_url`, `id` | | As on every endpoint. |

```json
{ "media_url": "https://example.com/jane-hook01.mp4" }
```

## Response

```json
{
  "duration": 7.0, "has_video": true, "has_audio": true,
  "width": 1080, "height": 1920, "rotation": -90, "fps": 30.0, "audio_channels": 2,
  "checked_seconds": 7.0,
  "black": [{ "start": 0.0, "end": 2.0 }],
  "frozen": [{ "start": 4.0, "end": 7.0 }],
  "loudness_lufs": -24.1, "peak_db": -1.2, "peak_count": 2, "samples": 309248
}
```

- `width` and `height` are as the video plays: a phone that filmed upright and stored the picture sideways with a `rotation` comes back upright.
- `black` holds stretches of half a second or more where 98% of the picture is black (FFmpeg `blackdetect`).
- `frozen` holds stretches of two seconds or more where the picture does not change (`freezedetect`). A black stretch is frozen too.
- `loudness_lufs` is the integrated loudness (EBU R128). Speech for social video is usually around -14 to -24; below about -35 is hard to hear. It is `null` when the sound is silent.
- `peak_db` is the loudest sample (0 is full scale) and `peak_count` how many times the sound hit it, out of `samples` per channel. Sound that hits 0 dB many times is clipped.
- Without a sound stream the sound fields are left out, and without a picture the picture ones are.

The picture is scaled to 320 wide for the black and frozen checks, so a minute of 4K reads in a few seconds.
