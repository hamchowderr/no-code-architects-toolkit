# New background (`/v1/video/background`)

Only in this fork. Puts a new background behind the speaker, with no green screen: their own background blurred, a colour, a picture or a video.

The speaker is cut out with their mask from [`/v1/video/cutout`](cutout.md). Pass it as `mask_url`; without one the toolkit makes it first and sends it back as `mask_url`.

## Request

`POST /v1/video/background` with the `X-API-Key` header.

| Field | Type | Meaning |
|---|---|---|
| `video_url` | string (required) | The video |
| `background.type` | `"blur"`, `"color"`, `"image"` or `"video"` (required) | What goes behind the speaker |
| `background.color` | `#rrggbb` | For `"color"` |
| `background.url` | string | For `"image"` and `"video"`. Scaled to fill the frame; a shorter video loops |
| `background.blur` | number, 2 to 80 | For `"blur"`: how strong (default 24) |
| `mask_url` | string | The speaker's mask, if already made |
| `webhook_url` | string | Send the answer here when done (recommended) |
| `id` | string | Your own id, sent back |

## Example

```json
{
  "video_url": "https://example.com/talk.mp4",
  "mask_url": "https://example.com/talk_mask.mp4",
  "background": {"type": "image", "url": "https://example.com/studio.jpg"}
}
```

## Answer

```json
{"file_url": "https://.../<job>_background.mp4", "duration": 58.003, "seconds": 53.0}
```

The video's sound is kept. The mask is soft at the edge (see [`/v1/video/cutout`](cutout.md)), so a background very different from the real one can show a faint halo around hair.
