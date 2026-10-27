# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

No-Code Architects Toolkit API is a Flask-based media processing API that handles audio/video conversion, transcription, translation, captioning, and cloud storage integration. It supports deployment on Docker, Google Cloud Platform, and Digital Ocean.

## Architecture

### Core Components

- **[app.py](app.py)** - Main Flask application with queue-based task processing
  - Creates task queue for async job processing
  - Provides `queue_task` decorator for route handlers
  - Supports GCP Cloud Run Jobs for long-running tasks
  - Auto-registers blueprints from `routes/` directory

- **[app_utils.py](app_utils.py)** - Core utilities
  - `validate_payload()` - JSON schema validation decorator
  - `queue_task_wrapper()` - Wraps routes for queue processing
  - `discover_and_register_blueprints()` - Auto-discovers and registers Flask blueprints
  - `log_job_status()` - Logs job status to LOCAL_STORAGE_PATH/jobs

- **[config.py](config.py)** - Environment configuration
  - Validates required environment variables per storage provider
  - Configures API_KEY, storage paths, and cloud credentials

### Request Flow

1. Request hits route in `routes/v1/{category}/{action}.py`
2. `@authenticate` decorator validates X-API-Key header
3. `@validate_payload()` validates JSON against schema
4. `@queue_task_wrapper()` determines processing path:
   - **No webhook_url**: Execute synchronously, return immediately
   - **With webhook_url**: Queue task, return 202, send webhook when done
   - **GCP_JOB_NAME set + webhook_url**: Trigger Cloud Run Job, return 202
   - **CLOUD_RUN_JOB env set**: Execute synchronously in job context

5. Route calls service function in `services/v1/{category}/{action}.py`
6. Service processes media, uploads to cloud storage, returns result
7. Route returns tuple: `(response_data, endpoint_string, status_code)`

### Job Processing Modes

**In-Process Queue** (Default)
- Single queue per worker process
- Background thread processes tasks sequentially
- MAX_QUEUE_LENGTH env var limits queue size (0 = unlimited)

**GCP Cloud Run Jobs** (Optional)
- Set GCP_JOB_NAME and GCP_JOB_LOCATION to enable
- Requires webhook_url in request payload
- Triggers Cloud Run Job with endpoint and payload as env vars
- Job executes task independently and sends webhook
- gunicorn.conf.py `when_ready` hook auto-executes job on startup when CLOUD_RUN_JOB env set

**Synchronous** (No Queue)
- Used when webhook_url not provided
- Request blocks until processing completes

### Dynamic Route Registration

Routes are auto-discovered from `routes/` directory. No manual registration needed in [app.py](app.py).

**Blueprint Convention:**
```python
from flask import Blueprint
from app_utils import validate_payload, queue_task_wrapper
from services.authentication import authenticate

v1_category_action_bp = Blueprint('v1_category_action', __name__)

@v1_category_action_bp.route('/v1/category/action', methods=['POST'])
@authenticate
@validate_payload(schema_dict)
@queue_task_wrapper(bypass_queue=False)
def action_handler(job_id, data):
    """
    Args:
        job_id (str): Unique job identifier
        data (dict): Request JSON payload

    Returns:
        Tuple[dict, str, int]: (response_data, endpoint_path, status_code)
    """
    # Implementation
    return result, "/v1/category/action", 200
```

**Decorator Order Matters:**
1. `@authenticate` - First, validates API key
2. `@validate_payload(schema)` - Second, validates request JSON
3. `@queue_task_wrapper(bypass_queue=False)` - Last, transforms function signature to accept (job_id, data)

The `queue_task_wrapper` injects `job_id` and `data` parameters, so your handler receives these instead of Flask's default arguments.

See [docs/adding_routes.md](docs/adding_routes.md) for detailed guide.

### Cloud Storage Abstraction

[services/cloud_storage.py](services/cloud_storage.py) provides unified interface:
- Detects provider from environment variables
- GCP: Requires GCP_SA_CREDENTIALS, GCP_BUCKET_NAME
- S3: Requires S3_ENDPOINT_URL, S3_ACCESS_KEY, S3_SECRET_KEY, S3_BUCKET_NAME, S3_REGION
- Digital Ocean: Extracts bucket/region from endpoint URL if not provided

Service functions call `upload_file(local_path)` which returns public URL.

### Service Layer Patterns

Services follow a consistent pattern for media processing:

1. **Download** - Use `download_file()` from services/file_management.py
2. **Process** - Use FFmpeg (via subprocess or ffmpeg-python) for media manipulation
3. **Upload** - Use `upload_file()` from services/cloud_storage.py
4. **Cleanup** - Remove temporary files from LOCAL_STORAGE_PATH
5. **Error Handling** - Clean up files in exception handlers, re-raise errors

Example service structure:
```python
def process_media(media_url, job_id=None):
    # Download
    input_file = download_file(media_url, os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_input"))

    try:
        # Process with FFmpeg
        output_file = os.path.join(LOCAL_STORAGE_PATH, f"{job_id}_output.ext")
        # ... ffmpeg processing ...

        # Return paths (route handles upload & cleanup)
        return output_file, input_file

    except Exception as e:
        # Cleanup on error
        if os.path.exists(input_file):
            os.remove(input_file)
        if 'output_file' in locals() and os.path.exists(output_file):
            os.remove(output_file)
        raise
```

Routes handle cloud upload and final cleanup after service returns.

## Development Commands

### Local Development

```bash
# Install dependencies
pip install -r requirements.txt

# Run development server (default port 8080)
python app.py

# Or use gunicorn
gunicorn --config gunicorn.conf.py app:app
```

### Docker

```bash
# Build image
docker build -t no-code-architects-toolkit .

# Run container
docker run -p 8080:8080 \
  -e API_KEY=your_key \
  -e S3_ENDPOINT_URL=... \
  -e S3_ACCESS_KEY=... \
  -e S3_SECRET_KEY=... \
  -e S3_BUCKET_NAME=... \
  -e S3_REGION=... \
  no-code-architects-toolkit

# Or use docker-compose
docker-compose up
```

### Testing

**No automated test suite** - This project uses manual testing via Postman.

Postman template: https://bit.ly/49Gkh61

Test endpoint:
```bash
curl -X POST http://localhost:8080/v1/toolkit/test \
  -H "X-API-Key: your_key"
```

When adding new features, manually test via Postman or curl before submitting PR.

## Environment Variables

**Required:**
- `API_KEY` - Authentication key for all endpoints

**Storage (choose one):**

GCP Storage:
- `GCP_SA_CREDENTIALS` - Service account JSON credentials
- `GCP_BUCKET_NAME` - GCS bucket name

S3-Compatible:
- `S3_ENDPOINT_URL` - S3 endpoint URL
- `S3_ACCESS_KEY` - Access key
- `S3_SECRET_KEY` - Secret key
- `S3_BUCKET_NAME` - Bucket name
- `S3_REGION` - Region (or "None" for some providers)

**Optional:**
- `LOCAL_STORAGE_PATH` - Temp file storage (default: /tmp)
- `MAX_QUEUE_LENGTH` - Max concurrent tasks (default: 0/unlimited)
- `GUNICORN_WORKERS` - Worker processes (default: CPU cores + 1)
- `GUNICORN_TIMEOUT` - Worker timeout seconds (default: 30)
- `GCP_JOB_NAME` - Cloud Run Job name for offloading
- `GCP_JOB_LOCATION` - Cloud Run Job region (default: us-central1)

## Key Patterns

### Route Structure
- Routes in `routes/v1/{category}/{action}.py`
- Services in `services/v1/{category}/{action}.py`
- Documentation in `docs/{category}/{action}.md`

### Return Format
All routes return: `(response_dict, endpoint_string, http_status_code)`

The `queue_task` decorator wraps this into full response with metadata:
```json
{
  "code": 200,
  "id": "user_provided_id",
  "job_id": "uuid",
  "response": {...},
  "message": "success",
  "run_time": 1.234,
  "queue_time": 0.567,
  "total_time": 1.801,
  "pid": 12345,
  "queue_id": 140123456789,
  "queue_length": 3,
  "build_number": "204"
}
```

### Job Status Tracking
Job status files written to `{LOCAL_STORAGE_PATH}/jobs/{job_id}.json`

Status values: `queued`, `running`, `done`, `failed`, `submitted`

### Webhook Pattern
When `webhook_url` provided in request:
- Task queued, immediate 202 response
- On completion, POST result to webhook_url
- Used to avoid timeouts on long-running tasks

### FFmpeg Usage
Most media processing uses FFmpeg via [ffmpeg-python](https://pypi.org/project/ffmpeg-python/) library.

Example from services:
```python
import ffmpeg

input_stream = ffmpeg.input(video_path)
output = ffmpeg.output(input_stream, output_path, **options)
ffmpeg.run(output, overwrite_output=True)
```

## Adding New Features

1. Create service function in `services/v1/{category}/{action}.py`
2. Create route in `routes/v1/{category}/{action}.py` following blueprint pattern
3. Add JSON schema validation to route
4. Service should download inputs, process, upload to cloud storage
5. Return `(result_dict, endpoint_string, status_code)` from route
6. Add documentation to `docs/{category}/{action}.md`
7. Update [README.md](README.md) with new endpoint

See [docs/adding_routes.md](docs/adding_routes.md) for full guide.

## Contributing

**Important:** Submit PRs to `build` branch, NOT main.

### Before You Start: Feature Evaluation

The project owner is very selective about contributions. Before implementing a feature, evaluate it against this framework:

| Criteria | ✅ Accept if... | ❌ Reject if... |
|----------|----------------|-----------------|
| **Mission Fit** | Replaces paid APIs, reduces costs or complexity | Adds noise or solves narrow edge cases |
| **Input Familiarity** | Uses standard names/types already in use (e.g., `file_url`, `text`) | Introduces new terms for same ideas |
| **Input Clarity** | Non-tech users would understand what to enter | Needs tech explanation or experimentation |
| **Output Usefulness** | Returns clean files, text, URLs | Returns raw data or deep nested structures |
| **Reliability** | API is stable, no retries needed, consistent | Depends on flaky APIs or fragile setup |
| **Maintenance Cost** | One-and-done, doesn't change often | Vendor changes often, breaks silently |
| **Value vs. Effort** | High impact, frequently requested | Niche, low ROI |

**What We Accept:**
- Solves common no-code challenges
- Reduces cost or replaces paid APIs/tools
- Works out-of-the-box, no setup required
- Uses existing input/output naming conventions

**What We Reject:**
- Features built for one person or edge-case
- Inconsistent input/output field names
- Requires polling, retries, or callback logic
- Needs constant maintenance or breaks frequently
- Lacks error handling or code comments
- Includes unused code, requirements, or bloat
- Adds huge packages that inflate Docker image size

**Standard Field Names** (use these, don't create new ones):
- `webhook_url` - For async webhook callbacks
- `video_url`, `audio_url`, `image_url`, `media_url`, `file_url` - For input media
- `id` - User-provided identifier (returned in response)
- `start`, `end` - For time-based operations (format: HH:MM:SS or seconds)

### Workflow

1. **Propose your feature first** - Before coding, consider opening a GitHub Discussion or issue to validate your idea fits the project's mission
2. Fork the repository
3. Fetch and checkout upstream build branch:
   ```bash
   git fetch upstream
   git checkout -b feature/your-feature-name upstream/build
   ```
4. Make changes following the patterns in this document
5. Test manually via Postman or curl
6. Push to your fork and create PR targeting `build` branch

### Branch Naming

- Features: `feature/descriptive-name`
- Bug fixes: `fix/descriptive-name`
- Docs: `docs/descriptive-name`

### Requirements

**Critical:** This repository only accepts complete, production-ready code. PRs requiring significant cleanup, completion work, or debugging by maintainers will be rejected.

- Complete, debugged code ready for review (no WIP submissions)
- Update README.md when adding new endpoints
- Add endpoint documentation to `docs/{category}/{action}.md`
- Follow existing patterns and conventions exactly
- Use clear, descriptive names and comments
- Handle errors properly, clean up temp files
- Don't change files unrelated to your feature
- Don't introduce large dependencies that bloat Docker image
- Test thoroughly via Postman/curl before submitting

### Versioning

Builds auto-increment via GitHub Actions. Don't manually edit version.py.

## Deployment Guides

- Digital Ocean App Platform: [docs/cloud-installation/do.md](docs/cloud-installation/do.md)
- Google Cloud Run: [docs/cloud-installation/gcp.md](docs/cloud-installation/gcp.md)
- General Docker: [docker-compose.md](docker-compose.md)
- Local with MinIO + n8n: [docker-compose.local.minio.n8n.md](docker-compose.local.minio.n8n.md)
