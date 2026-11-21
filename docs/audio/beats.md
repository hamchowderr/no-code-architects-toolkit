# Find the beat

`POST /v1/audio/beats` finds where the beats of a song fall, so an edit can cut on them. Plain signal processing, no AI, and nothing to download:

1. FFmpeg decodes the sound to one channel.
2. How suddenly the sound gets louder, frame by frame (spectral flux), gives an onset strength curve. The bass is counted twice, because a kick drum is often the beat.
3. The tempo is the beat length at which that curve repeats best (autocorrelation), leaning towards tempos near 120 beats a minute.
4. Beats are placed by dynamic programming (Ellis, 2007): each lands on a strong onset while the gaps stay close to the tempo.
5. Bars start on the beat in each group of four (or three) where the bass hits hardest.

A three-minute MP3 takes under a second. Works on any file with sound: a song, or a video whose sound is music.

## Request

| Field | Type | Notes |
|---|---|---|
| `media_url` | URL | The song, or a video with music. |
| `max_seconds` | number | Read at most this much, 1 to 3600 seconds. Default 600. |
| `beats_per_bar` | 3 or 4 | Beats in a bar, for `downbeats`. Default 4. |
| `min_bpm`, `max_bpm` | number | The tempo range to look in, 30 to 300. Default 50 to 210. Narrow it when a song comes back at half or double its tempo. |
| `webhook_url`, `id` | | As on every endpoint. |

```json
{ "media_url": "https://example.com/background.mp3" }
```

## Response

```json
{
  "duration": 31.2,
  "tempo": 120.04,
  "beat_seconds": 0.4998,
  "steadiness": 0.87,
  "beats_per_bar": 4,
  "beats": [0.232, 0.731, 1.232, 1.731],
  "strengths": [0.91, 0.42, 0.55, 0.40],
  "downbeats": [0.232, 2.231]
}
```

- `duration` is how much was read, in seconds.
- `tempo` is beats a minute over the whole song, and `beat_seconds` the length of one beat.
- `steadiness` is how evenly the sound repeats at that tempo, 0 to 1. Songs with a beat measured 0.3 to 0.6 (drum loops up to 0.9); speech measured below 0.3, and its "beats" are best not cut on.
- `beats` holds every beat in seconds, in order, and `strengths` how hard each one hits, 0 to 1 (1 is the hardest in the song).
- `downbeats` holds the first beat of each bar.
- Silence gives `tempo: null` and no beats. A file with no sound at all is answered with 400.
