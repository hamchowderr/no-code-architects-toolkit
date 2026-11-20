# Other Settings

These have working defaults; most people never set them.

| Variable | Default | What it does |
| --- | --- | --- |
| `WHISPER_CACHE_DIR` | `/app/whisper_cache` | Where transcription models are kept. The image already holds the `base` model. |
| `LOOK_MODEL_DIR` | `/app/look_models` | Where `/v1/media/look`'s picture model is kept. The image already holds it. |
| `HYPERFRAMES_BIN` | `hyperframes` | The program `/v1/video/motion` draws its pieces with. |
| `MOTION_RENDER_TIMEOUT` | `1800` | Seconds `/v1/video/motion` may spend drawing. |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright's own | Where the browser used for drawing and screenshots is. |
| `GDRIVE_USER` | not set | The Google Workspace user the `/gdrive-upload` route acts as. |
| `DISABLE_CLOUD_JOB` | not set | `true` stops handing long jobs to the Cloud Run Job named by `GCP_JOB_NAME`. |
| `CLOUD_RUN_JOB`, `CLOUD_RUN_EXECUTION`, `GCP_JOB_PATH`, `GCP_JOB_PAYLOAD` | set by Cloud Run | Read when the toolkit runs as a Cloud Run Job; you don't set them. |
| `ABANDONED_CHECK_SECONDS` | `60` | How often the queue workers look for jobs a stopped worker left behind and finish them as failed. |
