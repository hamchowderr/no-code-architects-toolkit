# Motion graphics and sound effects (`/v1/video/motion`)

Only in this fork. Lays designed motion pieces (lower thirds, title cards, keyword slams, stat cards, chapter cards, follow cards, quotes, transitions, a logo) and sound effects over a video.

Everything runs on the toolkit itself. The pieces are drawn by [HyperFrames](https://github.com/heygen-com/hyperframes) (Apache-2.0) in the Chromium the toolkit already has, animated with plain CSS. Sounds are CC0 (see `services/v1/video/motion/sounds/LICENSE.md`). No outside service is used.

## How it works

1. All the pieces of a request are laid end to end on one see-through "sheet" and drawn in one go (ProRes 4444 with alpha). Only the seconds that have a piece are drawn, so a long video with a few pieces is quick.
2. FFmpeg cuts each piece back out of the sheet and lays it over the video at its time.
3. Each piece plays a sound as it comes in (unless told not to). Extra sounds can be placed on their own. Sounds are mixed under the video's own sound without changing its loudness.

A request with only sounds keeps the picture as it is (no re-encode).

### Behind the speaker

A piece with `"layer": "behind"` is drawn between the background and the person, so big words, a rising graph or a doodle sit behind their head. This needs the speaker's mask from [`/v1/video/cutout`](cutout.md): pass it as `mask_url`. Without one, the toolkit cuts the speaker out first and sends the mask back as `mask_url`, so keep it with the video for the next request.

The same mask draws outlines around the person (`outlines`): a thin hand-drawn line that redraws itself a few times a second (`sketch`, the default) or a solid ring hugging them (`sticker`). Punch-ins (`zooms`) work on any video and need no mask.

## Request

`POST /v1/video/motion` with the `X-API-Key` header.

| Field | Type | Default | Meaning |
|---|---|---|---|
| `video_url` | string (required) | | The video |
| `pieces` | array, up to 120 | `[]` | Motion pieces, see below |
| `sounds` | array, up to 400 | `[]` | Sound effects on their own: `{sound, at, volume?}` (`volume` 0 to 2) |
| `style.look` | `"bold"`, `"clean"`, `"editorial"`, `"neon"` | `"bold"` | How the pieces look |
| `style.accent` | `#rrggbb` | `#46e5b7` | Accent colour (use the brand colour) |
| `style.text` | `#rrggbb` | `#ffffff` | Text colour |
| `style.font` | font name | look's font | Any font installed on the toolkit or on Google Fonts |
| `mask_url` | string | | The speaker's mask from `/v1/video/cutout`. Only used by behind pieces and outlines |
| `outlines` | array, up to 50 | `[]` | Outlines around the person: `{start?, duration?, style?, color?, width?}`. `style` is `sketch` (default) or `sticker`. No `duration` runs to the end. `width` is a percent of the frame's short side (0.3 to 4; default 0.45 for sketch, 1.4 for sticker), `color` defaults to white |
| `zooms` | array, up to 200 | `[]` | Camera moves: `{start, duration, style?, scale?, focus_y?}`. `style`: `punch` (default, a quick push in), `through` (a push into the face that speeds up and blurs at its end, for a cut; default scale 2.4), `dolly` (the room pushes in behind the speaker while they stay the same size) or `pop` (the video shrinks to a card on a dark, blurred room and the speaker breaks out over its top edge). `dolly` and `pop` need the speaker's mask: the room behind them is painted in from one still. `scale` 1 to 2.5 (default 1.3). `focus_y` is how far down the frame to zoom into, 0 (top) to 1 (bottom), default 0.35. Moves cannot overlap |
| `sound_volume` | number, 0 to 2 | `1` | Scales every sound; `0` mutes them all |
| `piece_sounds` | boolean | `true` | Whether pieces play their own sound |
| `webhook_url` | string | | Send the answer here when done (recommended) |
| `id` | string | | Your own id, sent back |

### Pieces

Each piece is `{type, start, duration?, title?, subtitle?, image?, items?, shape?, x?, y?, layer?, sound?}`. `start` is seconds into the video. `layer` is `"front"` (default) or `"behind"`. `duration` is kept between the type's shortest and longest and inside the video. `sound` picks another sound, or `null` for none.

| `type` | `title` | `subtitle` | Length (default, shortest to longest) | Sound |
|---|---|---|---|---|
| `lower_third` | Name | Role | 4 s (2 to 10) | slide |
| `title` | Title | Small line above it | 2.8 s (1.2 to 8) | impact |
| `keyword` | One word or a few | | 1.2 s (0.5 to 4) | pop |
| `stat` | A figure such as `40%` or `$12k` (a plain whole number counts up) | What it is | 3 s (1.5 to 8) | ding |
| `chapter` | Chapter name | Small line, such as "Chapter 2" | 2.4 s (1.2 to 6) | whoosh |
| `follow` | Handle | Call to action | 4 s (2 to 10) | pop |
| `quote` | The quote | Who said it | 4.5 s (2 to 12) | thud |
| `transition` | | | 0.7 s (0.4 to 1.5) | whoosh |
| `logo` | | | 3 s (1 s to the whole video). Needs `image` (a link to the logo) | ding |
| `big_text` | A magazine-cover name across the top: tall white serif capitals (Playfair Display) sized to fill the width. `"look": "glass"` draws heavy chrome letters instead | Small line above it | 3 s (1 s to the whole video). `y` sets the top in percent; behind the speaker with a mask, it drops so the head covers the bottom of the letters | impact |
| `graph` | Pill at the end of the line, such as `10X` | | 3.5 s (2 to 10). A thin line in the accent colour with a soft glow under it, creeping then shooting up. `items`: up to 6 labels along the top (default `JAN` to `JUN`) | riser |
| `doodle` | | | 2.5 s (1 s to the whole video). `shape`: `crown`, `star`, `heart`, `arrow`, `burst` or `circle`. `x`, `y` place its centre in percent; behind the speaker with a mask and no place given, it goes just above their head | pop |
| `checklist` | Small heading | | 4 s (2 to 12). A dark glass card with a count (`1 of 3`) and a progress bar; `items`: up to 6 lines, each typed in and ticked in turn while the rest wait as grey bars | tick |

Sounds: `click`, `ding`, `impact`, `pop`, `riser`, `slide`, `thud`, `tick`, `whoosh`.

`GET /v1/video/motion/catalog` lists the piece types, looks and sounds this toolkit has.

## Example

```json
{
  "video_url": "https://example.com/talk.mp4",
  "style": {"look": "bold", "accent": "#ff2a6d"},
  "pieces": [
    {"type": "title", "start": 0.2, "title": "How we film in caves", "subtitle": "Episode 4"},
    {"type": "lower_third", "start": 3, "title": "Jeanette Epps", "subtitle": "NASA astronaut"},
    {"type": "stat", "start": 11, "title": "6 days", "subtitle": "underground"},
    {"type": "follow", "start": 28, "title": "@yourchannel", "subtitle": "More every week"}
  ],
  "sounds": [{"sound": "riser", "at": 17.6}],
  "webhook_url": "https://example.com/hook"
}
```

Behind the speaker, with a sketch outline and a punch-in:

```json
{
  "video_url": "https://example.com/talk.mp4",
  "mask_url": "https://example.com/talk_mask.mp4",
  "style": {"accent": "#ffcf3f"},
  "pieces": [
    {"type": "big_text", "start": 0.3, "title": "Takewon", "layer": "behind"},
    {"type": "doodle", "start": 3.2, "shape": "crown", "layer": "behind"},
    {"type": "graph", "start": 6, "title": "10X", "items": ["Jan", "Feb", "Mar", "Apr"], "layer": "behind"},
    {"type": "checklist", "start": 9.4, "title": "Ready to post", "items": ["Cut the pauses", "Add the captions", "Ready to post"]}
  ],
  "outlines": [{"start": 3, "duration": 3, "color": "#ffffff"}],
  "zooms": [{"start": 7.5, "duration": 1.5, "scale": 1.25}]
}
```

## Answer

```json
{"file_url": "https://.../<job>_motion.mp4", "pieces": 4, "behind": 0, "outlines": 0, "zooms": 0, "sounds": 5, "duration": 32.6, "width": 1080, "height": 1920, "seconds": 64.2}
```

When the toolkit had to cut the speaker out itself, the answer also has `mask_url`.

A request that cannot be done (nothing inside the video, a piece with no words, overlapping punch-ins, a file with no picture) answers 400 with a plain message.

Phone videos filmed on their side are turned the right way up first, so the pieces are drawn for the frame people see.

## Speed

On 4 CPUs, drawing takes roughly 3 seconds per second of graphics, plus about 10 seconds to start. Nine pieces on a 33-second tall video took about 85 seconds.

## Setup

The Docker image installs Node.js and `hyperframes@0.9.70`. HyperFrames uses Playwright's Chromium (found automatically, or set `HYPERFRAMES_BROWSER_PATH`). Fonts named in `style.font` that are not installed are fetched from Google Fonts on first use and cached.
