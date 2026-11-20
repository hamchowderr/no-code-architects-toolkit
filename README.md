
![Original Logo Symbol](https://github.com/user-attachments/assets/75173cf4-2502-4710-998b-6b81740ae1bd)

# No-Code Architects Toolkit API 

Tired of wasting thousands of dollars on API subscriptions to support all your automations? What if there was a free alternative?

The 100% FREE No-Code Architects Toolkit API processes different types of media. It is built in Python using Flask.

## What Can It Do?

The API can convert audio files. It creates transcriptions of content. It translates content between languages. It adds captions to videos. It can do very complicated media processing for content creation. The API can also manage files across multiple cloud services like Google Drive, Amazon S3, Google Cloud Storage, and Dropbox.

You can deploy this toolkit in several ways. It works with Docker. It runs on Google Cloud Platform. It functions on Digital Ocean. You can use it with any system that hosts Docker.

Easily replace services like ChatGPT Whisper, Cloud Convert, Createomate, JSON2Video, PDF(dot)co, Placid and OCodeKit.

## This Fork

This is [Chowderr's fork](https://github.com/hamchowderr/no-code-architects-toolkit) of [Stephen Pope's toolkit](https://github.com/stephengpope/no-code-architects-toolkit). It is the toolkit Kontifi runs on, and it works on its own too, from n8n or any HTTP client. What it adds:

- A prebuilt image on GitHub's registry, so no machine has to compile FFmpeg (see [Run The Prebuilt Image](#run-the-prebuilt-image)).
- A shared Redis job queue that runs jobs side by side, and faster-whisper transcription ([docs](docs/toolkit/queue_and_transcription.md)).
- Storage requests (`/v1/storage/*`), so an app in front of the toolkit needs only its address and API key.
- `/v1/toolkit/health`, `/v1/media/look`, `/v1/video/reframe` and `/v1/video/motion`, listed below.
- Jobs left running by a restart are finished as failed instead of staying "running" forever.

Pull requests for this fork go to its `main` branch.

## Hosting

The toolkit runs on a rented server, or on a machine of your own that stays on around the clock (a Mac Mini is a good example). It needs Docker, and its image takes about 6 GB of disk.

- **CPU:** 4 cores at least. FFmpeg work uses every core it is given, so more cores means faster encodes and more jobs at once.
- **Memory:** about 450 MB idle, plus about 0.5 GB for each transcription running at once with the default `base` model. 8 GB is a comfortable start.
- **Disk:** room for the image and for the files being worked on (`LOCAL_STORAGE_PATH`); finished files go to the bucket.
- **Processor type:** the published image is x86-64 (Intel and AMD) only. On an Apple Silicon Mac, Docker Desktop runs it through emulation, which is slower.

## 👥 No-Code Architects Community

Want help? Join a supportive community and get dedicated tech support.

Join the ONLY community where you learn to leverage AI automation and content to grow your business (and streamline your biz).

Who's this for?
- Coaches and consultants
- AI Automation agencies
- SMMA & Content agencies
- SaaS Startup Founders

Get courses, community, support, daily calls and more.

Join the **[No-Code Architects Community](https://www.skool.com/no-code-architects)** today!

---

## API Endpoints

Each endpoint is supported by robust payload validation and detailed API documentation to facilitate easy integration and usage.

### Audio

- **[`/v1/audio/concatenate`](docs/audio/concatenate.md)**
  - Combines multiple audio files into a single audio file.

### Code

- **[`/v1/code/execute/python`](docs/code/execute/execute_python.md)**
  - Executes Python code remotely and returns the execution results.

### FFmpeg

- **[`/v1/ffmpeg/compose`](docs/ffmpeg/ffmpeg_compose.md)**
  - Provides a flexible interface to FFmpeg for complex media processing operations.

### Image

- **[`/v1/image/convert/video`](docs/image/convert/image_to_video.md)**
  - Transforms a static image into a video with custom duration and zoom effects.

- **[`/v1/image/screenshot/webpage`](docs/image/screenshot_webpage.md)**
  - Captures screenshots of web pages using Playwright with advanced options like viewport size, device emulation, and custom HTML/CSS/JS injection.

### Media

- **[`/v1/media/convert`](docs/media/convert/media_convert.md)**
  - Converts media files from one format to another with customizable codec options.

- **[`/v1/media/convert/mp3`](docs/media/convert/media_to_mp3.md)**
  - Converts various media formats specifically to MP3 audio.

- **[`/v1/BETA/media/download`](docs/media/download.md)**
  - Downloads media content from various online sources using yt-dlp.

- **[`/v1/media/feedback`](docs/media/feedback.md)**
  - Provides a web interface for collecting and displaying feedback on media content.

- **[`/v1/media/transcribe`](docs/media/media_transcribe.md)**
  - Transcribes or translates audio/video content from a provided media URL.

- **[`/v1/media/silence`](docs/media/silence.md)**
  - Detects silence intervals in a given media file.

- **[`/v1/media/metadata`](docs/media/metadata.md)**
  - Extracts comprehensive metadata from media files including format, codecs, resolution, and bitrates.

- **[`/v1/media/look`](docs/media/look.md)** (this fork only)
  - Describes frames, pictures and words as CLIP vectors so they can be matched to each other, and finds a video's camera cuts.

### S3

- **[`/v1/s3/upload`](docs/s3/upload.md)**
  - Uploads files to Amazon S3 storage by streaming directly from a URL.

### Storage (this fork)

- **[`/v1/storage/*`](docs/storage/storage.md)**
  - Signed upload and download links, folder listings, copy and delete for the toolkit's own bucket, so apps in front of the toolkit only need its address and API key.

### Toolkit

- **[`/v1/toolkit/authenticate`](docs/toolkit/authenticate.md)**
  - Provides a simple authentication mechanism to validate API keys.

- **[`/v1/toolkit/health`](docs/toolkit/health.md)** (this fork only)
  - Reports CPU, memory, work-file disk, the job queue, the last hour's jobs and the build.

- **[`/v1/toolkit/test`](docs/toolkit/test.md)**
  - Verifies that the NCA Toolkit API is properly installed and functioning.

- **[`/v1/toolkit/job/status`](docs/toolkit/job_status.md)**
  - Retrieves the status of a specific job by its ID.

- **[`/v1/toolkit/jobs/status`](docs/toolkit/jobs_status.md)**
  - Retrieves the status of all jobs within a specified time range.

### Video

- **[`/v1/video/caption`](docs/video/caption_video.md)**
  - Adds customizable captions to videos with various styling options.

- **[`/v1/video/concatenate`](docs/video/concatenate.md)**
  - Combines multiple videos into a single continuous video file.

- **[`/v1/video/thumbnail`](docs/video/thumbnail.md)**
  - Extracts a thumbnail image from a specific timestamp in a video.

- **[`/v1/video/cut`](docs/video/cut.md)**
  - Cuts specified segments from a video file with optional encoding settings.

- **[`/v1/video/split`](docs/video/split.md)**
  - Splits a video into multiple segments based on specified start and end times.

- **[`/v1/video/trim`](docs/video/trim.md)**
  - Trims a video by keeping only the content between specified start and end times.

- **[`/v1/video/reframe`](docs/video/reframe.md)** (this fork only)
  - Turns a wide video into a tall (or square) one that follows whoever is talking.

- **[`/v1/video/motion`](docs/video/motion.md)** (this fork only)
  - Lays motion graphics (lower thirds, titles, stat cards, follow cards and more) and sound effects over a video.

---

## Run The Prebuilt Image

GitHub Actions builds this fork's image and publishes it for every commit on `main`:

```bash
docker pull ghcr.io/hamchowderr/no-code-architects-toolkit:latest
# or pin one commit, which is what a server you rely on should do:
docker pull ghcr.io/hamchowderr/no-code-architects-toolkit:sha-<commit>
```

[`docker-compose.yml`](docker-compose.yml) runs it with Redis and HTTPS; see the [Docker Compose guide](docker-compose.md). Building the image yourself (`docker build -t no-code-architects-toolkit .`) compiles FFmpeg from source and takes 20 to 40 minutes, so do it on a machine nobody else is using, never on the server that runs your apps.

## Settings

### General Environment Variables

#### `API_KEY`
- **Purpose**: Used for API authentication.
- **Requirement**: Mandatory.

---

### S3-Compatible Storage Environment Variables

#### `S3_ENDPOINT_URL`
- **Purpose**: Endpoint URL for the S3-compatible service.
- **Requirement**: Mandatory if using S3-compatible storage.

#### `S3_ACCESS_KEY`
- **Purpose**: The access key for the S3-compatible storage service.
- **Requirement**: Mandatory if using S3-compatible storage.

#### `S3_SECRET_KEY`
- **Purpose**: The secret key for the S3-compatible storage service.
- **Requirement**: Mandatory if using S3-compatible storage.

#### `S3_BUCKET_NAME`
- **Purpose**: The bucket name for the S3-compatible storage service.
- **Requirement**: Mandatory if using S3-compatible storage.

#### `S3_REGION`
- **Purpose**: The region for the S3-compatible storage service.
- **Requirement**: Mandatory if using S3-compatible storage, "None" is acceptible for some s3 providers.

#### `S3_PUBLIC_URL`
- **Purpose**: Public prefix used in returned file links instead of `S3_ENDPOINT_URL/S3_BUCKET_NAME`. Useful when uploads go to an internal endpoint (e.g. `http://minio:9000` on the same Docker network) or when the bucket is served from a custom domain (e.g. Cloudflare R2).
- **Example**: `https://files.example.com/nca-toolkit` (MinIO, bucket in the path) or `https://files.example.com` (R2 custom domain).
- **Requirement**: Optional.

#### `S3_LINK_ENDPOINT_URL`
- **Purpose**: The bucket address browsers use, when `S3_ENDPOINT_URL` is an internal one (e.g. `http://minio:9000`). Signed links from `/v1/storage/sign` are made for this address. Not needed for R2, S3 or Spaces.
- **Requirement**: Optional.

#### `S3_ADDRESSING_STYLE`
- **Purpose**: `path` or `virtual` bucket addressing. Defaults to `path` for self-hosted endpoints (MinIO) and `auto` for AWS / DigitalOcean.
- **Requirement**: Optional.

---

### Google Cloud Storage (GCP) Environment Variables

#### `GCP_SA_CREDENTIALS`
- **Purpose**: The JSON credentials for the GCP Service Account.
- **Requirement**: Mandatory if using GCP storage.

#### `GCP_BUCKET_NAME`
- **Purpose**: The name of the GCP storage bucket.
- **Requirement**: Mandatory if using GCP storage.

---

### Performance Tuning Variables

#### `REDIS_URL`
- **Purpose**: One shared job queue. Jobs with a `webhook_url` run at the same time on `QUEUE_WORKERS` worker processes (default: half the CPU cores) instead of one at a time per Gunicorn worker. The API and webhooks are unchanged.
- **Default**: not set (each Gunicorn worker keeps its own queue, as before)
- **More**: [docs/toolkit/queue_and_transcription.md](docs/toolkit/queue_and_transcription.md), which also covers the faster-whisper settings (`WHISPER_MODEL`, `WHISPER_DEVICE`, ...).

#### `MAX_QUEUE_LENGTH`
- **Purpose**: Limits the maximum number of concurrent tasks in the queue.
- **Default**: 0 (unlimited)
- **Recommendation**: Set to a value based on your server resources, e.g., 10-20 for smaller instances.

#### `GUNICORN_WORKERS`
- **Purpose**: Number of worker processes for handling requests.
- **Default**: 2
- **Recommendation**: With `REDIS_URL` set, the queue workers do the heavy work and 2 is enough. Without it, raise this to run more jobs at once.

#### `GUNICORN_TIMEOUT`
- **Purpose**: Timeout (in seconds) for a request that runs without a `webhook_url`.
- **Default**: 300
- **Recommendation**: Send a `webhook_url` for anything long instead of raising this.

#### `QUEUE_WORKERS`, `ABANDONED_CHECK_SECONDS` and the transcription settings
- See [docs/toolkit/queue_and_transcription.md](docs/toolkit/queue_and_transcription.md).

### Other Settings

These have working defaults; most people never set them.

| Variable | Default | What it does |
| --- | --- | --- |
| `WHISPER_CACHE_DIR` | `/app/whisper_cache` | Where transcription models are kept. The image already holds the `base` model. |
| `LOOK_MODEL_DIR` | `/app/look_models` | Where `/v1/media/look`'s picture model is kept. The image already holds it. |
| `HYPERFRAMES_BIN` | `hyperframes` | The program `/v1/video/motion` draws its pieces with. |
| `MOTION_RENDER_TIMEOUT` | `1800` | Seconds `/v1/video/motion` may spend drawing. |
| `PLAYWRIGHT_BROWSERS_PATH` | Playwright's own | Where the browser used for drawing and screenshots is. |
| `GDRIVE_USER` | not set | The Google Workspace user the old `/gdrive-upload` route acts as. |
| `GCP_JOB_NAME`, `GCP_JOB_LOCATION` | not set, `us-central1` | Hand long jobs to a Google Cloud Run Job. `DISABLE_CLOUD_JOB=true` turns it off again. `CLOUD_RUN_JOB`, `CLOUD_RUN_EXECUTION`, `GCP_JOB_PATH` and `GCP_JOB_PAYLOAD` are set by Cloud Run itself. |

---

### Storage Configuration

#### `LOCAL_STORAGE_PATH`
- **Purpose**: Directory for temporary file storage during processing.
- **Default**: /tmp
- **Recommendation**: Set to a path with sufficient disk space for your expected workloads.

### Notes
- Ensure all required environment variables are set based on the storage provider in use (GCP or S3-compatible). 
- Missing any required variables will result in errors during runtime.
- Performance variables can be tuned based on your workload and available resources.

### Run the Docker Container:

   ```bash
   docker run -d -p 8080:8080 \
     # Authentication (required)
     -e API_KEY=your_api_key \
     
     # Cloud storage provider (choose one)

     # s3
     #
     #-e S3_ENDPOINT_URL=https://nyc3.digitaloceanspaces.com \
     #-e S3_ACCESS_KEY=your_access_key \
     #-e S3_SECRET_KEY=your_secret_key \
     #-e S3_BUCKET_NAME=your_bucket_name \
     #-e S3_REGION=nyc3 \

     # Or

     # GCP Storage
     #
     #-e GCP_SA_CREDENTIALS='{"your":"service_account_json"}' \
     #-e GCP_BUCKET_NAME=your_gcs_bucket_name \
     
     # Local storage configuration (optional)
     -e LOCAL_STORAGE_PATH=/tmp \
     
     # Performance tuning (optional)
     -e MAX_QUEUE_LENGTH=10 \
     -e GUNICORN_WORKERS=4 \
     -e GUNICORN_TIMEOUT=300 \
     
     ghcr.io/hamchowderr/no-code-architects-toolkit:latest
   ```

---

## Installation Guides

This API can be deployed to various cloud platforms:

### Digital Ocean

The Digital Ocean App platform is pretty easy to set up and get going, but it can cost more then other cloud providers.

#### Important: Long running processes

You need to use the "webhook_url" (for any request that exceeds 1 min) in your API payload to avoid timeouts due to CloudFlair proxy timeout.

If you use the webhook_url, there is no limit to the processing length.

- [Digital Ocean App Platform Installation Guide](docs/cloud-installation/do.md) - Deploy the API on Digital Ocean App Platform

### Google Cloud RUN Platform

Sometimes difficult for people to install (especially on Google Business Workspaces), lots of detailed security exceptions.

However this is one of the cheapest options with great performance because you're only charged when the NCA Toolkit is processesing a request.

Outside of that you are not charged.

#### Imporatnt: Requests exceeding 5+ minutes can be problemactic 

GCP Run will terminate long rununing processes, which can happen when processing larger files (whether you use the webhook_url or not).

However, when your processing times are consistant lower than 5 minutes (e.g. you're only process smaller files), it works great! The performance is also great and as soon as you stop making requests you stop paying.

They also have a GPU option that might be usable for better performance (untested).

- [Google Cloud RUN Platform (GCP) Installation Guide](docs/cloud-installation/gcp.md) - Deploy the API on Google Cloud Run

### General Docker Instructions

You can use these instructions to deploy the NCA Toolkit to any linux server (on any platform)

You can more easily control performance and cost this way, but requires more technical skill to get up and running (not much though).

- [General Docker Compose Installation Guide](docker-compose.md)

### Local Development with MinIO and n8n

For local development with integrated S3-compatible storage and workflow automation:

- [Local Development with MinIO and n8n Guide](docker-compose.local.minio.n8n.md) - Complete local development environment with MinIO storage and n8n workflow automation

## Testing the API

1. Install the **[Postman Template](https://bit.ly/49Gkh61)** on your computer
2. Import the API example requests from the template
3. Configure your environment variables in Postman:
   - `base_url`: Your deployed API URL
   - `x-api-key`: Your API key configured during installation
4. Use the example requests to validate that the API is functioning correctly
5. Use the **[NCA Toolkit API GPT](https://bit.ly/4feDDk4)** to explore additional features

---

## Contributing To the NCA Toolkit API

We welcome contributions from the public! If you'd like to contribute to this project, please follow these steps:

1. Fork the repository
2. Create a new branch for your feature or bug fix
3. Make your changes
4. Submit a pull request: to `main` for this fork, or to the "build" branch of [the original](https://github.com/stephengpope/no-code-architects-toolkit)

### Pull Request Process

1. Ensure any install or build dependencies are removed before the end of the layer when doing a build.
2. Update the README.md with details of changes to the interface, this includes new environment variables, exposed ports, useful file locations and container parameters.

### Adding New Routes

If you want to add new API endpoints, check out our [Adding Routes Guide](docs/adding_routes.md) to learn how to use the dynamic route registration system.

Thank you for your contributions!

---

## How To Get Support

Get courses, community, support daily calls and more.

Join the **[No-Code Architects Community](https://www.skool.com/no-code-architects)** today!

## License

This project is licensed under the [GNU General Public License v2.0 (GPL-2.0)](LICENSE).
