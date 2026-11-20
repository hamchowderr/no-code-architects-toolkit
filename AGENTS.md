# Notes for AI Agents

Read [CLAUDE.md](CLAUDE.md) for how the code is laid out and how to add a route. This file covers running the toolkit.

## Facts to keep straight

- This is Chowderr's fork of the NCA Toolkit. It is free and open source (GPL-2.0). Kontifi runs on this fork only; it works on its own too, from n8n or any HTTP client.
- Run the published image, `ghcr.io/hamchowderr/no-code-architects-toolkit:sha-<commit>` (or `:latest`). Never configure a server to build it.
- The image is x86-64 only and about 6 GB.

## Setting one up

1. Make a bucket (R2, S3, Spaces, B2 or MinIO) and a key for it.
2. Fill `.env` as in [docker-compose.md](docker-compose.md): `API_KEY`, the `S3_*` settings, `APP_DOMAIN`, `SSL_EMAIL`. Every setting is in [README.md](README.md).
3. `docker compose up -d` (toolkit, Redis, HTTPS).
4. Check: `curl -H "x-api-key: $API_KEY" https://$APP_DOMAIN/v1/toolkit/test` answers `"message":"success"` and `success.txt` appears in the bucket. `GET /v1/toolkit/health` reports CPU, memory, disk and the queue.
5. Long jobs: send a `webhook_url` with the request; the toolkit answers 202 at once and posts the result when done.

## Keeping the docs true

When code reads a new environment setting, document it in README.md or `docs/` in the same change; `tests/test_docs.py` checks this.
